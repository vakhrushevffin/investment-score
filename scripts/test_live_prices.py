"""Read-only live quote validation. Writes reports ONLY to --output-dir."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COLS = ['date', 'ticker', 'open', 'high', 'low', 'close', 'adj_close', 'volume']


def validate_registry(path):
    reg = pd.read_csv(path, dtype={'ticker': 'string'})
    required = {'ticker', 'enabled', 'technical_enabled'}
    if not required.issubset(reg):
        raise ValueError(f'Registry missing columns: {sorted(required - set(reg))}')
    reg['ticker'] = reg['ticker'].str.strip().str.upper()
    if reg.ticker.isna().any() or reg.ticker.duplicated().any():
        raise ValueError('Invalid or duplicate ticker in registry')
    for col in ('enabled', 'technical_enabled'):
        reg[col] = reg[col].astype(str).str.lower().isin(['true', '1', 'yes'])
    stocks = reg.loc[reg.enabled & reg.technical_enabled, 'ticker'].tolist()
    if len(stocks) != 28:
        raise ValueError(f'Expected exactly 28 active technical stocks, found {len(stocks)}')
    return sorted(set(stocks + ['SPY']))


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
    x['date'] = pd.to_datetime(x['date'], utc=True, errors='coerce').dt.tz_convert(None).dt.normalize()
    for c in ('open', 'high', 'low', 'close', 'adj_close', 'volume'):
        x[c] = pd.to_numeric(x[c], errors='coerce')
    return x[COLS]


def download_one(ticker, start):
    import yfinance as yf
    # Per-ticker requests isolate failures and avoid version-dependent MultiIndex behavior.
    return normalize_history(yf.Ticker(ticker).history(start=start, auto_adjust=False, actions=False, raise_errors=True), ticker)


def run(registry, archive, output, years, min_rows, max_stale_days, fetch=download_one):
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    details = []
    try:
        symbols = validate_registry(registry)
        if not archive.is_file():
            raise FileNotFoundError(f'Baseline archive missing: {archive}')
        old = pd.read_csv(archive, usecols=['ticker', 'date', 'close', 'adj_close'], parse_dates=['date'])
        old['ticker'] = old['ticker'].astype(str).str.upper()
        old['date'] = pd.to_datetime(old['date'], errors='coerce').dt.normalize()
        if old[['ticker', 'date']].isna().any().any() or old.duplicated(['ticker', 'date']).any():
            raise ValueError('Baseline archive contains invalid/duplicate ticker-date rows')
        today = dt.date.today()
        start = (today - dt.timedelta(days=round(years * 365.25) + 30)).isoformat()
        last_expected = pd.Timestamp(today) - pd.offsets.BDay(1)
        for ticker in symbols:
            item = {'ticker': ticker, 'status': 'FAIL', 'rows': 0, 'latest_date': '',
                    'baseline_latest': '', 'overlap_rows': 0, 'median_overlap_close_change_pct': None,
                    'max_overlap_close_change_pct': None, 'new_rows': 0}
            old_t = old.loc[old.ticker.eq(ticker)].sort_values('date')
            if not old_t.empty:
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
                if (x.volume < 0).any() or x.volume.isna().any():
                    raise ValueError('Invalid volume')
                if len(x) < min_rows:
                    raise ValueError(f'Insufficient sessions: {len(x)} < {min_rows}')
                newest = x.date.max()
                item['latest_date'] = newest.date().isoformat()
                if newest.date() > today:
                    raise ValueError('Future-dated quote')
                if (today - newest.date()).days > max_stale_days:
                    raise ValueError(f'Stale quote: {newest.date()}')
                if old_t.empty:
                    raise ValueError('Ticker absent from baseline archive')
                overlap = x.merge(old_t, on=['ticker', 'date'], suffixes=('_live', '_base'))
                item['overlap_rows'] = len(overlap)
                item['new_rows'] = int((~x.date.isin(old_t.date)).sum())
                if overlap.empty:
                    raise ValueError('No overlapping dates with baseline')
                # Raw closes can be revised after corporate actions; flag large changes, do not silently accept.
                diffs = ((overlap.close_live / overlap.close_base) - 1).abs() * 100
                item['median_overlap_close_change_pct'] = round(float(diffs.median()), 5)
                item['max_overlap_close_change_pct'] = round(float(diffs.max()), 5)
                if float(diffs.median()) > 2:
                    raise ValueError(f'Large median raw-close discrepancy: {diffs.median():.2f}%')
                if newest < old_t.date.max():
                    raise ValueError('Live data ends before baseline')
                item['status'] = 'PASS'
            except Exception as exc:
                errors.append({'ticker': ticker, 'error': str(exc)[:500]})
                item['error'] = str(exc)[:500]
            details.append(item)
            print(f"{ticker}: {item['status']} ({item['rows']} rows)", flush=True)
        passed = sum(d['status'] == 'PASS' for d in details)
        summary = {'date_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'expected': 29,
                   'tested': len(details), 'passed': passed, 'failed': len(errors),
                   'baseline_archive': str(archive.relative_to(ROOT)) if archive.is_relative_to(ROOT) else archive.name,
                   'baseline_unchanged': True, 'result': 'PASS' if passed == 29 else 'FAIL',
                   'note': 'No repository data files are written; this is not a trading or ranking validation.'}
    except Exception as exc:
        errors.append({'ticker': 'SETUP', 'error': str(exc)[:500]})
        summary = {'result': 'FAIL', 'error': str(exc)[:500], 'tested': len(details), 'failed': len(errors)}
    pd.DataFrame(details, columns=['ticker','status','rows','latest_date','baseline_latest','overlap_rows','median_overlap_close_change_pct','max_overlap_close_change_pct','new_rows','error']).to_csv(output / 'ticker_checks.csv', index=False)
    pd.DataFrame(errors, columns=['ticker', 'error']).to_csv(output / 'errors.csv', index=False)
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
    args = p.parse_args()
    sys.exit(run(args.registry, args.archive, args.output_dir, args.years, args.min_rows, args.max_stale_days))

if __name__ == '__main__':
    main()
