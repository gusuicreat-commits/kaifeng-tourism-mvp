"""Additive city state migration. All visitor objects cascade from the M7 session."""
from .facts import init_catalog


def init_city(db):
    init_catalog(db)
    columns = {r[1] for r in db.execute('PRAGMA table_info(conversation_sessions)')}
    if 'active_city_trip_id' not in columns:
        db.execute('ALTER TABLE conversation_sessions ADD COLUMN active_city_trip_id TEXT')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS city_trip_states (
      id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
      status TEXT NOT NULL, draft_json TEXT NOT NULL, anchor_json TEXT,
      version INTEGER NOT NULL DEFAULT 1, route_version INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL, ended_at TEXT
    );
    CREATE INDEX IF NOT EXISTS city_trips_owner ON city_trip_states(session_id,updated_at);
    CREATE TABLE IF NOT EXISTS city_trip_events (
      id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES city_trip_states(id) ON DELETE CASCADE,
      request_id TEXT NOT NULL, event_type TEXT NOT NULL, before_version INTEGER NOT NULL,
      after_version INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(trip_id,request_id)
    );
    CREATE TABLE IF NOT EXISTS city_proposals (
      id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
      trip_id TEXT NOT NULL REFERENCES city_trip_states(id) ON DELETE CASCADE,
      trip_version INTEGER NOT NULL, action_json TEXT NOT NULL, fact_revision TEXT NOT NULL,
      expires_at TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS city_receipts (
      session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
      request_id TEXT NOT NULL, trip_id TEXT NOT NULL REFERENCES city_trip_states(id) ON DELETE CASCADE,
      scope TEXT NOT NULL, body_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY(session_id,request_id)
    );
    ''')

    from .engine.storage import init_engine
    init_engine(db)
    from .travel_services import init as init_services
    init_services(db)
    from .basket import init_basket
    init_basket(db)

def maintain_city(db, moment, ended_before, events_before):
    ids = [r[0] for r in db.execute('SELECT id FROM city_trip_states WHERE ended_at<=?', (ended_before,))]
    for identity in ids:
        db.execute('UPDATE conversation_sessions SET active_city_trip_id=NULL WHERE active_city_trip_id=?', (identity,))
        db.execute('DELETE FROM city_trip_states WHERE id=?', (identity,))
    db.execute('DELETE FROM city_proposals WHERE expires_at<=?', (moment,))
    db.execute('DELETE FROM city_engine_candidates WHERE expires_at<=?', (moment,))
    db.execute('DELETE FROM city_trip_events WHERE created_at<?', (events_before,))
    return len(ids)
