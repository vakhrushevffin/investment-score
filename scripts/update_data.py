"""Batch refresh: offline-friendly; prices and fundamentals independently selectable."""
import argparse
import datetime as dt
import os
from pathlib import Path
import sys
import time
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine.refresh import refresh

REQUIRED=['ticker','cik','enabled','fundamental_enabled','technical_enabled']
def registry(path=ROOT/'data/tickers.csv'):
    r=pd.read_csv(path,dtype={'ticker':'string','cik':'Int64'})
    if not set(REQUIRED).issubset(r.columns):raise ValueError('Missing registry columns')
    r['ticker']=r.ticker.str.strip().str.upper()
    if r.ticker.isna().any() or r.ticker.eq('').any() or r.ticker.duplicated().any():raise ValueError('Invalid/duplicate tickers')
    if len(r)>1000:raise ValueError('Maximum registry size: 1000')
    for k in ('enabled','fundamental_enabled','technical_enabled'):
        r[k]=r[k].astype(str).str.strip().str.lower().isin(['true','1','yes'])
    if r.ticker.eq('SPY').any():raise ValueError('SPY belongs in the price archive only')
    return r

def snapshot_from_registry(reg, baseline):
    active = reg.loc[reg.enabled & reg.fundamental_enabled, ['ticker', 'cik']]
    if active.cik.isna().any() or active.cik.le(0).any() or active.cik.duplicated().any():
        raise ValueError('Active fundamental tickers require unique positive CIKs')
    snapshot = active.merge(
        baseline.drop(columns='cik'), on='ticker', how='left', validate='one_to_one',
        indicator=True,
    )
    snapshot.loc[snapshot['_merge'].eq('left_only'), 'status'] = 'SEC_PENDING'
    return snapshot.drop(columns='_merge')


def update_fundamentals(reg, batch=20, email=None, dry_run=False):
    old=pd.read_csv(ROOT/'data/baseline.csv').drop_duplicates('ticker')
    target=snapshot_from_registry(reg, old)
    if dry_run:return {'selected':len(target),'batches':(len(target)+batch-1)//batch}
    if not email or '@' not in email:raise ValueError('SEC_CONTACT_EMAIL environment variable required')
    logs=[];results=[];failed=[]
    for offset in range(0,len(target),batch):
        sub=target.iloc[offset:offset+batch].copy()
        try:
            result,log=refresh(sub,email,refresh_prices=False)
            results.append(result);logs.append(log)
            failed.extend(result.loc[result.status.eq('SEC_REFRESH_FAILED'),'ticker'].tolist())
        except Exception as exc:
            failed.extend(sub.ticker.tolist())
            logs.append({'ticker':','.join(sub.ticker),'source':'SEC','metric':'batch','result':'ERROR','details':str(exc)[:180]})
        print(f'Fundamentals batch {offset//batch+1}: {len(sub)} attempted',flush=True)
    if results:
        combined=pd.concat(results,ignore_index=True)
        # Preserve existing rows for disabled tickers; they remain visible in archive, not active ranking.
        other=old.loc[~old.ticker.isin(combined.ticker)]
        merged=pd.concat([combined,other],ignore_index=True)
        temp=ROOT/'data/baseline.csv.tmp';merged.to_csv(temp,index=False);temp.replace(ROOT/'data/baseline.csv')
    if logs:
        pd.concat([x if isinstance(x,pd.DataFrame) else pd.DataFrame([x]) for x in logs],ignore_index=True).to_csv(ROOT/'data/refresh_log.csv',index=False)
    return {'selected':len(target),'updated':len(target)-len(failed),'failed':len(failed)}

def update_prices(reg,batch=40,years=12,dry_run=False):
    target=reg[reg.enabled & reg.technical_enabled].ticker.tolist()
    symbols=sorted(set(target+['SPY']))
    if dry_run:return {'selected':len(symbols),'batches':(len(symbols)+batch-1)//batch}
    import yfinance as yf
    archive=ROOT/'data/daily_prices.csv.gz'
    existing=pd.read_csv(archive,parse_dates=['date']) if archive.exists() else pd.DataFrame()
    chunks=[];errors=[]
    start=(dt.date.today()-dt.timedelta(days=int(years*365.25)+30)).isoformat()
    for offset in range(0,len(symbols),batch):
        group=symbols[offset:offset+batch]
        try:
            data=yf.download(group,start=start,auto_adjust=False,actions=True,group_by='ticker',threads=False,progress=False)
            if data.empty:raise ValueError('No data returned')
            for t in group:
                try:
                    x=data[t] if isinstance(data.columns,pd.MultiIndex) else data
                    x=x.rename(columns={'Open':'open','High':'high','Low':'low','Close':'close','Adj Close':'adj_close','Volume':'volume','Dividends':'dividends','Stock Splits':'splits','Capital Gains':'capital_gains'})
                    x=x.reset_index().rename(columns={'Date':'date'})
                    for field in ('dividends','splits','capital_gains'):
                        if field not in x:
                            if field != 'capital_gains':raise ValueError(f'Missing {field}')
                            x[field]=0.
                    x['ticker']=t
                    x=x[['date','ticker','open','high','low','close','adj_close','volume','dividends','splits','capital_gains']].dropna(subset=['close','adj_close'])
                    if len(x)<200:raise ValueError('Fewer than 200 sessions')
                    x['date']=pd.to_datetime(x['date'],utc=True).dt.tz_localize(None)
                    if x.date.duplicated().any():raise ValueError('Duplicate dates in response')
                    if (x[['open','high','low','close','adj_close']]<=0).any().any():raise ValueError('Nonpositive OHLC price')
                    if not 0<=(dt.date.today()-x.date.max().date()).days<=7:raise ValueError('Future or stale latest quote')
                    numeric=x.drop(columns=['date','ticker']).apply(pd.to_numeric,errors='coerce')
                    if numeric.isna().any().any() or numeric.isin([float('inf'),float('-inf')]).any().any():raise ValueError('Missing or nonfinite price or action fields')
                    chunks.append(x)
                except Exception as e:errors.append({'ticker':t,'error':str(e)[:200]})
        except Exception as e:
            errors.extend({'ticker':t,'error':str(e)[:200]} for t in group)
        print(f'Prices batch {offset//batch+1}',flush=True)
        time.sleep(1)
    pd.DataFrame(errors,columns=['ticker','error']).to_csv(ROOT/'data/price_errors.csv',index=False)
    if errors:
        return {'selected':len(symbols),'updated':0,'errors':len(errors)}
    fresh=pd.concat(chunks,ignore_index=True)
    if fresh.groupby('ticker').date.max().nunique() != 1:
        raise ValueError('Latest price sessions are not synchronized')
    if not existing.empty:
        # Preserve older dates; only replace overlapping ticker/date observations.
        fresh=pd.concat([existing,fresh],ignore_index=True)
    fresh['date']=pd.to_datetime(fresh.date).dt.tz_localize(None)
    fresh=fresh.drop_duplicates(['ticker','date'],keep='last').sort_values(['ticker','date'])
    if fresh[['ticker','date','close','adj_close']].isna().any().any():raise ValueError('Archive integrity failure')
    tmp=archive.with_suffix('.tmp')
    fresh.to_csv(tmp,index=False,compression='gzip');tmp.replace(archive)
    return {'selected':len(symbols),'updated':len(chunks),'errors':0}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['prices','fundamentals','all'],default='prices')
    parser.add_argument('--batch-size',type=int,default=20);parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();r=registry()
    if args.batch_size<1 or args.batch_size>100:parser.error('batch-size must be 1..100')
    problems=0
    if args.mode in ('fundamentals','all'):
        result=update_fundamentals(r,args.batch_size,os.getenv('SEC_CONTACT_EMAIL'),args.dry_run)
        print('Fundamentals:',result);problems+=result.get('failed',0)
    if args.mode in ('prices','all'):
        result=update_prices(r,args.batch_size,dry_run=args.dry_run)
        print('Prices:',result);problems+=result.get('errors',0)
    if problems:raise SystemExit(f'Partial refresh: {problems} failures; see data error logs')
if __name__=='__main__':main()
