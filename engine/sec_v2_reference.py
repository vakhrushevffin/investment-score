"""SEC Companyfacts PIT current screener, with preserved 2026-11-20 quotes.
Run in Colab with SEC_CONTACT_EMAIL env var. Conservative: never fabricate missing metrics.
"""
import os,json,zipfile,io,time,datetime as dt,requests,pandas as pd,numpy as np
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ASOF=dt.date.fromisoformat(os.environ.get('SCORE_ASOF','2026-11-22'))
CIKS=json.loads((ROOT/'ticker_cik.json').read_text())
TAGS={'revenue':['RevenueFromContractWithCustomerExcludingAssessedTax','SalesRevenueNet','Revenues','RevenueFromContractWithCustomerIncludingAssessedTax'], 'op_income':['OperatingIncomeLoss'], 'net_income':['NetIncomeLoss','ProfitLoss'], 'cfo':['NetCashProvidedByUsedInOperatingActivities'], 'capex':['PaymentsToAcquirePropertyPlantAndEquipment']}
BANKS={'JPM','GS','BAC'}
SPECIAL={'CVX','GE','JNJ','MRK','NVDA','COST','GOOGL','META','MA'}

def duration(r):
 try:return (dt.date.fromisoformat(r['end'])-dt.date.fromisoformat(r['start'])).days+1
 except:return 0

def facts(data,tag,unit='USD'):
 return data.get('facts',{}).get('us-gaap',{}).get(tag,{}).get('units',{}).get(unit,[])

def eligible(data,tag):
 return [r for r in facts(data,tag) if r.get('form') in ('10-Q','10-K','10-Q/A','10-K/A') and r.get('filed','9999')<=ASOF.isoformat() and r.get('start') and duration(r)>0]

def select_filing_periods(data):
 # SEC fy/fp labels belong to filing, NOT comparative facts; use each filing's newest end.
 pool={}
 for tag in set(sum(TAGS.values(),[])):
  for r in eligible(data,tag):
   pool.setdefault((r['accn'],r['filed'],r['form']),[]).append(r)
 out={}
 for (acc,filed,form),rs in pool.items():
  if not rs:continue
  # identify reporting quarter end via maximum current-period end across all tags in this filing
  end=max(r['end'] for r in rs)
  current=[r for r in rs if r['end']==end]
  if not current:continue
  fy=next((r.get('fy') for r in current if r.get('fy')),None)
  fp=next((r.get('fp') for r in current if r.get('fp')),None)
  out[(acc,filed,form)]={'end':end,'fy':fy,'fp':fp,'facts':rs}
 return out

def choose_fact(rows,kind,filing_end):
 # Find direct quarterly, fiscal YTD or annual within same accession and tag.
 same=[r for r in rows if r['end']==filing_end]
 if not same:return None
 if kind=='quarter':
  cand=[r for r in same if 65<=duration(r)<=105]
 elif kind=='annual':
  cand=[r for r in same if 330<=duration(r)<=380]
 else:
  cand=[r for r in same if 65<=duration(r)<=320]
 if not cand:return None
 return sorted(cand,key=lambda r:(abs(duration(r)-({'quarter':91,'annual':365,'ytd':270}[kind])),r['start']))[0]

def build_metric(data,metric):
 # Group by real fiscal period end; use only the issuer's current reporting period per accession.
 by_end={}
 for tag in TAGS[metric]:
  for r in eligible(data,tag):
   by_end.setdefault(r['end'],[]).append((tag,r))
 # A comparative observation cannot be used to infer its filing's fiscal period.
 # Current reporting ends = maximum duration-fact end in each accession.
 filing_ends={}
 for tag in set(sum(TAGS.values(),[])):
  for r in eligible(data,tag):
   filing_ends[r['accn']]=max(filing_ends.get(r['accn'],''),r['end'])
 candidates={}
 for end,items in by_end.items():
  for tag in TAGS[metric]:
   current=[r for t,r in items if t==tag and filing_ends.get(r['accn'])==end]
   for r in current:
    key=(r['filed'],r['accn'])
    candidates.setdefault(end,{}).setdefault(key,{}).setdefault(tag,[]).append(r)
 records={}
 for end,filings in candidates.items():
  # latest eligible filing first; within filing, preferred tag; no mixing tags within a metric quarter
  for (filed,acc),tagmap in sorted(filings.items(),reverse=True):
   result=None
   for tag in TAGS[metric]:
    rs=tagmap.get(tag,[])
    direct=choose_fact(rs,'quarter',end)
    annual=choose_fact(rs,'annual',end)
    ytd=choose_fact(rs,'ytd',end)
    if direct is not None:
     result={'value':float(direct['val']),'end':end,'filed':filed,'accn':acc,'tag':tag,'method':'DIRECT','sources':[direct]};break
    if annual is not None:
     # Q4 annual - 9-month YTD of same tag and same fiscal-year START; previous report required
     prev=[p for p in eligible(data,tag) if p['end']<end and 240<=duration(p)<=310 and p['start']==annual['start'] and p['filed']<=filed and 55<=(dt.date.fromisoformat(end)-dt.date.fromisoformat(p['end'])).days<=115]
     if prev:
      p=max(prev,key=lambda x:(x['end'],x['filed']))
      result={'value':float(annual['val'])-float(p['val']),'end':end,'filed':filed,'accn':acc,'tag':tag,'method':'FY_MINUS_9M','sources':[annual,p]};break
    if ytd is not None:
     # Q2/Q3 cash flows: YTD minus prior YTD of same fiscal start/tag; reject Q1 YTD as direct if not quarter-sized
     prev=[p for p in eligible(data,tag) if p['end']<end and p['start']==ytd['start'] and p['filed']<=filed and 65<=duration(p)<duration(ytd) and 55<=(dt.date.fromisoformat(end)-dt.date.fromisoformat(p['end'])).days<=115]
     if prev:
      p=max(prev,key=lambda x:(x['end'],x['filed']))
      result={'value':float(ytd['val'])-float(p['val']),'end':end,'filed':filed,'accn':acc,'tag':tag,'method':'YTD_MINUS_PREV_YTD','sources':[ytd,p]};break
   if result is not None:
    records[end]=result;break
 return [records[k] for k in sorted(records)]

def ttm(q):
 if len(q)<4:return None
 last=q[-4:]
 ends=[dt.date.fromisoformat(x['end']) for x in last]
 if any(not 65<=(b-a).days<=115 for a,b in zip(ends,ends[1:])):return None
 if not 330<=(ends[-1]-ends[0]).days+91<=410:return None
 return {'value':sum(x['value'] for x in last),'end':last[-1]['end'],'filed':max(x['filed'] for x in last),'sources':last}

def shares(data):
 raw=data.get('facts',{}).get('dei',{}).get('EntityCommonStockSharesOutstanding',{}).get('units',{}).get('shares',[])
 a=[r for r in raw if r.get('filed','9999')<=ASOF.isoformat() and r.get('form') in ('10-Q','10-K','10-Q/A','10-K/A') and r.get('val',0)>0]
 return max(a,key=lambda r:(r['end'],r['filed'])) if a else None

def scale(v,a,b):return max(0,min(100,100*(v-a)/(b-a)))

def main():
 email=os.environ.get('SEC_CONTACT_EMAIL','')
 if '@' not in email:raise ValueError('Set SEC_CONTACT_EMAIL to your real email for SEC User-Agent')
 input_zip=ROOT/'Investment_Score_Current_Results.zip'
 if not input_zip.exists():raise FileNotFoundError('Place the previous Investment_Score_Current_Results.zip beside this script')
 with zipfile.ZipFile(input_zip) as z: old=pd.read_csv(io.BytesIO(z.read('current_score.csv')))
 old=old.set_index('ticker',drop=False)
 rows=[];lineage=[];errors=[]
 session=requests.Session();session.headers.update({'User-Agent':f'Investment Score Research {email}','Accept-Encoding':'gzip, deflate'})
 for i,(ticker,cik) in enumerate(CIKS.items(),1):
  print(f'[{i}/{len(CIKS)}] {ticker}',flush=True)
  p=old.loc[ticker] if ticker in old.index else None
  row={'ticker':ticker,'cik':cik,'asof':ASOF.isoformat(),'status':'REVIEW'}
  if p is not None:
   for k in ('price','quote_date','sma50','sma200','rsi14','mom12_1','mom6_1','technical_pass'):row[k]=p.get(k)
  try:
   resp=session.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json',timeout=50);resp.raise_for_status();data=resp.json()
   time.sleep(.25)
   q={m:build_metric(data,m) for m in TAGS};t={m:ttm(q[m]) for m in TAGS}
   for m,v in t.items():
    row[m+'_ttm']=v['value'] if v else None
    row[m+'_end']=v['end'] if v else None
    row[m+'_filed']=v['filed'] if v else None
    if v:
     for x in v['sources']:
      lineage.append({'ticker':ticker,'metric':m,'ttm_end':v['end'],'quarter_end':x['end'],'value':x['value'],'filed':x['filed'],'accession':x['accn'],'tag':x['tag'],'method':x['method'],'raw_facts':json.dumps(x['sources'])})
   rev=q['revenue'];rev8=rev[-8:];growth=None
   if len(rev8)==8 and all(65<=(dt.date.fromisoformat(b['end'])-dt.date.fromisoformat(a['end'])).days<=115 for a,b in zip(rev8,rev8[1:])):
    before=sum(x['value'] for x in rev8[:4]);growth=sum(x['value'] for x in rev8[4:])/before-1 if before>0 else None
   row['revenue_yoy']=growth
   s=shares(data)
   row['shares_outstanding']=s['val'] if s else None;row['shares_asof']=s['end'] if s else None;row['shares_filed']=s['filed'] if s else None
   row['market_cap']=row.get('price')*s['val'] if s and pd.notna(row.get('price')) else None
   same_end=len({t[m]['end'] for m in TAGS if t[m]})==1 and all(t.values())
   row['op_margin']=t['op_income']['value']/t['revenue']['value'] if t['op_income'] and t['revenue'] and t['op_income']['end']==t['revenue']['end'] and t['revenue']['value']>0 else None
   row['fcf_margin']=(t['cfo']['value']-t['capex']['value'])/t['revenue']['value'] if t['cfo'] and t['capex'] and t['revenue'] and len({t[x]['end'] for x in ('cfo','capex','revenue')})==1 and t['revenue']['value']>0 else None
   row['pe']=row['market_cap']/t['net_income']['value'] if t['net_income'] and row['market_cap'] and t['net_income']['value']>0 else None
   row['ps']=row['market_cap']/t['revenue']['value'] if t['revenue'] and row['market_cap'] and t['revenue']['value']>0 else None
   row['quality']=.6*scale(row['op_margin'],0,.4)+.4*scale(row['fcf_margin'],0,.3) if row['op_margin'] is not None and row['fcf_margin'] is not None else None
   row['growth']=scale(growth,-.1,.4) if growth is not None else None
   row['valuation']=.6*scale(row['pe'],50,10)+.4*scale(row['ps'],12,2) if row['pe'] and row['ps'] else None
   row['momentum']=.7*scale(row['mom12_1'],-.4,.8)+.3*scale(row['mom6_1'],-.3,.5) if pd.notna(row.get('mom12_1')) and pd.notna(row.get('mom6_1')) else None
   row['score']=sum(w*row[k] for k,w in [('quality',25),('growth',25),('valuation',20),('momentum',15)])/85 if all(row.get(k) is not None for k in ('quality','growth','valuation','momentum')) else None
   flags=[]
   if ticker in BANKS:flags.append('BANK_MODEL_UNSUPPORTED')
   if ticker in SPECIAL:flags.append('ACCOUNTING_REVIEW')
   if ticker in {'GOOGL','META','MA'}:flags.append('MULTICLASS_SHARES_REVIEW')
   if not same_end:flags.append('METRIC_PERIOD_MISMATCH')
   if row['score'] is None:flags.append('INCOMPLETE_SCORE')
   if any(t[m] and (ASOF-dt.date.fromisoformat(t[m]['filed'])).days>150 for m in TAGS):flags.append('STALE_FINANCIAL_METRIC')
   if s is None or (ASOF-dt.date.fromisoformat(s['end'])).days>150:flags.append('STALE_SHARES')
   if pd.isna(row.get('quote_date')) or (ASOF-dt.date.fromisoformat(str(row['quote_date']))).days>7:flags.append('STALE_QUOTE')
   if any(t[m] and (ASOF-dt.date.fromisoformat(t[m]['end'])).days>200 for m in TAGS):flags.append('STALE_PERIOD_END')
   row['flags']=';'.join(flags)
   row['status']='REVIEW' if flags else ('TECH_PASS' if row.get('technical_pass') else 'WATCH')
  except Exception as e:
   errors.append({'ticker':ticker,'error':repr(e)});row['flags']='SEC_FETCH_OR_PARSE_ERROR';row['score']=None
  rows.append(row)
 df=pd.DataFrame(rows)
 # Never present an unverified complete score as investable; rank only clean data
 df['verified_score']=df.apply(lambda r:r['score'] if r.get('status') in ('TECH_PASS','WATCH') else np.nan,axis=1)
 df['rank']=df['verified_score'].rank(ascending=False,method='min')
 df=df.sort_values(['verified_score','ticker'],ascending=[False,True],na_position='last')
 out=ROOT/'output';out.mkdir(exist_ok=True)
 df.to_csv(out/'current_score.csv',index=False);pd.DataFrame(lineage,columns=['ticker','metric','ttm_end','quarter_end','value','filed','accession','tag','method','raw_facts']).to_csv(out/'sec_lineage.csv',index=False)
 pd.DataFrame(errors,columns=['ticker','error']).to_csv(out/'errors.csv',index=False)
 with zipfile.ZipFile(ROOT/'Investment_Score_SEC_Fix_v2_Results.zip','w',zipfile.ZIP_DEFLATED) as z:
  for n in ('current_score.csv','sec_lineage.csv','errors.csv'):z.write(out/n,n)
 print('Completed',len(df),'raw complete',int(df.score.notna().sum()),'verified',int(df.verified_score.notna().sum()),'errors',len(errors))
 try:
  from google.colab import files
  files.download(str(ROOT/'Investment_Score_SEC_Fix_v2_Results.zip'))
 except ImportError:pass
if __name__=='__main__':main()
