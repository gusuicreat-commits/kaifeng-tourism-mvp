import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def database_path():
    return Path(os.getenv('DATABASE_PATH', str(ROOT / 'data' / 'tourism.sqlite3')))


@contextmanager
def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO schema_version VALUES (1);
        CREATE TABLE IF NOT EXISTS scenic (
            id TEXT PRIMARY KEY, content TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS networks (
            scenic_id TEXT PRIMARY KEY REFERENCES scenic(id), geojson TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY,
            scenic_id TEXT NOT NULL REFERENCES scenic(id), node_id TEXT NOT NULL,
            observed_at TEXT NOT NULL, period_end TEXT,
            count INTEGER, crowd_level REAL, capacity INTEGER,
            source_type TEXT NOT NULL, source_name TEXT NOT NULL, source_url TEXT,
            measurement_kind TEXT NOT NULL, updated_at TEXT NOT NULL,
            confidence REAL NOT NULL, is_simulated INTEGER NOT NULL,
            UNIQUE(scenic_id,node_id,observed_at,source_type,source_name,measurement_kind,is_simulated),
            CHECK(count IS NULL OR count >= 0),
            CHECK(crowd_level IS NULL OR (crowd_level >= 0 AND crowd_level <= 1)),
            CHECK(confidence >= 0 AND confidence <= 1),
            CHECK(is_simulated IN (0,1))
        );
        CREATE INDEX IF NOT EXISTS observations_lookup ON observations(scenic_id,node_id,observed_at);
        CREATE TABLE IF NOT EXISTS schedule_facts (
            id TEXT PRIMARY KEY, scenic_id TEXT NOT NULL REFERENCES scenic(id),
            content TEXT NOT NULL
        );
        ''')


def scenic_list(q=''):
    with connect() as db:
        items = [json.loads(r['content']) for r in db.execute('SELECT content FROM scenic ORDER BY rowid')]
    return [s for s in items if q.casefold() in s['name'].casefold()]


def scenic_get(scenic_id):
    with connect() as db:
        row = db.execute('SELECT content FROM scenic WHERE id=?', (scenic_id,)).fetchone()
    return json.loads(row['content']) if row else None


def network_get(scenic_id):
    with connect() as db:
        row = db.execute('SELECT geojson FROM networks WHERE scenic_id=?', (scenic_id,)).fetchone()
    return json.loads(row['geojson']) if row else None
