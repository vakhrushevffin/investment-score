"""Safe append-only daily-price update. Never writes the repository baseline."""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COLS = ['date', 'ticker', 'open', 'high', 'low', 'close', 'adj_close', 'volume']
NUM = ['open', 'high', 'low', 'close', 'adj_close', 'volume']


def registry_symbols(path):
    r = pd.read_csv(path, dtype={'ticker': 'string'})
    required = {'ticker', 'enabled', 'technical_enabled'}
    if not required.issubset(r.columns):
        raise ValueError(f'Registry missing: {sorted(required - set(r.columns))}')
    r['ticker'] = r.ticker.str.strip().str.upper()
    if r.ticker.isna().any() or r.ticker.eq('').any() or r.ticker.duplicated().any():
        raise ValueError('Invalid/duplicate ticker in registry')
    for c in ('enabled', 'technical_enabled'):
        r[c] = r[c].astype(str).str.strip().str.lower().isin(('true', '1', 'yes'))
    stocks = r.loc[r.enabled & r.technical_enabled, 'ticker'].tolist()
    if len(stocks) != 28 or 'SPY' in stocks:
        raise ValueError(f'Expected 28 stocks excluding SPY; found {len(stocks)}')
    return sorted(stocks + ['SPY'])


def dates(s):
    d = pd.to_datetime(s, errors='coerce', utc=True)
    return d.dt.tz_localize(None).dt.normalize()


def check_prices(df, label, symbols=None):
    if df.empty:
        raise ValueError(f'{label}: empty dataset')
    if df[['date', 'ticker']].isna().any().any() or df.duplicated(['ticker', 'date']).any():
        raise ValueError(f'{label}: invalid/duplicate ticker-date')
    if df[NUM].isna().any().any():
        raise ValueError(f'{label}: missing numeric fields')
    if (df[NUM[:-1]] <= 0).any().any() or (df.volume < 0).any():
        raise ValueError(f'{label}: invalid price/volume')
    if ((df.high < df.low) | (df.high < df[['open', 'close']].max(axis=1)) |
            (df.low > df[['open', 'close']].min(axis=1))).any():
        raise ValueError(f'{label}: invalid OHLC relationship')
    if symbols is not None and set(df.ticker) != set(symbols):
        raise ValueError(f'{label}: ticker coverage mismatch')


def load_archive(path):
    if not path.is_file():
        raise FileNotFoundError(f'Archive missing: {path}')
    old = pd.read_csv(path)
    if set(old.columns) != set(COLS):
        raise ValueError(f'Archive schema mismatch; expected {COLS}, got {list(old.columns)}')
    old = old[COLS].copy()
    old['date'] = dates(old.date)
    old['ticker'] = old.ticker.astype('string').str.strip().str.upper()
    for c in NUM:
        old[c] = pd.to_numeric(old[c], errors='coerce')
    check_prices(old, 'Baseline')
    return old


def fetch_one(ticker, start):
    import yfinance as yf
    raw = yf.Ticker(ticker).history(start=start, auto_adjust=False, actions=False)
    if raw is None or raw.empty:
        raise ValueError('Empty Yahoo response')
    x = raw.reset_index()
    x.columns = [str(c).strip().lower().replace(' ', '_') for c in x.columns]
    if 'date' not in x and 'datetime' in x:
        x = x.rename(columns={'datetime': 'date'})
    needed = set(COLS) - {'ticker'}
    if not needed.issubset(x.columns):
        raise ValueError(f'Missing Yahoo fields: {sorted(needed - set(x.columns))}')
    x['ticker'] = ticker
    # Keep exchange-local session dates; don't convert them to UTC first.
    x['date'] = pd.to_datetime(x.date, errors='coerce').dt.tz_localize(None).dt.normalize()
    for c in NUM:
        x[c] = pd.to_numeric(x[c], errors='coerce')
    return x[COLS]


def run(registry, archive, output, lookback_days, max_stale_days, fetch=fetch_one):
    output.mkdir(parents=True, exist_ok=True)
    summary = {'result': 'FAIL', 'mode': 'DRY_RUN', 'baseline_unchanged': True}
    checks, errors = [], []
    try:
        symbols = registry_symbols(registry)
        old = load_archive(archive)
        missing = set(symbols) - set(old.ticker)
        if missing:
            raise ValueError(f'Missing baseline symbols: {sorted(missing)}')
        today = dt.datetime.now(dt.timezone.utc).date()
        # Download overlapping recent history, including holidays/weekends.
        start = (today - dt.timedelta(days=lookback_days)).isoformat()
        additions = []
        for ticker in symbols:
            old_t = old.loc[old.ticker.eq(ticker)]
            row = {'ticker': ticker, 'status': 'FAIL', 'baseline_latest': old_t.date.max().date().isoformat(),
                   'live_latest': '', 'overlap_rows': 0, 'appended_rows': 0, 'max_overlap_close_change_pct': None}
            try:
                live = fetch(ticker, start)
                check_prices(live, ticker, [ticker])
                newest = live.date.max()
                row['live_latest'] = newest.date().isoformat()
                if newest.date() > today or (today - newest.date()).days > max_stale_days:
                    raise ValueError(f'Future/stale latest session: {newest.date()}')
                if newest < old_t.date.max():
                    raise ValueError('Live download ends before baseline')
                overlap = live.merge(old_t, on=['ticker', 'date'], suffixes=('_live', '_base'))
                row['overlap_rows'] = len(overlap)
                if len(overlap) < 3:
                    raise ValueError(f'Insufficient overlapping sessions: {len(overlap)} < 3')
                # A large revision requires manual investigation; never overwrite historical rows.
                close_change = ((overlap.close_live / overlap.close_base) - 1).abs() * 100
                row['max_overlap_close_change_pct'] = round(float(close_change.max()), 6)
                if close_change.max() > 2.0:
                    raise ValueError(f'Raw close revision exceeds 2%: {close_change.max():.3f}%')
                missing_old_dates = set(old_t.loc[old_t.date.ge(live.date.min()) & old_t.date.le(newest)].date) - set(live.date)
                if missing_old_dates:
                    raise ValueError(f'{len(missing_old_dates)} baseline dates absent from live download')
                # Reject unexplained gaps inside the existing baseline range.
                historical_live_only = live.loc[live.date.le(old_t.date.max()) & ~live.date.isin(old_t.date)]
                if not historical_live_only.empty:
                    raise ValueError(f'{len(historical_live_only)} unexpected historical dates; manual review required')
                new = live.loc[live.date.gt(old_t.date.max())].copy()
                row['appended_rows'] = len(new)
                additions.append(new)
                row['status'] = 'PASS'
            except Exception as exc:
                errors.append({'ticker': ticker, 'error': str(exc)[:500]})
                row['error'] = str(exc)[:500]
            checks.append(row)
            print(f"{ticker}: {row['status']}; appended={row['appended_rows']}", flush=True)
        if errors:
            raise ValueError(f'{len(errors)} ticker checks failed; no candidate archive produced')
        candidate = pd.concat([old, *additions], ignore_index=True)
        candidate = candidate.sort_values(['ticker', 'date']).reset_index(drop=True)
        check_prices(candidate, 'Candidate')
        if len(candidate) != len(old) + sum(len(a) for a in additions):
            raise ValueError('Unexpected candidate row count')
        # Candidate written only into Actions artifact directory, never into data/.
        target = output / 'candidate_daily_prices.csv.gz'
        candidate.to_csv(target, index=False, compression='gzip', date_format='%Y-%m-%d')
        summary.update(result='PASS', checked=len(symbols), passed=len(symbols),
                       appended_rows=sum(len(a) for a in additions), baseline_rows=len(old),
                       candidate_rows=len(candidate), candidate_file=target.name,
                       note='Candidate artifact only; no commit or production update.')
    except Exception as exc:
        summary['error'] = str(exc)[:500]
    pd.DataFrame(checks).to_csv(output / 'ticker_checks.csv', index=False)
    pd.DataFrame(errors, columns=['ticker', 'error']).to_csv(output / 'errors.csv', index=False)
    summary['date_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    (output / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return 0 if summary['result'] == 'PASS' else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--registry', type=Path, default=ROOT / 'data/tickers.csv')
    p.add_argument('--archive', type=Path, default=ROOT / 'data/daily_prices.csv.gz')
    p.add_argument('--output-dir', type=Path, default=Path('/tmp/investment-score-safe-update'))
    p.add_argument('--lookback-days', type=int, default=45)
    p.add_argument('--max-stale-days', type=int, default=7)
    a = p.parse_args()
    if a.lookback_days < 14 or a.max_stale_days < 1:
        p.error('lookback-days must be >=14; max-stale-days must be >=1')
    sys.exit(run(a.registry, a.archive, a.output_dir, a.lookback_days, a.max_stale_days))


if __name__ == '__main__':
    main()
