"""Additive SQLite migration. Independent deletion ledger survives main-DB restoration."""
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException
from ..db import connect, database_path


def now():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or now()).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def ledger_path():
    return Path(os.getenv('M7_DELETION_LEDGER_PATH', str(database_path()) + '.m7-deletions.sqlite3'))


@contextmanager
def ledger(create=False):
    path = ledger_path()
    if not create and not path.exists():
        raise HTTPException(503, detail='记忆删除台账缺失，已停止读取；请先完成恢复校验。')
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_memory():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS m7_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversation_sessions (
          id TEXT PRIMARY KEY, owner_user_id TEXT, guest_token_hash TEXT NOT NULL UNIQUE,
          active_trip_id TEXT, summary TEXT NOT NULL DEFAULT '', summary_version INTEGER NOT NULL DEFAULT 0,
          summary_sources TEXT NOT NULL DEFAULT '[]', history_enabled INTEGER NOT NULL DEFAULT 1,
          last_activity_at TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_messages (
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
          role TEXT NOT NULL CHECK(role IN ('user','assistant')), content_redacted TEXT NOT NULL,
          turn_id TEXT NOT NULL, sequence INTEGER NOT NULL, channel TEXT NOT NULL,
          created_at TEXT NOT NULL, expires_at TEXT NOT NULL, UNIQUE(session_id,turn_id,role)
        );
        CREATE INDEX IF NOT EXISTS m7_messages_session ON conversation_messages(session_id,sequence);
        CREATE TABLE IF NOT EXISTS trip_states (
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
          status TEXT NOT NULL, interview_json TEXT NOT NULL, constraints_json TEXT,
          excluded_ids TEXT NOT NULL DEFAULT '[]', completed_ids TEXT NOT NULL DEFAULT '[]',
          route_id TEXT, route_version INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL, ended_at TEXT
        );
        CREATE INDEX IF NOT EXISTS m7_trips_owner ON trip_states(session_id,updated_at);
        CREATE TABLE IF NOT EXISTS trip_events (
          id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trip_states(id) ON DELETE CASCADE,
          request_id TEXT NOT NULL, event_type TEXT NOT NULL, actor TEXT NOT NULL,
          before_version INTEGER NOT NULL, after_version INTEGER NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(trip_id,request_id)
        );
        CREATE TABLE IF NOT EXISTS m7_receipts (
          session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
          request_id TEXT NOT NULL, scope TEXT NOT NULL, body_hash TEXT NOT NULL,
          result_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(session_id,request_id)
        );
        CREATE TABLE IF NOT EXISTS m7_plan_owners (
          plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
          trip_id TEXT REFERENCES trip_states(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS m7_proposals (
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
          trip_id TEXT NOT NULL REFERENCES trip_states(id) ON DELETE CASCADE, trip_version INTEGER NOT NULL,
          route_version INTEGER NOT NULL, candidate_json TEXT NOT NULL, source_turn_id TEXT NOT NULL,
          expires_at TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS user_preferences (
          id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, key TEXT NOT NULL,
          value_json TEXT NOT NULL, source TEXT NOT NULL, confirmed_at TEXT NOT NULL,
          consent_record_id TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
          UNIQUE(owner_user_id,key)
        );
        CREATE TABLE IF NOT EXISTS saved_trips (
          id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, trip_id TEXT NOT NULL,
          snapshot_json TEXT NOT NULL, saved_at TEXT NOT NULL, deleted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS consent_records (
          id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, scope TEXT NOT NULL,
          granted_at TEXT NOT NULL, revoked_at TEXT, policy_version TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_deletion_jobs (
          id TEXT PRIMARY KEY, subject_hash TEXT NOT NULL, requested_at TEXT NOT NULL,
          status TEXT NOT NULL, purged_at TEXT, backup_policy_applied TEXT NOT NULL
        );
        ''')
        marker = db.execute("SELECT value FROM m7_metadata WHERE key='ledger_id'").fetchone()
        if not marker:
            with ledger(create=True) as journal:
                journal.executescript('''
                CREATE TABLE IF NOT EXISTS metadata (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS deletions (
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE, requested_at TEXT NOT NULL,
                  status TEXT NOT NULL, purged_at TEXT, request_id TEXT, token_hash TEXT
                );
                ''')
                identity = journal.execute('SELECT id FROM metadata').fetchone()
                value = identity['id'] if identity else str(uuid.uuid4())
                journal.execute('INSERT OR IGNORE INTO metadata VALUES (?)', (value,))
            db.execute("INSERT INTO m7_metadata VALUES ('ledger_id',?)", (value,))
        if ledger_path().exists():
            with ledger() as journal:
                columns={r[1] for r in journal.execute('PRAGMA table_info(deletions)')}
                for column in ('request_id','token_hash'):
                    if column not in columns:journal.execute(f'ALTER TABLE deletions ADD COLUMN {column} TEXT')
        from ..city.storage import init_city
        init_city(db)
    # Never recreate a missing ledger for a restored database; reads fail closed.


def check_ledger(db, session_id=None):
    marker = db.execute("SELECT value FROM m7_metadata WHERE key='ledger_id'").fetchone()
    with ledger() as journal:
        identity = journal.execute('SELECT id FROM metadata').fetchone()
        if not marker or not identity or marker['value'] != identity['id']:
            raise HTTPException(503, detail='记忆删除台账与数据库不匹配，已停止读取。')
        if session_id and journal.execute('SELECT 1 FROM deletions WHERE session_id=?', (session_id,)).fetchone():
            raise HTTPException(401, detail='本次记录已删除，请重新开始会话。')


def purge_session(db, session_id):
    from ..city.agent.service import purge
    purge(session_id)
    plans = [r[0] for r in db.execute('SELECT plan_id FROM m7_plan_owners WHERE session_id=?', (session_id,))]
    db.execute('DELETE FROM conversation_sessions WHERE id=?', (session_id,))
    for plan_id in plans:
        db.execute('DELETE FROM plans WHERE id=?', (plan_id,))


def maintenance():
    """Also serves as a restore gate: replay independent tombstones before serving restored data."""
    counts = {'sessions': 0, 'trips': 0, 'deletions_replayed': 0}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        check_ledger(db)
        with ledger() as journal:
            for job in journal.execute('SELECT * FROM deletions').fetchall():
                purge_session(db, job['session_id'])
                job_time = stamp()
                db.execute('INSERT OR REPLACE INTO memory_deletion_jobs VALUES (?,?,?,?,?,?)',
                           (job['id'], digest(job['session_id']), job['requested_at'], 'online_purged', job_time,
                            'restore_ledger_applied; backup_physical_expiry_pending'))
                journal.execute("UPDATE deletions SET status='online_purged',purged_at=? WHERE id=?", (job_time,job['id']))
                counts['deletions_replayed'] += 1
        moment = stamp()
        for row in db.execute('SELECT id FROM conversation_sessions WHERE expires_at<=?', (moment,)).fetchall():
            purge_session(db,row['id']); counts['sessions'] += 1
        from datetime import timedelta
        threshold = stamp(now()-timedelta(hours=72))
        for row in db.execute('SELECT id FROM trip_states WHERE ended_at<=?', (threshold,)).fetchall():
            plans = [r[0] for r in db.execute('SELECT plan_id FROM m7_plan_owners WHERE trip_id=?',(row['id'],))]
            db.execute('UPDATE conversation_sessions SET active_trip_id=NULL WHERE active_trip_id=?',(row['id'],))
            db.execute('DELETE FROM trip_states WHERE id=?',(row['id'],))
            for p in plans: db.execute('DELETE FROM plans WHERE id=?',(p,))
            counts['trips'] += 1
        db.execute('DELETE FROM conversation_messages WHERE expires_at<=?', (moment,))
        db.execute('DELETE FROM m7_proposals WHERE expires_at<=?', (moment,))
        # Summaries are projections: invalidate if a referenced message expired.
        for s in db.execute("SELECT id,summary_sources FROM conversation_sessions WHERE summary!=''").fetchall():
            ids = json.loads(s['summary_sources'])
            if any(not db.execute('SELECT 1 FROM conversation_messages WHERE id=?',(i,)).fetchone() for i in ids):
                db.execute("UPDATE conversation_sessions SET summary='',summary_sources='[]',summary_version=0 WHERE id=?",(s['id'],))
        db.execute('DELETE FROM trip_events WHERE created_at<?', (stamp(now()-timedelta(days=30)),))
        from ..city.storage import maintain_city
        counts['city_trips'] = maintain_city(db,moment,threshold,stamp(now()-timedelta(days=30)))
    return counts
