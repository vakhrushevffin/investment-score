"""Safe append-only daily-price update. Never writes the repository baseline."""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import numpy as np
from reconcile_prices import normalize, reconcile, same, NUM as RECON_NUM

ROOT = Path(__file__).resolve().parents[1]
COLS = ['date', 'ticker', 'open', 'high', 'low', 'close', 'adj_close', 'volume']
NUM = ['open', 'high', 'low', 'close', 'adj_close', 'volume']
ACTION_FIELDS = ['dividends', 'splits', 'capital_gains']


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
    if not np.isfinite(df[NUM]).all().all():
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
    if len(old.columns) != len(set(old.columns)):
        raise ValueError('Archive contains duplicate column names')
    missing = set(COLS) - set(old.columns)
    if missing:
        raise ValueError(f'Archive missing required columns: {sorted(missing)}')
    # Preserve the exact existing schema and column order.
    old = old.copy()
    old['date'] = dates(old.date)
    old['ticker'] = old.ticker.astype('string').str.strip().str.upper()
    for c in NUM:
        old[c] = pd.to_numeric(old[c], errors='coerce')
    check_prices(old, 'Baseline')
    return old


def fetch_one(ticker, start):
    import yfinance as yf
    raw = yf.Ticker(ticker).history(start=start, auto_adjust=False, actions=True)
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
    # Keep available corporate-action columns for candidate schema alignment.
    return normalize(x)


def run(registry, archive, output, lookback_days, max_stale_days, fetch=fetch_one):
    output.mkdir(parents=True, exist_ok=True)
    (output / 'candidate_daily_prices.csv.gz').unlink(missing_ok=True)
    summary = {'result': 'FAIL', 'mode': 'DRY_RUN', 'baseline_unchanged': True}
    checks, errors, volume_notes = [], [], []
    try:
        symbols = registry_symbols(registry)
        old = load_archive(archive)
        missing = set(symbols) - set(old.ticker)
        if missing:
            raise ValueError(f'Missing baseline symbols: {sorted(missing)}')
        today = dt.datetime.now(dt.timezone.utc).date()
        # Download overlapping recent history, including holidays/weekends.
        start = min(today - dt.timedelta(days=lookback_days), old.date.max().date() - dt.timedelta(days=14)).isoformat()
        additions = []
        archive_columns = list(old.columns)
        for ticker in symbols:
            old_t = old.loc[old.ticker.eq(ticker)]
            row = {'ticker': ticker, 'status': 'FAIL', 'appended_rows': 0}
            try:
                live = normalize(fetch(ticker, start))
                check_prices(live, ticker, [ticker])
                newest = live.date.max()
                if newest.date() > today or (today-newest.date()).days > max_stale_days:
                    raise ValueError('Future/stale session')
                overlap = old_t.merge(live,on=['ticker','date'],suffixes=('_base','_live'))
                if len(overlap)<3 or newest<old_t.date.max(): raise ValueError('Insufficient overlap')
                expected = old_t[old_t.date>=live.date.min()]
                if set(expected.date)!=set(live.loc[live.date<=old_t.date.max(),'date']): raise ValueError('Historical session mismatch')
                fresh=live[live.date>old_t.date.max()]
                event=(fresh[ACTION_FIELDS]!=0).any().any()
                for d,base_v,live_v in zip(overlap.date,overlap.volume_base,overlap.volume_live):
                    if base_v!=live_v: volume_notes.append({'ticker':ticker,'date':str(d.date()),'baseline_volume':float(base_v),'provider_volume':float(live_v)})
                changed=any(not same(overlap[c+'_base'],overlap[c+'_live'],rtol=5e-5 if c=='adj_close' else 2e-5) for c in NUM if c!='volume')
                if event or changed:
                    full=normalize(fetch(ticker,old_t.date.min().date().isoformat()))
                    check_prices(full,ticker,[ticker])
                    result=reconcile(old_t,full)
                else:
                    result=pd.concat([old_t,fresh[archive_columns]],ignore_index=True)
                additions.append(result)
                row.update(status='PASS',appended_rows=len(result)-len(old_t))
            except Exception as exc:
                errors.append({'ticker':ticker,'error':str(exc)})
                row['error']=str(exc)
            checks.append(row)
        if errors:
            raise ValueError(f'{len(errors)} ticker checks failed; no candidate archive produced')
        candidate = pd.concat([old.loc[~old.ticker.isin(symbols)], *additions], ignore_index=True)
        candidate = candidate.sort_values(['ticker', 'date']).reset_index(drop=True)
        if list(candidate.columns) != archive_columns:
            raise ValueError('Candidate archive schema/order differs from baseline')
        # Legacy archives may contain blank corporate-action cells. Preserve them
        # exactly; require valid values only in newly appended rows.
        for field in ACTION_FIELDS:
            if field in candidate.columns:
                original = candidate[field]
                numeric = pd.to_numeric(original, errors='coerce')
                unexpected = original.notna() & numeric.isna()
                if unexpected.any():
                    raise ValueError(f'Non-numeric {field} in candidate')
                candidate[field] = numeric
        check_prices(candidate, 'Candidate')
        if len(candidate) != len(old) + sum(r['appended_rows'] for r in checks):
            raise ValueError('Unexpected candidate row count')
        # Candidate written only into Actions artifact directory, never into data/.
        target = output / 'candidate_daily_prices.csv.gz'
        candidate.to_csv(target, index=False, compression='gzip', date_format='%Y-%m-%d')
        summary.update(result='PASS', checked=len(symbols), passed=len(symbols),
                       appended_rows=sum(r['appended_rows'] for r in checks), baseline_rows=len(old),
                       candidate_rows=len(candidate), candidate_file=target.name,
                       note='Candidate artifact only; no commit or production update.')
    except Exception as exc:
        summary['error'] = str(exc)[:500]
    pd.DataFrame(volume_notes,columns=['ticker','date','baseline_volume','provider_volume']).to_csv(output/'volume_revisions.csv',index=False)
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
