"""Fail-closed reconciliation of split-normalized Yahoo history."""
import numpy as np
import pandas as pd

PRICE=['open','high','low','close']
NUM=PRICE+['adj_close','volume']
EVENT=['dividends','splits','capital_gains']

def require(ok,msg):
    if not ok: raise ValueError(msg)

def same(a,b,rtol=2e-5,atol=2e-5):
    return np.allclose(a,b,rtol=rtol,atol=atol,equal_nan=True)

def normalize(x):
    x=x.rename(columns={'stock_splits':'splits'}).copy()
    require(not x.columns.duplicated().any(),'Duplicate provider fields')
    for c in EVENT:
        if c not in x:
            require(c=='capital_gains',f'Missing provider field {c}')
            x[c]=0.
        x[c]=pd.to_numeric(x[c],errors='coerce')
        require(np.isfinite(x[c]).all() and (x[c]>=0).all(),f'Invalid {c}')
    require(np.isfinite(x[NUM]).all().all(),'Nonfinite prices')
    return x

def reconcile(old,live):
    """Full coverage required. Preserve historical cells unless justified by new events."""
    live=normalize(live).set_index('date').sort_index()
    a=old.set_index('date').sort_index(); last=a.index.max()
    require(a.index.isin(live.index).all(),'Missing historical sessions')
    require(set(live.index[live.index<=last])==set(a.index),'Unexpected historical sessions')
    b=live.loc[a.index]; fresh=live.loc[live.index>last]
    require(len(a)>=3,'Insufficient history')
    splits=float(fresh.splits.replace(0,1).prod())
    require(np.isfinite(splits) and splits>0,'Invalid cumulative split')
    scale=1.
    if not same(a[PRICE],b[PRICE]):
        require(splits!=1 and same(a[PRICE]/splits,b[PRICE]),'Unexplained OHLC revision')
        scale=splits
    require(same(a.volume,b.volume,rtol=0,atol=1) or (scale!=1 and same(a.volume*scale,b.volume,rtol=0,atol=1)), 'Unexplained volume revision')
    # New cash distributions imply a uniform backward adjustment. Yahoo amounts
    # and previous closes are on the same current split-normalized basis.
    factor=1.
    for day,r in fresh.iterrows():
        cash=r.dividends+r.capital_gains
        if cash:
            prev=live.loc[live.index<day,'close'].iloc[-1]
            require(0<cash<prev,'Invalid cash distribution relative to prior close')
            factor*=1-cash/prev
    expected=a.adj_close/scale*factor
    # Already normalized series is accepted only if full historical values agree.
    if not same(a.adj_close,b.adj_close,rtol=5e-5,atol=2e-5):
        require(same(expected,b.adj_close,rtol=5e-4,atol=2e-5),'Unexplained adjusted-close revision')
    out=a.copy()
    for c in NUM:
        if not same(a[c],b[c],rtol=5e-5 if c=='adj_close' else 2e-5,atol=2e-5): out[c]=b[c]
    # Historical dividends are per share and must follow a confirmed split.
    if scale!=1:
        for c in ['dividends','capital_gains']:
            if c in out:
                mask=a[c].notna()
                require(same(a.loc[mask,c]/scale,b.loc[mask,c]),f'Unexplained historical {c}')
                out.loc[mask,c]=b.loc[mask,c]
    return pd.concat([out,fresh[a.columns]]).reset_index()[old.columns]
