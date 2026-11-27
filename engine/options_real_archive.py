"""Separate, explicit opt-in SQLite archive for historical pilot observations."""
from collections import Counter
from hashlib import sha256
import json

from engine.omr_archive_demo import connect, revision_decision
from engine.options_real import SCHEMA
from engine.options_real_provider import PILOT


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def save(path, report, buckets, *, raw=None):
    if report['availability'] != 'AVAILABLE':
        return {'UNAVAILABLE': 1}
    digest = report['source_sha256']
    if raw is not None and sha256(raw).hexdigest() != digest:
        raise ValueError('raw_payload_hash_mismatch')
    db = connect(path)
    outcomes = Counter()
    try:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS option_reports (
                digest TEXT PRIMARY KEY, payload TEXT NOT NULL, raw BLOB);
            CREATE TABLE IF NOT EXISTS option_observations (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, ticker TEXT NOT NULL,
                day TEXT NOT NULL, contract TEXT NOT NULL, fingerprint TEXT NOT NULL,
                revision TEXT, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS option_observation_key
                ON option_observations(source,ticker,day,contract,id);
        ''')
        db.execute('BEGIN IMMEDIATE')
        with db:
            for ticker, rows in buckets.items():
                for row in rows:
                    if row['schema'] != SCHEMA or ticker not in PILOT or row['underlying_ticker'] != ticker:
                        raise ValueError('invalid_archive_row')
                    if row['quality_status'] in ('INVALID', 'INCONSISTENT'):
                        outcomes['QUALITY_REJECTED'] += 1
                        continue
                    key = (row['source_identifier'], ticker, row['observation_date'], row['contract_identifier'])
                    version = row['source_version']
                    if version is not None and (type(version) is not int or version <= 0):
                        raise ValueError('invalid_source_version')
                    core = {k:v for k,v in row.items() if k not in (
                        'retrieval_timestamp','source_sha256','source_version','snapshot_recency',
                        'quality_reason','quality_status','missing_fields','quote_freshness','oi_freshness')}
                    fingerprint = sha256(dump(core).encode()).hexdigest()
                    prior = db.execute('''
                        SELECT fingerprint,revision FROM option_observations
                        WHERE source=? AND ticker=? AND day=? AND contract=?
                        ORDER BY id DESC LIMIT 1''', key).fetchone()
                    outcome = 'ACCEPT' if prior is None else revision_decision(
                        int(prior[1]) if prior[1] is not None else None, version, prior[0] == fingerprint)
                    outcomes[outcome] += 1
                    if outcome == 'ACCEPT':
                        db.execute('''INSERT INTO option_observations
                            (source,ticker,day,contract,fingerprint,revision,payload) VALUES (?,?,?,?,?,?,?)''',
                                   (*key, fingerprint, str(version) if version is not None else None, dump(row)))
            payload = dict(report, archive_write=dict(outcomes))
            db.execute('INSERT OR IGNORE INTO option_reports VALUES (?,?,?)', (digest, dump(payload), raw))
            if raw is not None:
                db.execute('UPDATE option_reports SET raw=? WHERE digest=? AND raw IS NULL', (raw, digest))
    finally:
        db.close()
    return dict(outcomes)

