"""Offline educational market statistics; no network access."""
from pathlib import Path
import pandas as pd
import numpy as np

SYMBOLS = ['VIX9D','VIX','VIX3M','VXN']
FIELDS = ['date','series','value','source_id']

def validate(frame):
    if not set(FIELDS).issubset(frame.columns):
        raise ValueError('Missing required columns: date, series, value, source_id')
    d = frame[FIELDS].copy()
    d['date'] = pd.to_datetime(d.date, format='%Y-%m-%d', errors='raise')
    if d.date.isna().any() or (d.date != d.date.dt.normalize()).any():
        raise ValueError('Invalid dates')
    d['value'] = pd.to_numeric(d.value, errors='raise')
    if not np.isfinite(d.value).all() or (d.value < 0).any():
        raise ValueError('Invalid values')
    if d[['series','source_id']].isna().any().any() or not d.series.isin(SYMBOLS + ['TOTAL_PC','EQUITY_PC','INDEX_PC','ETP_PC']).all():
        raise ValueError('Invalid series or source')
    if d.duplicated(['date','series','source_id']).any():
        raise ValueError('Duplicate observations')
    return d.sort_values('date')

def demo():
    rng = np.random.default_rng(251)
    dates = pd.bdate_range('2020-01-01', periods=260)
    base = 20 + 3*np.sin(np.arange(len(dates))/15)
    rows = []
    for symbol, offset in zip(SYMBOLS, [-2,0,2,6]):
        rows.extend((d,symbol,round(float(v),2),'SYNTHETIC') for d,v in zip(dates,base+offset+rng.normal(0,.4,len(dates))))
    for symbol, offset in zip(['TOTAL_PC','EQUITY_PC','INDEX_PC','ETP_PC'],[1,.7,1.2,.9]):
        rows.extend((d,symbol,round(float(v),3),'SYNTHETIC') for d,v in zip(dates,offset+rng.uniform(-.15,.15,len(dates))))
    return validate(pd.DataFrame(rows,columns=FIELDS))

def load_history(folder):
    folder = Path(folder)
    registry = pd.read_csv(folder/'source_registry.csv',dtype=str).fillna('')
    required = ['source_id','series','source_url','scope','methodology','session','display_permission','permission_reference']
    if not set(required).issubset(registry.columns) or registry.duplicated(['source_id','series']).any():
        raise ValueError('Invalid source registry')
    allowed = registry.loc[registry.display_permission.eq('APPROVED') & registry[required].apply(lambda c:c.str.strip().ne('')).all(axis=1)]
    frames = [pd.read_csv(folder/name) for name in ['market_history.csv','put_call_history.csv'] if (folder/name).exists()]
    if not frames: raise ValueError('No historical files')
    d = validate(pd.concat(frames,ignore_index=True))
    if d.empty: raise ValueError("No historical observations")
    if d.source_id.eq('SYNTHETIC').any(): raise ValueError('Synthetic data in historical mode')
    merged = d.merge(allowed,on=['source_id','series'],how='left',validate='many_to_one',indicator=True)
    if not merged['_merge'].eq('both').all(): raise ValueError('Unapproved or undocumented source')
    return merged.drop(columns='_merge')

def common_snapshot(d):
    """Only matching dates and a single declared session; no forward filling."""
    subset = d[d.series.isin(SYMBOLS[:3])]
    if subset.groupby('series').source_id.nunique().gt(1).any(): raise ValueError('Choose one source per series')
    if 'session' in subset and subset.session.nunique()!=1: return None
    if subset.empty: return None
    p = subset.pivot(index='date',columns='series',values='value').reindex(columns=SYMBOLS[:3]).dropna()
    return None if p.empty else p.iloc[-1]
