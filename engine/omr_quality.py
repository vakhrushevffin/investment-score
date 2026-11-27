import argparse
import csv
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path


def reconcile(a, b, qa, qb, reference_floor):
    difference = b-a
    radius = (qa+qb)/2
    relative = abs(difference)/max(abs(a),abs(b)) if max(abs(a),abs(b)) > reference_floor else None
    return difference, relative, abs(difference) <= radius


def aware(value):
    return isinstance(value, datetime) and value.utcoffset() is not None


def classify(rows, expected, day, cutoff, units='decimal', method='v1', prior=None,
             *, quote_not_before=None, require_contract_fields=False):
    if type(day) is not date or not aware(cutoff):
        return 'INVALID', 'invalid_reference_dates'
    if quote_not_before is None:
        quote_not_before = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    if not aware(quote_not_before):
        return 'INVALID', 'invalid_quote_window'
    cutoff_utc = cutoff.astimezone(timezone.utc)
    quote_start_utc = quote_not_before.astimezone(timezone.utc)
    if quote_start_utc > cutoff_utc:
        return 'INVALID', 'invalid_quote_window'
    if not rows:
        return 'UNAVAILABLE', 'no_rows'
    if units not in ('decimal','percent') or any(
        not isinstance(x, Mapping) or
        (x.get('volume') is not None and (type(x['volume']) is not int or x['volume'] < 0)) or
        (x.get('multiplier') is not None and (
            type(x['multiplier']) not in (int, D) or
            not D(x['multiplier']).is_finite() or x['multiplier'] <= 0)) or
        type(x.get('oi')) is not int or x['oi']<0 or
        (x.get('iv') is not None and (not isinstance(x['iv'],D) or not x['iv'].is_finite() or x['iv']<0))
        or not isinstance(x.get('key'),str) or not x['key']
        or type(x.get('date')) is not date
        or not aware(x.get('timestamp'))
        or x['date']>day or x['timestamp'].astimezone(timezone.utc)>cutoff_utc
        or x.get('deliverable')=='invalid'
        for x in rows):
        return 'INVALID', 'schema_units_or_future'
    keys=Counter(x['key'] for x in rows)
    if any(n>1 for n in keys.values()):
        return 'INCONSISTENT', 'duplicate_key'
    if expected is not None and set(keys)-expected:
        return 'INCONSISTENT', 'out_of_scope_key'
    dates={x['date'] for x in rows}
    if len(dates)>1:
        return 'INCONSISTENT','mixed_effective_dates'
    if prior is not None and {x['key']:x for x in rows}!={x['key']:x for x in prior}:
        return 'INCONSISTENT','same_version_changed'
    stale_oi = next(iter(dates)) < day
    stale_quote = any(x['timestamp'].astimezone(timezone.utc) < quote_start_utc for x in rows)
    if stale_oi and stale_quote:
        return 'STALE', 'prior_effective_date_and_stale_quote'
    if stale_oi:
        return 'STALE', 'prior_effective_date'
    if stale_quote:
        return 'STALE', 'stale_quote'
    if any(x.get(field) is None and (require_contract_fields or field in x)
           for x in rows for field in ('volume', 'multiplier')):
        return 'PARTIAL', 'missing_contract_fields'
    if (expected is not None and set(keys)!=expected) or any(x.get('iv') is None or x.get('zero_origin')=='placeholder' for x in rows):
        return 'PARTIAL','coverage_or_missing'
    if expected is None or not method or any(
        not x.get('deliverable') or (x['iv']==0 and x.get('zero_origin') not in ('measured','rounded')) for x in rows):
        return 'UNVERIFIED','lineage_or_zero_unknown'
    return 'VALID','passed'


def write(path,header,rows):
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def compare_aggregate(parts, reported, *, steps=None, materiality=None):
    """Compare a reported total with components in one already aligned scope.

    Decimal steps are supplied only when nearest rounding is documented.
    Materiality is an externally justified tolerance in the same units.
    """
    values = (*parts, reported)
    if not parts or any(not isinstance(x, D) or not x.is_finite() for x in values):
        raise ValueError('Finite Decimal components and total required')
    difference = reported - sum(parts)
    lower_bound = None
    if steps is not None:
        if len(steps) != len(values) or any(
            not isinstance(x, D) or not x.is_finite() or x < 0 for x in steps
        ):
            raise ValueError('Provide one non-negative Decimal step per value')
        lower_bound = max(D(0), abs(difference) - sum(steps)/2)
    if materiality is not None and (
        not isinstance(materiality, D) or not materiality.is_finite() or materiality < 0
    ):
        raise ValueError('Materiality must be a non-negative finite Decimal')
    return {
        'difference': difference,
        'rounding_compatible': None if lower_bound is None else lower_bound == 0,
        'material_beyond_rounding': (
            None if materiality is None or lower_bound is None else lower_bound > materiality
        ),
    }


def run(output):
    output.mkdir(parents=True,exist_ok=True)
    d=date(2026,1,5)
    previous=date(2026,1,2)
    cutoff=datetime(2026,1,5,21,tzinfo=timezone.utc)
    a=dict(key='A',oi=10,iv=D('.2'),date=d,timestamp=cutoff,deliverable='documented')
    b=dict(a,key='B')
    checks=[
        ('baseline',[a,b],{},'VALID'),('empty',[],{},'UNAVAILABLE'),
        ('missing_iv',[dict(a,iv=None),b],{},'PARTIAL'),
        ('duplicate',[a,a,b],{},'INCONSISTENT'),
        ('string_oi',[dict(a,oi='10'),b],{},'INVALID'),
        ('negative_oi',[dict(a,oi=-1),b],{},'INVALID'),
        ('nonfinite_iv',[dict(a,iv=D('NaN')),b],{},'INVALID'),
        ('mixed_dates',[dict(a,date=previous),b],{},'INCONSISTENT'),
        ('stale',[dict(a,date=previous),dict(b,date=previous)],{},'STALE'),
        ('future',[dict(a,date=date(2026,1,6)),b],{},'INVALID'),
        ('bad_units',[a,b],{'units':'unmapped'},'INVALID'),
        ('incomplete',[a],{},'PARTIAL'),
        ('no_reference',[a,b],{'expected':None},'UNVERIFIED'),
        ('deliverable_missing',[dict(a,deliverable=None),b],{},'UNVERIFIED'),
        ('corporate_action_bad_mapping',[dict(a,deliverable='invalid'),b],{},'INVALID'),
        ('zero_unknown',[dict(a,iv=D(0)),b],{},'UNVERIFIED'),
        ('zero_measured',[dict(a,iv=D(0),zero_origin='measured'),b],{},'VALID'),
        ('zero_rounded',[dict(a,iv=D(0),zero_origin='rounded'),b],{},'VALID'),
        ('zero_placeholder',[dict(a,iv=D(0),zero_origin='placeholder'),b],{},'PARTIAL'),
        ('method_missing',[a,b],{'method':None},'UNVERIFIED'),
        ('silent_revision',[dict(a,oi=11),b],{'prior':[a,b]},'INCONSISTENT'),
        ('naive_timestamp',[dict(a,timestamp=cutoff.replace(tzinfo=None)),b],{},'INVALID'),
    ]
    cases=[];results=[]
    for name,rows,kw,want in checks:
        got,reason=classify(rows,day=d,cutoff=cutoff,**({'expected':{'A','B'}}|kw))
        cases.append((name,want,repr(rows),repr(kw)))
        results.append((name,want,got,got==want,reason))
    for name,x,y,qx,qy,want in [
        ('rounding_overlap','1.23','1.234','.01','.001',True),
        ('rounding_incompatible','1.23','1.24','.01','.001',False),
        ('near_zero_relative','0','.001','.01','.001',True),
        ('aggregate_net_identity','1.2','1.3','.02','.01',False),
    ]:
        x,y,qx,qy=map(D,(x,y,qx,qy))
        dif,rel,got=reconcile(x,y,qx,qy,(qx+qy)/2)
        cases.append((name,want,repr((x,y,qx,qy)),'nearest rounding assumed'))
        results.append((name,want,got,got==want,f'delta={dif}; relative={rel}'))
    got=D('25')/100==D('.25')
    cases.append(('explicit_percent_conversion',True,'25 percent','divide by 100'))
    results.append(('explicit_percent_conversion',True,got,got,'25% = 0.25'))
    write(output/'SYNTHETIC_TEST_CASES.csv',['case','expected','inputs','parameters'],cases)
    write(output/'EXPERIMENT_RESULTS.csv',['case','expected','observed','pass','evidence'],results)
    if not all(x[3] for x in results):
        raise AssertionError('Synthetic checks failed')
    return len(results)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Offline synthetic quality checks')
    p.add_argument('--out', type=Path, default=Path('.'))
    args = p.parse_args()
    print(f'{run(args.out)} synthetic checks passed')
