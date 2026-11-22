"""Conservative on-demand SEC/price update with per-metric audit trail."""
import datetime as dt
import time
import numpy as np
import pandas as pd
import requests
from . import sec_v2_reference as sec
from .scoring import enrich, METRICS, BANKS, date

MULTICLASS={'GOOGL','META','MA','V'}
# This tag includes assets beyond pure PP&E; label it as broad, not a confirmed CapEx equivalent.
BROAD_CAPEX={'NVDA','V'}

def refresh(df,email,progress=None):
    if '@' not in email or len(email)<6:raise ValueError('Введите контактный email для SEC.')
    import yfinance as yf
    out=df.copy().set_index('ticker',drop=False)
    logs=[];today=dt.date.today()
    session=requests.Session()
    session.headers.update({'User-Agent':f'InvestmentScoreResearch/2.0 ({email})','Accept-Encoding':'gzip, deflate'})
    sec.ASOF=today
    for i,t in enumerate(out.index):
        if progress:progress(i/len(out),f'{t}: котировки и SEC')
        old=out.loc[t].copy(); flags=[]
        try:
            closes=yf.Ticker(t).history(period='18mo',auto_adjust=True)['Close'].dropna()
            if len(closes)<220:raise ValueError('Недостаточно истории цен')
            price=float(closes.iloc[-1]);qd=closes.index[-1].date()
            if (today-qd).days>7 or qd>today:raise ValueError('Котировка устарела')
            out.at[t,'price']=price;out.at[t,'quote_date']=qd.isoformat()
            out.at[t,'sma50']=float(closes.tail(50).mean());out.at[t,'sma200']=float(closes.tail(200).mean())
            delta=closes.diff();up=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean();down=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
            out.at[t,'rsi14']=float(100-100/(1+up.iloc[-1]/down.iloc[-1])) if down.iloc[-1]>0 else 100.
            out.at[t,'mom12_1']=price/float(closes.iloc[-253])-1 if len(closes)>=253 else np.nan
            out.at[t,'mom6_1']=price/float(closes.iloc[-127])-1 if len(closes)>=127 else np.nan
            out.at[t,'technical_pass']=bool(price>out.at[t,'sma200'] and out.at[t,'sma50']>out.at[t,'sma200'])
            logs.append({'ticker':t,'source':'PRICE','metric':'price','result':'OK','details':qd.isoformat()})
        except Exception as e:
            flags.append('PRICE_REFRESH_FAILED')
            # Do not silently use the previous price in an up-to-date ranking.
            out.at[t,'quote_date']=''
            logs.append({'ticker':t,'source':'PRICE','metric':'price','result':'ERROR','details':str(e)[:180]})
        try:
            resp=session.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(old["cik"]):010d}.json',timeout=35)
            resp.raise_for_status();data=resp.json()
            orig={k:list(v) for k,v in sec.TAGS.items()}
            if t=='GOOGL':sec.TAGS['revenue']=['Revenues']
            if t in BROAD_CAPEX:sec.TAGS['capex']=['PaymentsToAcquireProductiveAssets']
            try:
                quarters={m:sec.build_metric(data,m) for m in METRICS}
                ttms={m:sec.ttm(quarters[m]) for m in METRICS}
            finally:sec.TAGS.update(orig)
            # A valid target comes from recent reporting, not an ancient CapEx series.
            current_ends=[date(v['end']) for k,v in ttms.items() if v and k in ('revenue','net_income','cfo')]
            current_ends=[x for x in current_ends if x and x<=today and (today-x).days<=200]
            target=max(current_ends) if current_ends else None
            for metric in METRICS:
                value=ttms[metric];end=date(value['end']) if value else None
                aligned=bool(target and end==target)
                if t in BANKS:aligned=False
                # Clear stale data instead of silently preserving previous observations.
                out.at[t,metric+'_ttm']=value['value'] if value and aligned else np.nan
                out.at[t,metric+'_end']=value['end'] if value and aligned else ''
                out.at[t,metric+'_filed']=value['filed'] if value and aligned else ''
                result='OK' if aligned else ('STALE_OR_MISMATCH' if value else 'MISSING')
                tag=','.join(sorted({s['tag'] for s in value['sources']})) if value else ''
                logs.append({'ticker':t,'source':'SEC','metric':metric,'result':result,'details':f'target={target}; observed={end}; tag={tag}'})
                if not aligned:flags.append(metric.upper()+'_'+result)
            rev=quarters['revenue'];a=rev[-8:]
            yoy=np.nan
            if target and len(a)==8 and date(a[-1]['end'])==target:
                gaps=[(date(b['end'])-date(x['end'])).days for x,b in zip(a,a[1:])]
                denom=sum(x['value'] for x in a[:4])
                if all(65<=g<=115 for g in gaps) and denom>0:yoy=sum(x['value'] for x in a[4:])/denom-1
            out.at[t,'revenue_yoy']=yoy if t not in BANKS else np.nan
            if t in MULTICLASS:
                # Existing audited/indicative all-class equivalent remains, but is not auto-verified.
                flags.append('MULTICLASS_CAPITALIZATION_REVIEW')
            else:
                s=sec.shares(data)
                if s and 0<=(today-date(s['end'])).days<=150:
                    out.at[t,'shares_outstanding']=float(s['val']);out.at[t,'shares_asof']=s['end'];out.at[t,'shares_filed']=s['filed']
                else:
                    out.at[t,'shares_asof']=''
                    flags.append('SHARES_MISSING_OR_STALE')
            if t in BROAD_CAPEX:flags.append('CAPEX_BROAD_SCOPE_REVIEW')
            if t in BANKS:flags.append('BANK_SEPARATE_MODEL_REQUIRED')
            out.at[t,'status']='REVIEW_REQUIRED' if flags else 'AUTOMATED_CHECK_ONLY'
            out.at[t,'flags']=';'.join(sorted(set(flags)))
        except Exception as e:
            # Failure must not make previous numbers look freshly validated.
            for m in METRICS:
                out.at[t,m+'_end']='';out.at[t,m+'_filed']='';out.at[t,m+'_ttm']=np.nan
            out.at[t,'revenue_yoy']=np.nan
            out.at[t,'status']='SEC_REFRESH_FAILED';out.at[t,'flags']='SEC_REFRESH_FAILED'
            logs.append({'ticker':t,'source':'SEC','metric':'all','result':'ERROR','details':str(e)[:180]})
        time.sleep(.15)
    if progress:progress(1.,'Готово')
    return enrich(out.reset_index(drop=True),asof=today),pd.DataFrame(logs)
