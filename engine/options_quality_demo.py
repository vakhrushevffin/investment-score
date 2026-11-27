"""Fixed synthetic scenarios only; no live data or persistent writes."""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal as D
from .omr_quality import classify, compare_aggregate
from . import omr_archive_demo as archive

def checks():
    day=date(2026,1,5); end=datetime(2026,1,5,21,tzinfo=timezone.utc)
    start=end-timedelta(hours=7)
    a=dict(key='A',oi=10,volume=2,multiplier=100,iv=D('.2'),date=day,timestamp=end,deliverable='documented')
    b=dict(a,key='B')
    cases=[('Complete',[a,b],'VALID'),('Negative volume',[dict(a,volume=-1),b],'INVALID'),('Invalid multiplier',[dict(a,multiplier=0),b],'INVALID'),('Invalid date',[dict(a,date='2026-01-05'),b],'INVALID'),('Stale quote',[dict(a,timestamp=start-timedelta(seconds=1)),b],'STALE'),('Stale OI',[dict(a,date=day-timedelta(days=3)),dict(b,date=day-timedelta(days=3))],'STALE'),('Missing key',[a],'PARTIAL'),('Duplicate key',[a,a,b],'INCONSISTENT'),('Empty',[],'UNAVAILABLE'),('Unknown zero',[dict(a,iv=D(0)),b],'UNVERIFIED')]
    result=[]
    for name,rows,want in cases:
        got,reason=classify(rows,{'A','B'},day,end,quote_not_before=start,require_contract_fields=True)
        result.append(dict(Check=name,Expected=want,Observed=got,Reason=reason,Passed=got==want))
    for name,total,want in [('Rounding compatible',D('3.01'),True),('Beyond rounding',D('3.20'),False)]:
        r=compare_aggregate([D('1.00'),D('2.00')],total,steps=[D('.01')]*3)
        got=r['rounding_compatible']; result.append(dict(Check=name,Expected=str(want),Observed=str(got),Reason='Known nearest rounding; no materiality threshold assumed',Passed=got==want))
    return result

def archive_checks():
    db=archive.connect(':memory:')
    try:
        archive.init(db); cal=archive.calendar(); a=next(archive.seed(cal)); known=archive.parse(a['retrieved']); b=dict(a,value=12,source_version=2)
        result=[]
        for name,row,want in [('Initial',a,'VALID'),('Correction',b,'VALID'),('Old version replay',a,'STALE'),('Unknown order',dict(a,value=99,source_version=None),'UNVERIFIED')]:
            if row.get('source_version') is None: row={k:v for k,v in row.items() if k!='source_version'}
            _,_,got=archive.ingest(db,[row],cal,known)
            result.append(dict(Check=name,Expected=want,Observed=got,Reason='In-memory synthetic archive',Passed=got==want))
        import json
        value=json.loads(archive.snapshot(db,known)[0][2])['value']
        result.append(dict(Check='Corrected value retained',Expected='12',Observed=str(value),Reason='Current snapshot',Passed=value==12))
        return result
    finally: db.close()
