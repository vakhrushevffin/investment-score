"""Normalize the historical Cboe pilot and apply the OMR quality rules."""
from collections import Counter
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import csv
import zipfile
from zoneinfo import ZoneInfo

from engine.omr_quality import classify
from engine.options_real_provider import PILOT, SOURCE, URL, sample_rows

REQUIRED = tuple('underlying_ticker contract_identifier expiration_date strike_price option_type contract_multiplier bid ask last_price volume open_interest implied_volatility underlying_price quote_timestamp open_interest_effective_date retrieval_timestamp source_identifier source_version'.split())
NY = ZoneInfo('America/New_York')
SCHEMA = 'omr.option-observation.v1'


def number(value, *, integral=False, positive=False):
    if value == '':
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError('malformed_number') from error
    if not parsed.is_finite() or parsed < 0 or (positive and parsed == 0):
        raise ValueError('number_out_of_domain')
    if integral:
        if parsed != parsed.to_integral_value() or parsed > 2**63 - 1:
            raise ValueError('invalid_count')
        return int(parsed)
    if parsed == 0:
        return '0'
    if abs(parsed.adjusted()) > 15 or abs(parsed.as_tuple().exponent) > 20:
        raise ValueError('unsupported_decimal_scale')
    fixed = format(parsed, 'f')
    return fixed.rstrip('0').rstrip('.') if '.' in fixed else fixed


def normalize(raw, received, digest):
    day = date.fromisoformat(raw['quote_date'])
    expiry = date.fromisoformat(raw['expiration'])
    kind = {'C': 'call', 'P': 'put'}.get(raw['option_type'])
    strike = number(raw['strike'], positive=True)
    if not kind or not raw['root'] or strike is None or expiry < day:
        raise ValueError('invalid_contract_key')
    # Source-defined scheduled snapshot, distinct from an event timestamp.
    nominal = datetime.combine(day, time(15,45), NY).astimezone(timezone.utc)
    if day > received.astimezone(NY).date() or nominal > received:
        raise ValueError('sample_from_future')
    bid, ask = (number(raw[name]) for name in ('bid_1545','ask_1545'))
    iv = number(raw['implied_volatility_1545'])
    base = number(raw['active_underlying_price_1545'])
    return dict(
        schema=SCHEMA, underlying_ticker=raw['underlying_symbol'],
        contract_identifier='|'.join((raw['root'], expiry.isoformat(), kind, strike)),
        expiration_date=expiry.isoformat(), strike_price=strike, option_type=kind,
        contract_multiplier=None, bid=bid, ask=ask, last_price=number(raw['close']),
        volume=number(raw['trade_volume'], integral=True),
        open_interest=number(raw['open_interest'], integral=True),
        implied_volatility=iv if iv != '0' else None,
        underlying_price=base if base != '0' else None, quote_timestamp=None,
        open_interest_effective_date=None, retrieval_timestamp=received.isoformat(),
        source_identifier=SOURCE, source_url=URL, source_version=None,
        source_sha256=digest, observation_date=day.isoformat(),
        nominal_quote_snapshot=nominal.isoformat(), delivery_code=raw['delivery_code'],
        iv_zero_unverified=iv == '0', underlying_zero_unverified=base == '0',
        price_basis='15:45 ET bid/ask and underlying; separate EOD close, volume and reported OI',
    )


def assess(rows, now, invalid, *, quote_not_before=None, expected_oi_date=None):
    today = now.astimezone(NY).date() if rows else None
    def key(row):
        return row['contract_identifier'], row['observation_date']

    counts = Counter(map(key, rows))
    for row in rows:
        row['missing_fields'] = [field for field in REQUIRED if row[field] is None]
        quote = datetime.fromisoformat(row['quote_timestamp']) if row['quote_timestamp'] else None
        oi_date = date.fromisoformat(row['open_interest_effective_date']) if row['open_interest_effective_date'] else None
        row['quote_freshness'] = ('UNVERIFIED' if quote is None or quote_not_before is None
                                  else 'INVALID' if quote > now
                                  else 'STALE' if quote < quote_not_before else 'FRESH')
        row['oi_freshness'] = ('UNVERIFIED' if oi_date is None or expected_oi_date is None
                               else 'INVALID' if oi_date > expected_oi_date
                               else 'STALE' if oi_date < expected_oi_date else 'FRESH')
        row['snapshot_recency'] = ('HISTORICAL' if date.fromisoformat(row['observation_date'])
                                   < today else 'SAME_DATE')
        checked = dict(key=row['contract_identifier'], oi=row['open_interest'],
                       iv=Decimal(row['implied_volatility']) if row['implied_volatility'] else None,
                       date=oi_date, timestamp=quote, volume=row['volume'],
                       multiplier=Decimal(row['contract_multiplier']) if row['contract_multiplier'] is not None else None)
        result, reason = classify([checked], None, expected_oi_date or today, now,
                                  quote_not_before=quote_not_before or (quote if quote and quote <= now else None),
                                  require_contract_fields=True, allow_missing_core=True)
        if counts[key(row)] > 1:
            result, reason = 'INCONSISTENT', 'duplicate_contract_and_session'
        elif row['bid'] is not None and row['ask'] is not None and Decimal(row['bid']) > Decimal(row['ask']):
            result, reason = 'INCONSISTENT', 'crossed_quote'
        elif result == 'PARTIAL' and quote is None and oi_date is None and row['contract_multiplier'] is None:
            reason = 'unknown_quote_event_time_oi_date_multiplier_and_coverage'
        row['quality_status'], row['quality_reason'] = result, reason
    return {
        'rows': len(rows) + sum(invalid.values()), 'normalized': len(rows),
        'statuses': dict(Counter(row['quality_status'] for row in rows) + Counter({'INVALID':sum(invalid.values())})),
        'reasons': dict(Counter(row['quality_reason'] for row in rows) + invalid),
        'rejects': dict(invalid),
        'field_counts': {field: sum(row[field] is not None for row in rows) for field in REQUIRED},
        'quote_freshness': dict(Counter(row['quote_freshness'] for row in rows)),
        'oi_freshness': dict(Counter(row['oi_freshness'] for row in rows)),
    }


def collect(provider, *, decode=sample_rows, convert=normalize, source=SOURCE, source_url=URL):
    response = provider.fetch()
    report = dict(source=source, source_url=source_url, availability=response.status, reason=response.reason,
                  fetched_at=response.received_at.isoformat() if response.received_at else None, symbols={})
    buckets = {ticker: [] for ticker in PILOT}
    rejects = {ticker: Counter() for ticker in PILOT}
    if response.body is not None:
        digest = sha256(response.body).hexdigest()
        try:
            for raw in decode(response.body):
                try:
                    row = convert(raw, response.received_at, digest)
                except ValueError as error:
                    rejects[raw['underlying_symbol']][str(error)] += 1
                else:
                    buckets[row['underlying_ticker']].append(row)
        except (ValueError, OSError, UnicodeError, zipfile.BadZipFile, csv.Error):
            report.update(availability='INVALID', reason='unreadable_sample')
            return report, {}, None
        report.update(availability='AVAILABLE', last_successful_retrieval=report['fetched_at'],
                      source_sha256=digest)
    for ticker, rows in buckets.items():
        summary = assess(rows, response.received_at, rejects[ticker])
        summary['availability'] = 'AVAILABLE' if rows else ('INVALID' if summary['rows'] else 'UNAVAILABLE')
        summary['dates'] = sorted({row['observation_date'] for row in rows})
        report['symbols'][ticker] = summary
    return report, buckets, response.body
