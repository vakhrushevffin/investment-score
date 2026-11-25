"""Validate a staged price archive; preserve original CSV cells on publication."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import exchange_calendars as xc
from safe_price_update import registry_symbols, load_archive, fetch_one, check_prices, NUM, ACTION_FIELDS


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(archive, registry, report, expected_hash, now=None, fetch=fetch_one):
    require(digest(archive) == expected_hash, 'Baseline hash changed')
    (report / 'publish_daily_prices.csv.gz').unlink(missing_ok=True)
    summary = json.loads((report / 'summary.json').read_text())
    symbols = registry_symbols(registry)
    require(summary['result'] == 'PASS' and summary['checked'] == summary['passed'] == len(symbols), 'Loader did not pass')
    require(pd.read_csv(report / 'errors.csv').empty, 'Loader reported errors')
    candidate = report / 'candidate_daily_prices.csv.gz'
    old, new = load_archive(archive), load_archive(candidate)
    require(list(old.columns) == list(new.columns), 'Schema changed')
    require(np.isfinite(old[NUM]).all().all() and np.isfinite(new[NUM]).all().all(), 'Nonfinite prices')
    keys = ['ticker', 'date']
    a, b = old.set_index(keys).sort_index(), new.set_index(keys).sort_index()
    require(a.index.isin(b.index).all(), 'Historical keys lost')
    historical = b.loc[a.index]
    for col in a.columns:
        if col in NUM + ACTION_FIELDS:
            np.testing.assert_allclose(pd.to_numeric(a[col]), pd.to_numeric(historical[col]), rtol=1e-12, atol=1e-12, equal_nan=True, err_msg=col)
        else:
            require(a[col].equals(historical[col]), f'Historical field changed: {col}')
    added = b.loc[~b.index.isin(a.index)].reset_index()
    require(len(old) == summary['baseline_rows'] and len(new) == summary['candidate_rows'] and len(added) == summary['appended_rows'], 'Row counts disagree')
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    require(now.tzinfo is not None, 'UTC-aware clock required')
    latest = old[old.ticker.isin(symbols)].groupby('ticker').date.max()
    require(set(latest.index) == set(symbols) and latest.nunique() == 1, 'Baseline sessions not synchronized')
    last = latest.iloc[0]
    cal = xc.get_calendar('XNYS', start=last - pd.Timedelta(days=10), end=now.tz_convert(None).normalize()+pd.Timedelta(days=2))
    completed = cal.schedule.loc[cal.schedule['close'] + pd.Timedelta(minutes=60) <= now].index
    completed = pd.DatetimeIndex(completed).tz_localize(None)
    require(len(completed) > 0 and last in completed, 'Baseline latest is not a completed session')
    expected = set(completed[completed > last])
    require(set(added.date) == expected, 'Missing or unexpected completed sessions')
    require(set(added.ticker).issubset(symbols), 'Unexpected ticker')
    for day, group in added.groupby('date'):
        require(set(group.ticker) == set(symbols), f'Incomplete session {day}')
    if not added.empty:
        events = []
        issues = []
        def flag(ticker, date, reason):
            issues.append({'ticker': ticker, 'date': str(date), 'reason': reason})
        for row in added.itertuples():
            for col in ACTION_FIELDS:
                if col not in added:
                    flag(row.ticker, row.date, f'Missing action field {col}')
                    continue
                value = pd.to_numeric(getattr(row, col), errors='coerce')
                if not np.isfinite(value) or value < 0:
                    flag(row.ticker, row.date, f'Invalid {col}: {value}')
                elif value != 0:
                    events.append({'ticker': row.ticker, 'date': str(row.date), 'field': col, 'value': float(value)})
                    if col != 'dividends':
                        flag(row.ticker, row.date, f'{col}: manual review required')
                    elif value / row.close > 0.01:
                        flag(row.ticker, row.date, 'Dividend exceeds 1% of close: manual review required')
        for ticker in symbols:
            try:
                live = fetch(ticker, (last - pd.Timedelta(days=14)).date().isoformat())
                check_prices(live, ticker, [ticker])
                require(np.isfinite(live[NUM]).all().all(), 'Nonfinite live prices')
                reference = new[(new.ticker == ticker) & (new.date >= last - pd.Timedelta(days=14))]
                merged = reference.merge(live, on=keys, suffixes=('_stored','_live'))
                require(len(merged) == len(reference) and len(merged) >= 3, 'Incomplete live overlap')
                for col in ['close','adj_close']:
                    if not np.allclose(merged[col+'_stored'], merged[col+'_live'], rtol=1e-5, atol=1e-5):
                        flag(ticker, '', f'{col} revision: manual review required')
                fresh = merged[merged.date > last]
                for col in ACTION_FIELDS:
                    require(col in reference, f'Missing stored {col}')
                    if col not in live:
                        # Missing event columns are not independent evidence of zero.
                        flag(ticker, '', f'Missing live {col}')
                        continue
                    x = pd.to_numeric(fresh[col+'_stored'], errors='coerce')
                    y = pd.to_numeric(fresh[col+'_live'], errors='coerce')
                    if not np.isfinite(y).all() or not np.allclose(x,y,rtol=1e-8,atol=1e-8):
                        flag(ticker, '', f'{col} mismatch on new sessions')
            except Exception as exc:
                flag(ticker, '', str(exc))
        (report / 'corporate_actions.json').write_text(json.dumps({'events':events,'issues':issues,'policy':'append-only; dividends <=1% of close; repeated provider check, not issuer confirmation'},indent=2))
        require(not issues, f'Corporate action / overlap review required: {len(issues)} issues; see corporate_actions.json')
    require(digest(archive) == expected_hash, 'Baseline changed during validation')
    if len(added):
        # Preserve original CSV values exactly, including blanks and precision.
        target = report / 'publish_daily_prices.csv.gz'
        with gzip.open(archive,'rt',newline='') as src, gzip.open(candidate,'rt',newline='') as cand, gzip.open(target,'wt',newline='') as out:
            reader, fresh = csv.DictReader(src), csv.DictReader(cand)
            writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerows(reader)
            wanted = {(r.ticker, r.date.date().isoformat()) for r in added.itertuples()}
            count = 0
            for row in fresh:
                if (row['ticker'],row['date']) in wanted:
                    writer.writerow(row); count += 1
            require(count == len(added), 'Output append count mismatch')
        require(len(load_archive(target)) == len(new), 'Final archive row count mismatch')
    return {'result':'PASS','appended_rows':len(added),'baseline_rows':len(old),'candidate_rows':len(new),'latest_completed':str(completed[-1].date())}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--registry',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--expected-hash',required=True)
    args=p.parse_args()
    try:
        result=validate(args.archive,args.registry,args.report,args.expected_hash)
    except Exception as exc:
        result={'result':'FAIL','error':str(exc)}
    (args.report/'publication_summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    return 0 if result['result']=='PASS' else 1

if __name__=='__main__':
    raise SystemExit(main())
