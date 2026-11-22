"""Investment Score v1.1. Never score stale/misaligned data as complete."""
import datetime as dt
import numpy as np
import pandas as pd
WEIGHTS={'quality':25,'growth':25,'valuation':20,'momentum':15}
BANKS={'JPM','GS'}
METRICS=('revenue','op_income','net_income','cfo','capex')

def num(x):return pd.to_numeric(x,errors='coerce')
def scale(v,a,b):
    if pd.isna(v):return np.nan
    return max(0.,min(100.,100.*(float(v)-a)/(b-a)))
def date(x):
    try:return dt.date.fromisoformat(str(x)[:10])
    except (ValueError,TypeError):return None

def score_row(r,asof=None):
    asof=asof or dt.date.today()
    def get(k):return num(r.get(k))
    bank=str(r.get('ticker')) in BANKS
    flags=[]
    # A single reporting end must support all ratios; do not borrow a 2017 CapEx
    # or 2015 operating income from an unrelated financial period.
    ends={k:date(r.get(k+'_end')) for k in METRICS}
    valid_ends=[v for v in ends.values() if v is not None and v<=asof and (asof-v).days<=200]
    target=max(valid_ends) if valid_ends else None
    def metric(k):
        value=get(k+'_ttm')
        end=ends[k]
        if pd.isna(value):flags.append('MISSING_'+k.upper());return np.nan
        if target is None or end is None or end!=target:
            flags.append('MISALIGNED_'+k.upper());return np.nan
        if end>asof or (asof-end).days>200:
            flags.append('STALE_'+k.upper());return np.nan
        filed=date(r.get(k+'_filed'))
        if filed is None or filed>asof or filed<end:
            flags.append('FILING_DATE_'+k.upper());return np.nan
        return value
    rev,op,net,cfo,capex=[metric(k) for k in METRICS]
    if pd.notna(rev) and rev>0 and pd.notna(capex) and (capex<0 or capex>0.5*rev):
        flags.append('CAPEX_ECONOMIC_SCOPE_REVIEW');capex=np.nan
    if bank:
        flags.append('BANK_MODEL_UNSUPPORTED')
        rev=op=net=cfo=capex=np.nan
    shares=get('shares_outstanding');price=get('price');cap=get('market_cap')
    share_date=date(r.get('shares_asof'))
    if pd.isna(shares) or shares<=0 or share_date is None or share_date>asof or (asof-share_date).days>150:
        cap=np.nan;flags.append('SHARES_UNVERIFIED_OR_STALE')
    elif pd.notna(price) and price>0:
        cap=shares*price
    else:cap=np.nan
    quote_date=date(r.get('quote_date'))
    fresh_price=quote_date is not None and 0<=(asof-quote_date).days<=7
    if not fresh_price:
        flags.append('PRICE_STALE');cap=np.nan
    op_margin=op/rev if pd.notna(op) and pd.notna(rev) and rev>0 else np.nan
    fcf_margin=(cfo-capex)/rev if all(pd.notna(x) for x in (cfo,capex,rev)) and rev>0 else np.nan
    pe=cap/net if pd.notna(cap) and pd.notna(net) and net>0 else np.nan
    ps=cap/rev if pd.notna(cap) and pd.notna(rev) and rev>0 else np.nan
    quality=.6*scale(op_margin,0,.4)+.4*scale(fcf_margin,0,.3) if pd.notna(op_margin) and pd.notna(fcf_margin) else np.nan
    growth=get('revenue_yoy')
    # Growth is valid only if it ends on the current reporting period.
    if pd.isna(rev):growth=np.nan
    growth=scale(growth,-.1,.4)
    valuation=.6*scale(pe,50,10)+.4*scale(ps,12,2) if pd.notna(pe) and pd.notna(ps) else np.nan
    m12,m6=get('mom12_1'),get('mom6_1')
    momentum=.7*scale(m12,-.4,.8)+.3*scale(m6,-.3,.5) if fresh_price and pd.notna(m12) and pd.notna(m6) else np.nan
    factors={'quality':quality,'growth':growth,'valuation':valuation,'momentum':momentum}
    avail=[k for k,v in factors.items() if pd.notna(v)]
    partial=sum(WEIGHTS[k]*factors[k] for k in avail)/sum(WEIGHTS[k] for k in avail) if avail else np.nan
    full=partial if len(avail)==4 and not bank else np.nan
    return {**factors,'score':full,'partial_score':partial,'completeness':len(avail)/4,'op_margin':op_margin,'fcf_margin':fcf_margin,'pe':pe,'ps':ps,'market_cap':cap,'validation_flags':';'.join(sorted(set(flags))),'ttm_target_end':target.isoformat() if target else ''}

def enrich(df,asof=None):
    d=df.copy()
    vals=d.apply(lambda r:pd.Series(score_row(r,asof)),axis=1)
    for col in vals:d[col]=vals[col]
    d['ranking_score']=d['score'].fillna(d['partial_score'])
    d=d.sort_values(['ranking_score','completeness','ticker'],ascending=[False,False,True],na_position='last').reset_index(drop=True)
    d['rank']=np.arange(1,len(d)+1)
    return d
