"""Read-only synchronization of research factors with the published archive."""
import numpy as np
import pandas as pd
from .scoring import enrich

FIELDS = ('price','sma50','sma200','rsi14','mom12_1','mom6_1')

def synchronize(snapshot, history, asof=None):
    h=history.copy()
    h['date']=pd.to_datetime(h.date,errors='raise')
    if h.empty or h.date.isna().any() or h.duplicated(['ticker','date']).any():
        raise ValueError('Invalid archive dates or duplicate keys')
    if 'SPY' not in set(h.ticker): raise ValueError('Missing SPY')
    latest=h.loc[h.ticker.eq('SPY'),'date'].max()
    today=pd.Timestamp(asof or pd.Timestamp.today().date()).normalize()
    if latest>today or (today-latest).days>7: raise ValueError('Archive date is future or stale')
    out=snapshot.copy().reset_index(drop=True)
    if out.ticker.duplicated().any(): raise ValueError('Duplicate snapshot ticker')
    out['technical_pass'] = False
    for i,r in out.iterrows():
        x=h.loc[h.ticker.eq(r.ticker)].sort_values('date')
        x=x.loc[x.date<=latest]
        c=pd.to_numeric(x.adj_close,errors='coerce')
        p=pd.to_numeric(x.close,errors='coerce')
        for k in FIELDS: out.at[i,k]=np.nan
        out.at[i,'quote_date']=''
        out.at[i,'price_sync_status']='MISSING_OR_INVALID'
        if x.empty or x.date.iloc[-1]!=latest or not np.isfinite(c).all() or not np.isfinite(p).all() or (c<=0).any() or (p<=0).any(): continue
        out.at[i,'price']=float(p.iloc[-1]);out.at[i,'quote_date']=latest.date().isoformat()
        # Preserve the existing 18-month RSI initialization and session offsets.
        c=c.loc[x.date>=latest-pd.DateOffset(months=18)]
        for n in (50,200):
            if len(c)>=n: out.at[i,f'sma{n}']=float(c.tail(n).mean())
        if len(c)>=15:
            delta=c.diff();up=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean();down=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
            out.at[i,'rsi14']=float(100-100/(1+up.iloc[-1]/down.iloc[-1])) if down.iloc[-1]>0 else 100.
        for k,n in [('mom12_1',253),('mom6_1',127)]:
            if len(c)>=n: out.at[i,k]=float(c.iloc[-1]/c.iloc[-n]-1)
        out.at[i,'technical_pass']=bool(c.iloc[-1]>out.at[i,'sma200'] and out.at[i,'sma50']>out.at[i,'sma200'])
        out.at[i,'price_sync_status']='OK' if len(c)>=253 else 'SHORT_HISTORY'
        # A split after the shares observation invalidates the old share count.
        sd=pd.to_datetime(r.get('shares_asof'),errors='coerce')
        if 'splits' in x and pd.notna(sd) and (pd.to_numeric(x.loc[x.date>sd,'splits'],errors='coerce').fillna(0)!=0).any():
            out.at[i,'shares_asof']='';out.at[i,'price_sync_status']='SHARES_SPLIT_REVIEW'
    return enrich(out,asof=today.date())
