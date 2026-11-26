"""Same-tag comparative reconstruction; no substitution of pretax for operating income."""
import datetime as dt
import math
from . import sec_v2_reference as sec
BROAD={'AMZN','HD','NVDA','V','CVX','GE','MRK'}

def resolve(data, metric, ticker, target, asof):
    tags=list(sec.TAGS[metric])
    if metric=='capex' and ticker in BROAD: tags.append('PaymentsToAcquireProductiveAssets')
    if metric=='revenue' and ticker=='GOOGL': tags=['Revenues']
    if not target:return None,None
    end=target.isoformat()
    for tag in tags:
        rows=[r for r in sec.facts(data,tag) if r.get('form') in ('10-K','10-Q','10-K/A','10-Q/A') and r.get('start') and r['end']<=r.get('filed','')<=asof.isoformat() and r['end']<=end and sec.duration(r)>0 and math.isfinite(r['val'])]
        periods={}
        for r in rows:
            k=(r['start'],r['end']); old=periods.get(k)
            if old is None or (r['filed'],r['accn'])>(old['filed'],old['accn']):periods[k]=r
        q={}
        for r in periods.values():
            dur=sec.duration(r); candidates=[]
            if 65<=dur<=105:candidates=[(r['start'],float(r['val']),[r], 'DIRECT')]
            elif 150<=dur<=380:
                for p in rows:
                    if p['start']!=r['start'] or p['end']>=r['end'] or p['filed']>r['filed']:continue
                    gap=(dt.date.fromisoformat(r['end'])-dt.date.fromisoformat(p['end'])).days
                    if 65<=gap<=105:candidates.append(((dt.date.fromisoformat(p['end'])+dt.timedelta(days=1)).isoformat(),float(r['val'])-float(p['val']),[r,p],'YTD_MINUS_PREV'))
            if dur>105 and candidates:
                latest=max(x[2][1]['filed'] for x in candidates)
                candidates=[x for x in candidates if x[2][1]['filed']==latest]
            for start,val,src,method in candidates:
                if metric=='capex' and val<0:continue
                rec=dict(start=start,end=r['end'],value=val,filed=r['filed'],tag=tag,sources=src,method=method)
                q.setdefault(r['end'],[]).append(rec)
        chain=[]; cursor=end
        for _ in range(8):
            opts=q.get(cursor,[])
            if not opts:break
            # Reject conflicting same-date direct facts rather than silently choosing a value.
            opts.sort(key=lambda x:(x['filed'],x['method']=='DIRECT'),reverse=True)
            best=opts[0]
            peers=[x for x in opts if x['filed']==best['filed'] and x['method']==best['method']]
            if any(x['start']!=best['start'] or not math.isclose(x['value'],best['value'],rel_tol=1e-9,abs_tol=1) for x in peers):break
            chain.append(best);cursor=(dt.date.fromisoformat(best['start'])-dt.timedelta(days=1)).isoformat()
        if len(chain)<4:continue
        last=chain[:4]; span=(target-dt.date.fromisoformat(last[-1]['start'])).days+1
        if not 330<=span<=380:continue
        result=dict(value=sum(x['value'] for x in last),end=end,filed=max(x['filed'] for x in last),sources=last)
        growth=None
        if len(chain)==8:
            prior=sum(x['value'] for x in chain[4:]);span2=(dt.date.fromisoformat(chain[4]['end'])-dt.date.fromisoformat(chain[7]['start'])).days+1
            if prior>0 and 330<=span2<=380:growth=result['value']/prior-1
        return result,growth
    return None,None
