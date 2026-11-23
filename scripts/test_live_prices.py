"""Read-only live quote validation; reports are written only to --output-dir."""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COLS = ['date', 'ticker', 'open', 'high', 'low', 'close', 'adj_close', 'volume']
DETAIL_COLS = ['ticker', 'status', 'rows', 'first_date', 'latest_date',
               'baseline_first', 'baseline_latest', 'baseline_rows', 'overlap_rows',
               'only_live_rows', 'only_baseline_rows', 'newer_than_baseline_rows',
               'median_overlap_close_change_pct', 'max_overlap_close_change_pct', 'error']
DATE_COLS = ['ticker', 'source', 'date', 'classification']


def validate_registry(path):
    reg = pd.read_csv(path, dtype={'ticker': 'string'})
    required = {'ticker', 'enabled', 'technical_enabled'}
    if not required.issubset(reg.columns):
        raise ValueError(f'Registry missing columns: {sorted(required - set(reg.columns))}')
    reg['ticker'] = reg['ticker'].str.strip().str.upper()
    if reg.ticker.isna().any() or reg.ticker.eq('').any() or reg.ticker.duplicated().any():
        raise ValueError('Invalid or duplicate ticker in registry')
    for col in ('enabled', 'technical_enabled'):
        reg[col] = reg[col].astype(str).str.lower().isin(['true', '1', 'yes'])
    stocks = reg.loc[reg.enabled & reg.technical_enabled, 'ticker'].tolist()
    if len(stocks) != 28 or 'SPY' in stocks:
        raise ValueError(f'Expected exactly 28 active technical stocks excluding SPY, found {len(stocks)}')
    return sorted(stocks + ['SPY'])


def normalize_history(hist, ticker):
    if hist is None or hist.empty:
        raise ValueError('Empty Yahoo Finance response')
    x = hist.copy()
    x.columns = [str(c).strip().lower().replace(' ', '_') for c in x.columns]
    x = x.reset_index()
    x.columns = [str(c).strip().lower().replace(' ', '_') for c in x.columns]
    if 'date' not in x.columns and 'datetime' in x.columns:
        x = x.rename(columns={'datetime': 'date'})
    missing = set(COLS) - set(x.columns) - {'ticker'}
    if missing:
        raise ValueError(f'Missing price fields: {sorted(missing)}')
    x['ticker'] = ticker
    # Convert market-local trading dates without shifting them to another UTC day.
    x['date'] = pd.to_datetime(x['date'], errors='coerce').dt.tz_localize(None).dt.normalize()
    for c in ('open', 'high', 'low', 'close', 'adj_close', 'volume'):
        x[c] = pd.to_numeric(x[c], errors='coerce')
    return x[COLS]


def download_one(ticker, start):
    import yfinance as yf
    return normalize_history(yf.Ticker(ticker).history(start=start, auto_adjust=False, actions=False), ticker)


def run(registry, archive, output, years, min_rows, max_stale_days, fetch=download_one):
    output.mkdir(parents=True, exist_ok=True)
    errors, details, date_differences = [], [], []
    try:
        symbols = validate_registry(registry)
        if not archive.is_file():
            raise FileNotFoundError(f'Baseline archive missing: {archive}')
        old = pd.read_csv(archive, usecols=['ticker', 'date', 'close', 'adj_close'])
        old['ticker'] = old['ticker'].astype(str).str.strip().str.upper()
        old['date'] = pd.to_datetime(old['date'], errors='coerce').dt.normalize()
        for c in ('close', 'adj_close'):
            old[c] = pd.to_numeric(old[c], errors='coerce')
        if old[['ticker', 'date']].isna().any().any() or old.duplicated(['ticker', 'date']).any():
            raise ValueError('Baseline contains invalid/duplicate ticker-date rows')
        today = dt.datetime.now(dt.timezone.utc).date()
        start = (today - dt.timedelta(days=round(years * 365.25) + 30)).isoformat()
        for ticker in symbols:
            item = dict.fromkeys(DETAIL_COLS, None)
            item.update(ticker=ticker, status='FAIL', rows=0, overlap_rows=0,
                        only_live_rows=0, only_baseline_rows=0, newer_than_baseline_rows=0)
            old_t = old.loc[old.ticker.eq(ticker)].sort_values('date')
            item['baseline_rows'] = len(old_t)
            if not old_t.empty:
                item['baseline_first'] = old_t.date.min().date().isoformat()
                item['baseline_latest'] = old_t.date.max().date().isoformat()
            try:
                x = fetch(ticker, start)
                item['rows'] = len(x)
                if x.empty:
                    raise ValueError('No rows')
                if x.date.isna().any() or x.duplicated(['ticker', 'date']).any():
                    raise ValueError('Invalid dates or duplicate ticker-date keys')
                if x[['open', 'high', 'low', 'close', 'adj_close']].isna().any().any():
                    raise ValueError('Missing OHLC/adjusted prices')
                if (x[['open', 'high', 'low', 'close', 'adj_close']] <= 0).any().any():
                    raise ValueError('Nonpositive prices')
                if (x.high < x.low).any() or (x.high < x[['open', 'close']].max(axis=1)).any() or (x.low > x[['open', 'close']].min(axis=1)).any():
                    raise ValueError('Invalid OHLC relationship')
                if x.volume.isna().any() or (x.volume < 0).any():
                    raise ValueError('Invalid volume')
                if len(x) < min_rows:
                    raise ValueError(f'Insufficient sessions: {len(x)} < {min_rows}')
                first, newest = x.date.min(), x.date.max()
                item['first_date'] = first.date().isoformat()
                item['latest_date'] = newest.date().isoformat()
                if newest.date() > today:
                    raise ValueError('Future-dated quote')
                if (today - newest.date()).days > max_stale_days:
                    raise ValueError(f'Stale quote: {newest.date()}')
                if old_t.empty:
                    raise ValueError('Ticker absent from baseline archive')
                # Compare only dates covered by the live download. An older baseline
                # may legitimately contain years before the requested download start.
                old_window = old_t.loc[old_t.date.ge(first) & old_t.date.le(newest)]
                live_only = sorted(set(x.date) - set(old_t.date))
                baseline_only = sorted(set(old_window.date) - set(x.date))
                item['only_live_rows'] = len(live_only)
                item['only_baseline_rows'] = len(baseline_only)
                item['newer_than_baseline_rows'] = sum(d > old_t.date.max() for d in live_only)
                for d in live_only:
                    kind = 'after_baseline_end' if d > old_t.date.max() else ('before_baseline_start' if d < old_t.date.min() else 'inside_baseline_range')
                    date_differences.append(dict(ticker=ticker, source='live_only', date=d.date().isoformat(), classification=kind))
                for d in baseline_only:
                    date_differences.append(dict(ticker=ticker, source='baseline_only', date=d.date().isoformat(), classification='missing_in_live_within_download_range'))
                overlap = x.merge(old_t, on=['ticker', 'date'], suffixes=('_live', '_base'))
                item['overlap_rows'] = len(overlap)
                if overlap.empty:
                    raise ValueError('No overlapping dates with baseline')
                if (overlap.close_base <= 0).any() or overlap.close_base.isna().any():
                    raise ValueError('Invalid baseline close in overlap')
                diffs = ((overlap.close_live / overlap.close_base) - 1).abs() * 100
                item['median_overlap_close_change_pct'] = round(float(diffs.median()), 5)
                item['max_overlap_close_change_pct'] = round(float(diffs.max()), 5)
                if float(diffs.median()) > 2:
                    raise ValueError(f'Large median raw-close discrepancy: {diffs.median():.2f}%')
                if newest < old_t.date.max():
                    raise ValueError('Live data ends before baseline')
                if baseline_only:
                    raise ValueError(f'{len(baseline_only)} baseline dates missing in live download window; see date_differences.csv')
                item['status'] = 'PASS'
            except Exception as exc:
                item['error'] = str(exc)[:500]
                errors.append({'ticker': ticker, 'error': item['error']})
            details.append(item)
            print(f"{ticker}: {item['status']} ({item['rows']} rows; live-only={item['only_live_rows']}; baseline-only={item['only_baseline_rows']})", flush=True)
        passed = sum(d['status'] == 'PASS' for d in details)
        summary = {'date_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                   'expected': 29, 'tested': len(details), 'passed': passed,
                   'failed': len(errors), 'only_live_dates': sum(d['only_live_rows'] or 0 for d in details),
                   'missing_baseline_dates_in_download_window': sum(d['only_baseline_rows'] or 0 for d in details),
                   'baseline_archive': str(archive), 'baseline_unchanged': True,
                   'result': 'PASS' if passed == 29 else 'FAIL',
                   'note': 'Read-only diagnostic; no repository data files written. Does not prove exchange-calendar completeness or validate ranking/trading.'}
    except Exception as exc:
        errors.append({'ticker': 'SETUP', 'error': str(exc)[:500]})
        summary = {'result': 'FAIL', 'error': str(exc)[:500], 'tested': len(details), 'failed': len(errors)}
    pd.DataFrame(details, columns=DETAIL_COLS).to_csv(output / 'ticker_checks.csv', index=False)
    pd.DataFrame(errors, columns=['ticker', 'error']).to_csv(output / 'errors.csv', index=False)
    pd.DataFrame(date_differences, columns=DATE_COLS).to_csv(output / 'date_differences.csv', index=False)
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary['result'] == 'PASS' else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--registry', type=Path, default=ROOT / 'data/tickers.csv')
    p.add_argument('--archive', type=Path, default=ROOT / 'data/daily_prices.csv.gz')
    p.add_argument('--output-dir', type=Path, default=Path('/tmp/investment-score-live-prices-report'))
    p.add_argument('--years', type=int, default=12)
    p.add_argument('--min-rows', type=int, default=200)
    p.add_argument('--max-stale-days', type=int, default=7)
    a = p.parse_args()
    sys.exit(run(a.registry, a.archive, a.output_dir, a.years, a.min_rows, a.max_stale_days))


if __name__ == '__main__':
    main()
