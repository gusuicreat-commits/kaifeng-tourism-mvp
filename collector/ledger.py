"""Engineering ledger for the Kaifeng collection toolkit.

SQLite file data/collection.db holds five tables: source_registry, collect_task,
collect_run, collect_failure and verify_task. These are engineering metadata
only. They never store city-facts content and never write to the runtime fact
store data/tourism.sqlite3.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from .config import DEFAULT_DB_PATH, DEFAULT_RUNS_DIR

TZ = ZoneInfo("Asia/Shanghai")

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_registry (
  source_key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT,
  publisher TEXT,
  source_type TEXT NOT NULL,
  access_kind TEXT NOT NULL DEFAULT 'platform',
  access_status TEXT NOT NULL DEFAULT 'reachable',
  first_seen_at TEXT NOT NULL,
  last_checked_at TEXT,
  content_sha256 TEXT,
  raw_path TEXT,
  license_status TEXT NOT NULL DEFAULT 'unknown',
  notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_registry_url ON source_registry(url);

CREATE TABLE IF NOT EXISTS collect_task (
  task_id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  scope_district TEXT,
  objective TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  priority TEXT NOT NULL DEFAULT 'normal',
  created_by TEXT NOT NULL DEFAULT 'collector',
  created_at TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS collect_run (
  run_id TEXT PRIMARY KEY,
  task_id TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  params_json TEXT,
  items_fetched INTEGER NOT NULL DEFAULT 0,
  items_new INTEGER NOT NULL DEFAULT 0,
  items_changed INTEGER NOT NULL DEFAULT 0,
  items_failed INTEGER NOT NULL DEFAULT 0,
  output_dir TEXT,
  report_path TEXT,
  error_summary TEXT
);

CREATE TABLE IF NOT EXISTS collect_failure (
  failure_id TEXT PRIMARY KEY,
  run_id TEXT,
  source_key TEXT,
  failure_kind TEXT NOT NULL,
  detail TEXT,
  occurred_at TEXT NOT NULL,
  retry_of TEXT
);

CREATE TABLE IF NOT EXISTS verify_task (
  verify_id TEXT PRIMARY KEY,
  fact_ref TEXT NOT NULL,
  check_item TEXT NOT NULL,
  method_required TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  reviewer TEXT,
  verified_at TEXT,
  note TEXT,
  created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Create the ledger schema. Idempotent; never touches the runtime fact store."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript(SCHEMA)
    return path


@contextmanager
def connect(db_path: Path | str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(db: sqlite3.Connection, sql: str, params: Iterable = ()) -> list[dict]:
    return [dict(r) for r in db.execute(sql, tuple(params))]


def one(db: sqlite3.Connection, sql: str, params: Iterable = ()) -> dict | None:
    result = db.execute(sql, tuple(params)).fetchone()
    return dict(result) if result else None


# ---------------------------------------------------------------- source registry

def upsert_source(db: sqlite3.Connection, *, source_key: str, name: str, source_type: str,
                  url: str | None = None, publisher: str | None = None,
                  access_kind: str = "platform", access_status: str = "reachable",
                  content_sha256: str | None = None, raw_path: str | None = None,
                  license_status: str = "unknown", notes: str | None = None,
                  last_checked_at: str | None = None) -> str:
    """Register or refresh a source. Returns the stable source_key."""
    existing = one(db, "SELECT * FROM source_registry WHERE source_key=?", (source_key,))
    stamp = now_iso()
    if existing:
        db.execute("""UPDATE source_registry SET name=?, url=?, publisher=?, source_type=?,
                       access_kind=?, access_status=?, last_checked_at=?, content_sha256=?,
                       raw_path=?, license_status=?, notes=? WHERE source_key=?""",
                   (name, url, publisher, source_type, access_kind, access_status,
                    last_checked_at or stamp, content_sha256 or existing["content_sha256"],
                    raw_path or existing["raw_path"], license_status, notes, source_key))
        return source_key
    db.execute("""INSERT INTO source_registry (source_key, name, url, publisher, source_type,
                   access_kind, access_status, first_seen_at, last_checked_at, content_sha256,
                   raw_path, license_status, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (source_key, name, url, publisher, source_type, access_kind, access_status,
                stamp, last_checked_at or stamp, content_sha256, raw_path, license_status, notes))
    return source_key


def list_sources(db: sqlite3.Connection) -> list[dict]:
    return rows(db, "SELECT * FROM source_registry ORDER BY first_seen_at")


def source_keys(db: sqlite3.Connection) -> set[str]:
    return {r["source_key"] for r in rows(db, "SELECT source_key FROM source_registry")}


def source_by_url(db: sqlite3.Connection, url: str) -> dict | None:
    return one(db, "SELECT * FROM source_registry WHERE url=? ORDER BY first_seen_at LIMIT 1", (url,))


# ------------------------------------------------------------------ collect tasks

def create_task(db: sqlite3.Connection, *, category: str, objective: str,
                scope_district: str | None = None, priority: str = "normal",
                created_by: str = "collector") -> str:
    task_id = new_id("task")
    db.execute("""INSERT INTO collect_task (task_id, category, scope_district, objective, status,
                   priority, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
               (task_id, category, scope_district, objective, "pending", priority, created_by,
                now_iso(), now_iso()))
    return task_id


def update_task_status(db: sqlite3.Connection, task_id: str, status: str) -> None:
    db.execute("UPDATE collect_task SET status=?, updated_at=? WHERE task_id=?",
               (status, now_iso(), task_id))


def list_tasks(db: sqlite3.Connection) -> list[dict]:
    return rows(db, "SELECT * FROM collect_task ORDER BY created_at")


# -------------------------------------------------------------------- collect runs

def start_run(db: sqlite3.Connection, *, task_id: str | None = None, params: dict | None = None,
              output_dir: Path | str | None = None) -> str:
    run_id = new_id("run")
    import json
    # output_dir 必须指向本次运行真实使用的 runs_dir；默认值是仓库级目录，
    # 调用方用临时目录时要显式传入，否则台账会记一个不存在的路径。
    directory = Path(output_dir) / run_id if output_dir is not None else (DEFAULT_RUNS_DIR / run_id)
    db.execute("""INSERT INTO collect_run (run_id, task_id, started_at, status, params_json, output_dir)
                   VALUES (?,?,?,?,?,?)""",
               (run_id, task_id, now_iso(), "running", json.dumps(params or {}, ensure_ascii=False),
                str(directory)))
    return run_id


def finish_run(db: sqlite3.Connection, run_id: str, *, status: str = "success",
               items_fetched: int = 0, items_new: int = 0, items_changed: int = 0,
               items_failed: int = 0, report_path: str | None = None,
               error_summary: str | None = None) -> None:
    """Close a run. Silent when the run_id is unknown: report-only runs must be
    registered first with ensure_run(), otherwise the ledger would drift."""
    db.execute("""UPDATE collect_run SET finished_at=?, status=?, items_fetched=?, items_new=?,
                   items_changed=?, items_failed=?, report_path=?, error_summary=? WHERE run_id=?""",
               (now_iso(), status, items_fetched, items_new, items_changed, items_failed,
                report_path, error_summary, run_id))


def ensure_run(db: sqlite3.Connection, *, run_id: str, params: dict | None = None,
               output_dir: Path | str | None = None) -> str:
    """Insert a run row for a known run_id when the ledger does not have it yet.

    Used when regenerating a report against a different ledger file: without
    this, finish_run updates zero rows and the run stays invisible.
    """
    import json
    if get_run(db, run_id) is not None:
        return run_id
    directory = Path(output_dir) if output_dir is not None else (DEFAULT_RUNS_DIR / run_id)
    db.execute("""INSERT INTO collect_run (run_id, task_id, started_at, status, params_json, output_dir)
                  VALUES (?,?,?,?,?,?)""",
               (run_id, None, now_iso(), "running", json.dumps(params or {}, ensure_ascii=False),
                str(directory)))
    return run_id


def get_run(db: sqlite3.Connection, run_id: str) -> dict | None:
    return one(db, "SELECT * FROM collect_run WHERE run_id=?", (run_id,))


def set_report_path(db: sqlite3.Connection, run_id: str, report_path: str | Path) -> None:
    """Record the quality-report path for a run without touching its counters."""
    db.execute("UPDATE collect_run SET report_path=? WHERE run_id=?", (str(report_path), run_id))


def list_runs(db: sqlite3.Connection) -> list[dict]:
    return rows(db, "SELECT * FROM collect_run ORDER BY started_at")


# ---------------------------------------------------------------------- failures

def record_failure(db: sqlite3.Connection, *, run_id: str | None, source_key: str | None,
                   failure_kind: str, detail: str, retry_of: str | None = None) -> str:
    failure_id = new_id("fail")
    db.execute("""INSERT INTO collect_failure (failure_id, run_id, source_key, failure_kind, detail,
                   occurred_at, retry_of) VALUES (?,?,?,?,?,?,?)""",
               (failure_id, run_id, source_key, failure_kind, detail, now_iso(), retry_of))
    return failure_id


def list_failures(db: sqlite3.Connection, run_id: str | None = None) -> list[dict]:
    if run_id:
        return rows(db, "SELECT * FROM collect_failure WHERE run_id=? ORDER BY occurred_at", (run_id,))
    return rows(db, "SELECT * FROM collect_failure ORDER BY occurred_at")


# ------------------------------------------------------------------ verify tasks

def add_verify_task(db: sqlite3.Connection, *, fact_ref: str, check_item: str,
                    method_required: str, note: str | None = None) -> str:
    verify_id = new_id("verify")
    db.execute("""INSERT INTO verify_task (verify_id, fact_ref, check_item, method_required, status,
                   note, created_at) VALUES (?,?,?,?,?,?,?)""",
               (verify_id, fact_ref, check_item, method_required, "open", note, now_iso()))
    return verify_id


def set_verify_status(db: sqlite3.Connection, verify_id: str, status: str, *,
                      reviewer: str | None = None, note: str | None = None) -> None:
    db.execute("""UPDATE verify_task SET status=?, reviewer=?, verified_at=?, note=? WHERE verify_id=?""",
               (status, reviewer, now_iso() if status != "open" else None, note, verify_id))


def list_verify_tasks(db: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        return rows(db, "SELECT * FROM verify_task WHERE status=? ORDER BY created_at", (status,))
    return rows(db, "SELECT * FROM verify_task ORDER BY created_at")


def open_verify_refs(db: sqlite3.Connection) -> set[str]:
    return {r["fact_ref"] for r in rows(db, "SELECT fact_ref FROM verify_task WHERE status='open'")}
