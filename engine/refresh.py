"""Explicit on-demand update. No broker trading and no automatic approvals."""
import datetime as dt
import json,time
import numpy as np
import pandas as pd
import requests
from . import sec_v2_reference as sec
from .scoring import enrich

MULTICLASS={'GOOGL','META','MA','V'}
BROAD_CAPEX={'NVDA','V'}

def refresh(df,email,progress=None):
    if '@' not in email or len(email)<6:
        raise ValueError('Укажите действительный контактный email для SEC User-Agent в настройках.')
    import yfinance as yf
    out=df.copy().set_index('ticker',drop=False)
    logs=[]
    session=requests.Session()
    session.headers.update({'User-Agent':f'InvestmentScoreResearch/1.0 ({email})','Accept-Encoding':'gzip, deflate'})
    sec.ASOF=dt.date.today()
    for i,t in enumerate(out.index):
        if progress: progress(i/len(out),f'{t}: котировки и SEC')
        old=out.loc[t].copy()
        try:
            history=yf.Ticker(t).history(period='18mo',auto_adjust=True)
            closes=history['Close'].dropna()
            if len(closes)<220: raise ValueError('Недостаточно истории цен')
            price=float(closes.iloc[-1]); date=str(closes.index[-1].date())
            out.at[t,'price']=price;out.at[t,'quote_date']=date
            out.at[t,'sma50']=float(closes.tail(50).mean());out.at[t,'sma200']=float(closes.tail(200).mean())
            delta=closes.diff(); up=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean();down=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
            out.at[t,'rsi14']=float(100-100/(1+up.iloc[-1]/down.iloc[-1])) if down.iloc[-1]>0 else 100.
            out.at[t,'mom12_1']=price/float(closes.iloc[-253])-1 if len(closes)>=253 else np.nan
            out.at[t,'mom6_1']=price/float(closes.iloc[-127])-1 if len(closes)>=127 else np.nan
            out.at[t,'technical_pass']=bool(price>out.at[t,'sma200'] and out.at[t,'sma50']>out.at[t,'sma200'])
        except Exception as e:
            logs.append({'ticker':t,'source':'PRICE','result':'ERROR','details':str(e)[:180]})
            # Keep old quote with original date; no fake freshness.
        try:
            cik=int(old['cik'])
            response=session.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json',timeout=25)
            response.raise_for_status(); data=response.json()
            # Special mapping already used in saved baseline; scope is explicitly flagged.
            oldtags={k:list(v) for k,v in sec.TAGS.items()}
            if t=='GOOGL':sec.TAGS['revenue']=['Revenues']
            if t in BROAD_CAPEX:sec.TAGS['capex']=['PaymentsToAcquireProductiveAssets']
            try:
                quarters={m:sec.build_metric(data,m) for m in sec.TAGS}
                ttms={m:sec.ttm(quarters[m]) for m in sec.TAGS}
            finally:
                sec.TAGS.update(oldtags)
            ends={v['end'] for v in ttms.values() if v}
            aligned=len(ends)==1 and all(ttms.values())
            for metric,value in ttms.items():
                out.at[t,metric+'_ttm']=value['value'] if value else np.nan
                out.at[t,metric+'_end']=value['end'] if value else ''
                out.at[t,metric+'_filed']=value['filed'] if value else ''
            q=quarters['revenue']
            if len(q)>=8:
                a=q[-8:]; gaps=[(dt.date.fromisoformat(b['end'])-dt.date.fromisoformat(x['end'])).days for x,b in zip(a,a[1:])]
                out.at[t,'revenue_yoy']=sum(x['value'] for x in a[4:])/sum(x['value'] for x in a[:4])-1 if all(65<=g<=115 for g in gaps) and sum(x['value'] for x in a[:4])>0 else np.nan
            else:out.at[t,'revenue_yoy']=np.nan
            # Multiclass counts must be separately audited: do NOT overwrite with DEI single-class count.
            if t not in MULTICLASS:
                shares=sec.shares(data)
                if shares:
                    out.at[t,'shares_outstanding']=float(shares['val']);out.at[t,'shares_asof']=shares['end']
            shares=pd.to_numeric(out.at[t,'shares_outstanding'],errors='coerce')
            price=pd.to_numeric(out.at[t,'price'],errors='coerce')
            out.at[t,'market_cap']=float(shares*price) if pd.notna(shares) and pd.notna(price) else np.nan
            flags=str(old.get('flags','')) if pd.notna(old.get('flags')) else ''
            flags=';'.join(filter(None,[flags,'MULTICLASS_SHARES_FROZEN_REVIEW' if t in MULTICLASS else '', 'TTM_MISMATCH_OR_MISSING' if not aligned else '']))
            out.at[t,'flags']=flags
            out.at[t,'status']='REVIEW_REQUIRED' if (not aligned or t in MULTICLASS or flags) else 'AUTOMATED_CHECK_ONLY'
            logs.append({'ticker':t,'source':'SEC','result':'OK' if aligned else 'PARTIAL','details':','.join(sorted(ends))})
        except Exception as e:
            out.at[t,'status']='SEC_REFRESH_FAILED'
            out.at[t,'flags']='SEC_REFRESH_FAILED;'+str(old.get('flags',''))
            logs.append({'ticker':t,'source':'SEC','result':'ERROR','details':str(e)[:180]})
        time.sleep(.15)
    if progress:progress(1.,'Готово')
    return enrich(out.reset_index(drop=True)),pd.DataFrame(logs)
