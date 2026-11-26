"""Validate a staged price archive; preserve original CSV cells on publication."""
import argparse
import shutil
from reconcile_prices import normalize, reconcile, same
import csv
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import exchange_calendars as xc
from safe_price_update import registry_symbols, archive_coverage, load_archive, fetch_one, check_prices, NUM, ACTION_FIELDS


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compare_reconciled(verified, proposed, ticker, diagnostics):
    x=verified.sort_values('date').reset_index(drop=True)
    y=proposed.sort_values('date').reset_index(drop=True)
    # Only independently reconciled price values receive provider precision tolerance.
    pd.testing.assert_frame_equal(x.drop(columns=['adj_close']),y.drop(columns=['adj_close']),check_dtype=False,rtol=1e-8,atol=1e-8)
    a=x.adj_close.to_numpy(); b=y.adj_close.to_numpy()
    delta=np.abs(a-b); relative=delta/np.maximum(np.abs(a),np.abs(b))
    ok=bool(np.isfinite(delta).all() and np.all(delta<=2e-5+5e-5*np.minimum(np.abs(a),np.abs(b))))
    diagnostics.append({'ticker':ticker,'rows':len(a),'max_abs':float(delta.max()),'max_relative':float(relative.max()),'pass':ok})
    require(ok,f'{ticker}: reconciled adj_close mismatch; max absolute={delta.max():.9g}, max relative={relative.max():.9g}')


def validate(archive, registry, report, expected_hash, now=None, fetch=fetch_one):
    require(digest(archive) == expected_hash, 'Baseline hash changed')
    (report / 'publish_daily_prices.csv.gz').unlink(missing_ok=True)
    summary = json.loads((report / 'summary.json').read_text())
    candidate = report / 'candidate_daily_prices.csv.gz'
    old, new = load_archive(archive), load_archive(candidate)
    symbols, pending = archive_coverage(registry_symbols(registry), old)
    require(summary.get('pending_bootstrap', []) == pending, 'Registry coverage changed')
    require(summary['result'] == 'PASS' and summary['checked'] == summary['passed'] == len(symbols), 'Loader did not pass')
    require(pd.read_csv(report / 'errors.csv').empty, 'Loader reported errors')
    require(list(old.columns) == list(new.columns), 'Schema changed')
    require(np.isfinite(old[NUM]).all().all() and np.isfinite(new[NUM]).all().all(), 'Nonfinite prices')
    keys = ['ticker', 'date']
    a, b = old.set_index(keys).sort_index(), new.set_index(keys).sort_index()
    require(a.index.isin(b.index).all(), 'Historical keys lost')
    historical = b.loc[a.index]
    added = b.loc[~b.index.isin(a.index)].reset_index()
    require(len(old) == summary['baseline_rows'] and len(new) == summary['candidate_rows'] and len(added) == summary['appended_rows'], 'Row counts disagree')
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    require(now.tzinfo is not None, 'UTC-aware clock required')
    latest = old[old.ticker.isin(symbols)].groupby('ticker').date.max()
    require(set(latest.index) == set(symbols) and latest.nunique() == 1, 'Baseline sessions not synchronized')
    last = latest.iloc[0]
    cal = xc.get_calendar('XNYS', start=last - pd.Timedelta(days=10), end=now.tz_convert(None).normalize()+pd.Timedelta(days=2))
    completed = cal.schedule.loc[pd.to_datetime(cal.schedule['close' if 'close' in cal.schedule else 'market_close'],utc=True) + pd.Timedelta(minutes=60) <= now].index
    completed = pd.DatetimeIndex(completed).tz_localize(None)
    require(len(completed) > 0 and last in completed, 'Baseline latest is not a completed session')
    expected = set(completed[completed > last])
    require(set(added.date) == expected, 'Missing or unexpected completed sessions')
    require(set(added.ticker).issubset(symbols), 'Unexpected ticker')
    for day, group in added.groupby('date'):
        require(set(group.ticker) == set(symbols), f'Incomplete session {day}')
    diagnostics=[]
    (report/'precision_checks.csv').unlink(missing_ok=True)
    events=[]
    revised=0
    for ticker in symbols:
        base=old[old.ticker==ticker]
        proposed=new[new.ticker==ticker]
        live=normalize(fetch(ticker,(last-pd.Timedelta(days=14)).date().isoformat()))
        check_prices(live,ticker,[ticker])
        ref=proposed[proposed.date>=live.date.min()]
        require(set(ref.date)==set(live.date),'Live session mismatch')
        merged=ref.merge(live,on=keys,suffixes=('_stored','_live'))
        require(len(merged)>=3,'Insufficient live overlap')
        for c in NUM:
            # Historical volume is verified against baseline or split reconciliation below.
            m=merged.loc[merged.date>last] if c=='volume' else merged
            require(same(m[c+'_stored'],m[c+'_live'],rtol=5e-5 if c=='adj_close' else 2e-5),f'{ticker}: live mismatch {c}')
        fresh=live[live.date>last]
        for c in ACTION_FIELDS:
            require(same(merged.loc[merged.date>last,c+'_stored'],merged.loc[merged.date>last,c+'_live']),f'{ticker}: action mismatch {c}')
        x=base.set_index('date').sort_index(); y=proposed.set_index('date').loc[x.index]
        changed=~np.isclose(x[NUM],y[NUM],rtol=1e-12,atol=1e-12).all(axis=1)
        has_event=(fresh[ACTION_FIELDS]!=0).any().any()
        if changed.any() or has_event:
            full=normalize(fetch(ticker,base.date.min().date().isoformat()))
            check_prices(full,ticker,[ticker])
            verified=reconcile(base,full)
        else:
            verified=pd.concat([base,fresh[base.columns]],ignore_index=True)
        try:
            compare_reconciled(verified,proposed,ticker,diagnostics)
        finally:
            pd.DataFrame(diagnostics).to_csv(report/'precision_checks.csv',index=False)
        revised+=int(changed.sum())
        for r in fresh.itertuples():
            for c in ACTION_FIELDS:
                if getattr(r,c): events.append({'ticker':ticker,'date':str(r.date),'field':c,'value':float(getattr(r,c))})
    pd.testing.assert_frame_equal(a.loc[~a.index.get_level_values('ticker').isin(symbols)],b.loc[~b.index.get_level_values('ticker').isin(symbols)],check_dtype=False)
    require(digest(archive)==expected_hash,'Baseline changed during validation')
    (report/'corporate_actions.json').write_text(json.dumps({'events':events,'revised_rows':revised,'issues':[]},indent=2))
    if len(added) or revised:
        shutil.copyfile(archive,report/'baseline_backup.csv.gz')
        shutil.copyfile(candidate,report/'publish_daily_prices.csv.gz')
    return {'result':'PASS','appended_rows':len(added),'revised_rows':revised,'baseline_rows':len(old),'candidate_rows':len(new),'latest_completed':str(completed[-1].date())}


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
