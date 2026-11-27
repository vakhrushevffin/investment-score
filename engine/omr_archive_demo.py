"""Offline synthetic archive study. Python standard library only."""
import csv
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from time import perf_counter
from zoneinfo import ZoneInfo

UTC=timezone.utc
NY=ZoneInfo('America/New_York')
HOLIDAYS={date.fromisoformat(s) for s in
          ('2025-01-20','2025-02-17','2025-04-18','2025-05-26','2025-06-19','2025-07-04','2025-09-01','2025-11-27','2025-12-25','2026-01-01')}
SHORT={date(2025,7,3),date(2025,11,28),date(2025,12,24)}
FIELDS={'key','session','value','observed','published','retrieved','source','schema'}
KEYS={f'SYN-{i:03}' for i in range(104)}
SOURCE='SYNTHETIC-V1'


def utc(dt):
    return dt.astimezone(UTC).isoformat()


def parse(text):
    dt=datetime.fromisoformat(text)
    if dt.utcoffset() is None:
        raise ValueError('timezone missing')
    return dt.astimezone(UTC)


def calendar():
    result={}
    day=date(2025,1,2)
    while len(result)<252:
        if day.weekday()<5 and day not in HOLIDAYS:
            result[str(day)]=datetime.combine(day,time(13 if day in SHORT else 16),NY).astimezone(UTC)
        day+=timedelta(days=1)
    return result


def seed(cal):
    for j,(session,close) in enumerate(cal.items()):
        for i,key in enumerate(sorted(KEYS)):
            yield dict(key=key,session=session,value=100000+j*104+i,
                       observed=utc(close),published=utc(close+timedelta(minutes=30)),
                       retrieved=utc(close+timedelta(minutes=35)),source=SOURCE,schema=1,source_version=1)


def inspect(row,cal,known):
    if set(row)-{'source_version'} != FIELDS or type(row['schema']) is not int or row['schema'] != 1:
        return 'INVALID'
    if 'source_version' in row and (type(row['source_version']) is not int or row['source_version'] < 1):
        return 'INVALID'
    if not isinstance(row['key'], str) or not isinstance(row['session'], str) or row['key'] not in KEYS or row['source']!=SOURCE or type(row['value']) is not int:
        return 'INVALID'
    try:
        observed,published,retrieved=(parse(row[k]) for k in ('observed','published','retrieved'))
    except (TypeError,ValueError):
        return 'INVALID'
    close=cal.get(row['session'])
    if close is None or observed!=close or not observed<=published<=retrieved<=known:
        return 'INVALID'
    return 'VALID'


def connect(path):
    db=sqlite3.connect(path)
    db.execute('PRAGMA synchronous=FULL')
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init(db):
    db.executescript('''
    CREATE TABLE batches(id INTEGER PRIMARY KEY, known TEXT NOT NULL);
    CREATE TABLE rows(session TEXT,key TEXT,batch INTEGER REFERENCES batches(id),
                      payload TEXT NOT NULL,sha TEXT NOT NULL,
                      PRIMARY KEY(session,key,batch));
    ''')


def pack(row):
    # Version order and receipt time are independent of the publication content.
    payload=json.dumps(row,sort_keys=True,separators=(',',':'))
    core=json.dumps({k:v for k,v in row.items() if k not in ('retrieved', 'source_version')},sort_keys=True,separators=(',',':'))
    return payload,hashlib.sha256(core.encode()).hexdigest()


def revision_decision(previous, incoming, same_content):
    """Order revisions by a trusted source version, never by receipt time."""
    if previous is None or incoming is None:
        return 'UNCHANGED' if same_content else 'UNVERIFIED'
    if incoming < previous:
        return 'STALE'
    if incoming == previous:
        return 'UNCHANGED' if same_content else 'INCONSISTENT'
    return 'ACCEPT'


def ingest(db,rows,cal,known):
    if not rows:
        return 0,0,'UNAVAILABLE'
    status=[inspect(row,cal,known) for row in rows]
    if any(flag!='VALID' for flag in status):
        return 0,0,'INVALID'
    keys=[(r['session'],r['key']) for r in rows]
    if len(set(keys))!=len(keys):
        return 0,0,'INCONSISTENT'
    previous=db.execute('SELECT max(known) FROM batches').fetchone()[0]
    if previous and utc(known)<previous:
        return 0,0,'INVALID'
    # Resolve the entire batch before writing. One ambiguous ordering rejects
    # the batch, so unrelated observations cannot commit beside a conflict.
    pending = []
    for row in rows:
        payload, sha = pack(row)
        old = db.execute('SELECT payload FROM rows WHERE session=? AND key=? ORDER BY batch DESC LIMIT 1',
                         (row['session'], row['key'])).fetchone()
        if old is not None:
            old_row = json.loads(old[0])
            _, old_sha = pack(old_row)
            previous_version = old_row.get('source_version')
            version = row.get('source_version')
            decision = revision_decision(previous_version, version, sha == old_sha)
            if decision == 'UNCHANGED':
                continue
            if decision != 'ACCEPT':
                return 0, 0, decision
        pending.append((row['session'], row['key'], payload, sha, old is None))

    if not pending:
        return 0, 0, 'VALID'
    with db:
        batch = db.execute('INSERT INTO batches(known) VALUES (?)', (utc(known),)).lastrowid
        db.executemany('INSERT INTO rows VALUES (?,?,?,?,?)',
                       ((session, key, batch, payload, sha) for session, key, payload, sha, _ in pending))
    new = sum(item[-1] for item in pending)
    return new, len(pending)-new, 'VALID'


def snapshot(db,cutoff):
    return db.execute('''
    SELECT session,key,payload FROM (
        SELECT session,key,payload,row_number() OVER(PARTITION BY session,key ORDER BY batch DESC) AS n
        FROM rows JOIN batches ON rows.batch=batches.id WHERE known<=?
    ) WHERE n=1 ORDER BY session,key
    ''',(utc(cutoff),)).fetchall()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def csvfile(path,header,rows):
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f);w.writerow(header);w.writerows(rows)


def run(out):
    out.mkdir(parents=True,exist_ok=True)
    cal=calendar()
    full=list(seed(cal))
    baseline=max(cal.values())+timedelta(hours=1)
    later=baseline+timedelta(days=1)
    results=[]; bench=[]
    def test(name,expected,fn):
        t=perf_counter()
        value=fn()
        results.append((name,str(expected),str(value),value==expected,round(perf_counter()-t,6)))
        if value!=expected:
            raise AssertionError((name,expected,value))

    with tempfile.TemporaryDirectory() as tmp:
        work=Path(tmp)
        path=work/'study.db'
        db=connect(path);init(db)
        # One synthetic batch per completed session. Knowledge time is commit
        # time in the deterministic simulation, not actual wall-clock time.
        t=perf_counter()
        for i,close in enumerate(cal.values()):
            assert ingest(db,full[i*104:(i+1)*104],cal,close+timedelta(minutes=45))==(104,0,'VALID')
        bench.append(('baseline_daily_ingest',len(full),path.stat().st_size,perf_counter()-t))
        t=perf_counter()
        before=snapshot(db,baseline)
        assert len(before)==26208
        bench.append(('snapshot_read',len(before),path.stat().st_size,perf_counter()-t))

        def replay():
            replayed=[dict(r,retrieved=utc(later)) for r in full]
            value=ingest(db,replayed,cal,later)
            return value,snapshot(db,later)==before
        test('F01_replay',((0,0,'VALID'),True),replay)

        corr=dict(full[0],value=-7,source_version=2,published=utc(later-timedelta(minutes=5)),retrieved=utc(later))
        def revision():
            verdict=ingest(db,[corr],cal,later)
            return verdict,snapshot(db,baseline)==before,json.loads(snapshot(db,later)[0][2])['value']
        test('F02_revision',((0,1,'VALID'),True,-7),revision)

        def coverage():
            partial=connect(':memory:')
            init(partial)
            close=next(iter(cal.values()))
            ingest(partial,full[:101],cal,close+timedelta(hours=1))
            missing=KEYS-{key for _,key,_ in snapshot(partial,baseline)}
            partial.close()
            return len(missing),'PARTIAL' if missing else 'VALID'
        test('F03_gap',(3,'PARTIAL'),coverage)

        def late():
            r=full[0]
            expected=cal[r['session']]+timedelta(minutes=15)
            earlier=len(snapshot(db,expected))
            return parse(r['published'])>expected,earlier,len(snapshot(db,cal[r['session']]+timedelta(minutes=45)))
        test('F04_late',(True,0,104),late)

        def clocks():
            row=full[0]
            variants=[dict(row,observed=row['observed'][:19]),dict(row,published=utc(cal[row['session']]-timedelta(minutes=1))),
                      dict(row,observed=utc(cal[row['session']]+timedelta(hours=1))),
                      dict(row,retrieved=utc(later+timedelta(seconds=1))),dict(row,session='2025-11-27')]
            return tuple(ingest(db,[r],cal,later)[2] for r in variants)
        test('F05_clocks',('INVALID',)*5,clocks)
        test('T_calendar',(252,True,True,21,20,20,21,18),
             lambda:(len(cal),'2025-11-27' not in cal,'2025-11-29' not in cal,
                     *(cal[d].hour for d in ('2025-03-07','2025-03-10','2025-10-31','2025-11-03','2025-11-28'))))

        precrash=snapshot(db,later)
        crash_time=later+timedelta(hours=1)
        def crash():
            db.close()
            child=subprocess.run([sys.executable,__file__,'--crash',str(path),utc(crash_time)],capture_output=True)
            assert path.with_name(path.name+'-journal').exists()
            with connect(path) as reopened:
                return child.returncode,b'DIRTY' in child.stdout,snapshot(reopened,crash_time)==precrash,reopened.execute('PRAGMA integrity_check').fetchone()[0]
        test('F06_interrupt',(73,True,True,'ok'),crash)
        db=connect(path)

        backup=work/'backup.db'
        t=perf_counter()
        dest=connect(backup)
        db.backup(dest)
        assert dest.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        dest.close()
        backup_hash=digest(backup)
        bench.append(('verified_backup',len(precrash),backup.stat().st_size,perf_counter()-t))
        def corruption():
            damaged=work/'damaged.db'
            data=bytearray(backup.read_bytes());data[0]^=1
            damaged.write_bytes(data)
            try:
                with connect(damaged) as fd:
                    fd.execute('PRAGMA integrity_check').fetchall()
            except sqlite3.DatabaseError:
                return digest(damaged)!=backup_hash,True
            return digest(damaged)!=backup_hash,False
        test('F07_corruption',(True,True),corruption)

        retry=dict(full[1],value=-8,source_version=2,published=utc(crash_time),retrieved=utc(crash_time))
        test('F08_retry',((0,1,'VALID'),(0,0,'VALID')),lambda:(
            ingest(db,[retry],cal,crash_time),ingest(db,[retry],cal,crash_time)))
        preschema=snapshot(db,crash_time)
        test('F09_schema',((0,0,'INVALID'),True),lambda:(
            ingest(db,[dict(full[2],schema=2,extra=1)],cal,crash_time),snapshot(db,crash_time)==preschema))

        def restore():
            verified=digest(backup)==backup_hash
            candidate=work/'restore.tmp'
            candidate.write_bytes(backup.read_bytes())
            restored=work/'restored.db'
            os.replace(candidate,restored)
            with connect(restored) as check:
                return verified,snapshot(check,crash_time)==precrash,snapshot(check,baseline)==before
        test('F10_restore',(True,True,True),restore)
        t=perf_counter()
        present=snapshot(db,crash_time)
        bench.append(('final_db',len(present),path.stat().st_size,perf_counter()-t))
        exported=work/'reference.jsonl'
        t=perf_counter()
        exported.write_text(''.join(payload+'\n' for _,_,payload in present))
        bench.append(('current_snapshot_jsonl_export',len(present),exported.stat().st_size,perf_counter()-t))
        revisions=db.execute('SELECT count(*) FROM rows').fetchone()[0]
        manifest={'snapshot_sha256':digest(exported),'snapshot_rows':len(present),'physical_rows':revisions,
                  'backup_sha256':backup_hash,'sessions':len(cal),'first_session':next(iter(cal)),
                  'last_session':next(reversed(cal)),'cutoff_utc':utc(crash_time),
                  'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version}
        (out/'RUN_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
        db.close()
    csvfile(out/'EXPERIMENT_RESULTS.csv',['experiment','expected','actual','passed','seconds'],results)
    csvfile(out/'STORAGE_BENCHMARK.csv',['measurement','rows','bytes','seconds'],bench)
    csvfile(out/'SYNTHETIC_CALENDAR.csv',['session','close_utc','short'],[(d,utc(t),date.fromisoformat(d) in SHORT) for d,t in cal.items()])
    return results


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--crash':
        db=connect(sys.argv[2])
        db.execute('PRAGMA cache_size=4')
        with db:
            batch=db.execute('INSERT INTO batches(known) VALUES (?)',(sys.argv[3],)).lastrowid
            db.executemany('INSERT INTO rows VALUES (?,?,?,?,?)',
                           [('2099-01-01',f'dirty{i}',batch,'X'*4000,'dirty') for i in range(128)])
            print('DIRTY',flush=True)
            os._exit(73)
    run(Path(sys.argv[1]) if len(sys.argv)>1 else Path('.'))
