"""Same v1.1 factor scale as saved SEC model. Missing factor -> no invented full score."""
import math
import pandas as pd
import numpy as np
WEIGHTS={'quality':25,'growth':25,'valuation':20,'momentum':15}

def scale(v, a, b):
    if pd.isna(v): return np.nan
    return max(0.,min(100.,100*(float(v)-a)/(b-a)))

def score_row(r):
    def get(k): return pd.to_numeric(r.get(k),errors='coerce')
    rev,op,cfo,capex,net,cap = [get(k) for k in ['revenue_ttm','op_income_ttm','cfo_ttm','capex_ttm','net_income_ttm','market_cap']]
    op_margin=op/rev if pd.notna(op) and pd.notna(rev) and rev>0 else np.nan
    fcf_margin=(cfo-capex)/rev if all(pd.notna(x) for x in (cfo,capex,rev)) and rev>0 else np.nan
    pe=cap/net if pd.notna(cap) and pd.notna(net) and net>0 else np.nan
    ps=cap/rev if pd.notna(cap) and pd.notna(rev) and rev>0 else np.nan
    quality=.6*scale(op_margin,0,.4)+.4*scale(fcf_margin,0,.3) if pd.notna(op_margin) and pd.notna(fcf_margin) else np.nan
    growth=scale(get('revenue_yoy'),-.1,.4)
    valuation=.6*scale(pe,50,10)+.4*scale(ps,12,2) if pd.notna(pe) and pd.notna(ps) else np.nan
    m12,m6=get('mom12_1'),get('mom6_1')
    momentum=.7*scale(m12,-.4,.8)+.3*scale(m6,-.3,.5) if pd.notna(m12) and pd.notna(m6) else np.nan
    factors={'quality':quality,'growth':growth,'valuation':valuation,'momentum':momentum}
    avail=[k for k,v in factors.items() if pd.notna(v)]
    partial=sum(WEIGHTS[k]*factors[k] for k in avail)/sum(WEIGHTS[k] for k in avail) if avail else np.nan
    full=partial if len(avail)==4 else np.nan
    return {**factors,'score':full,'partial_score':partial,'completeness':len(avail)/4,'op_margin':op_margin,'fcf_margin':fcf_margin,'pe':pe,'ps':ps}

def enrich(df):
    d=df.copy()
    values=d.apply(lambda r:pd.Series(score_row(r)),axis=1)
    for col in values: d[col]=values[col]
    # Rank all names: full scores first by numerical score, incomplete by partial score;
    # the missing-data penalty is visible and deliberately not an imputed score.
    d['ranking_score']=d['score'].fillna(d['partial_score'])
    d=d.sort_values(['ranking_score','completeness','ticker'],ascending=[False,False,True],na_position='last').reset_index(drop=True)
    d['rank']=np.arange(1,len(d)+1)
    return d
