"""PostgreSQL persistence. Pending captures never overwrite the last approved date."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from .models import NormalizedSession

TZ = ZoneInfo("Asia/Shanghai")
MIGRATIONS = Path(__file__).with_name("migrations")


def connect(url: str):
    return psycopg.connect(url, row_factory=dict_row, autocommit=False, connect_timeout=5)


def migrate(url: str) -> list[int]:
    applied = []
    with connect(url) as db, db.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS scenic_schedule_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        cur.execute("LOCK TABLE scenic_schedule_migrations IN ACCESS EXCLUSIVE MODE")
        for migration in sorted(MIGRATIONS.glob("[0-9]*.sql")):
            version = int(migration.name.split("_", 1)[0])
            cur.execute("SELECT 1 FROM scenic_schedule_migrations WHERE version=%s FOR UPDATE", (version,))
            if cur.fetchone():
                continue
            cur.execute(migration.read_text(encoding="utf-8"))
            applied.append(version)
    return applied


def ensure_parks(db):
    with db.cursor() as cur:
        cur.execute("INSERT INTO scenic_parks VALUES ('qmsy','清明上河园'),('wansui','万岁山武侠城') ON CONFLICT DO NOTHING")


def content_hash(sessions: list[NormalizedSession]) -> str:
    records = []
    for session in sessions:
        record = asdict(session)
        for key, value in record.items():
            if value is not None and hasattr(value, "isoformat"):
                record[key] = value.isoformat()
        records.append(record)
    return hashlib.sha256(json.dumps(sorted(records, key=lambda row: str(row)), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def source_fingerprint(source_updated_on, complete, issues, sessions, source_kind):
    content = {"source_updated_on": source_updated_on.isoformat() if source_updated_on else None,
               "complete": complete, "issues": issues, "source_kind": source_kind, "content_sha256": content_hash(sessions)}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def natural_key(session: NormalizedSession) -> str:
    # Area is part of venue disambiguation but not a substitute for a verified POI.
    return hashlib.sha256("\x1f".join((session.program_name, session.venue_name, session.start_time.isoformat() if session.start_time else "", session.service_date.isoformat(), session.area or "")).encode()).hexdigest()


def register_source(db, *, park_id: str, source_type: str, source_ref: str,
                    fetched_at: datetime, raw: bytes | None, source_updated_on: date | None = None,
                    http_status: int | None = None, fetch_status="received", error_code=None,
                    media_type="text/html; charset=utf-8", evidence_sha256=None,
                    evidence_storage_ref=None) -> dict:
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("采集时间必须带时区")
    fetched_at = fetched_at.astimezone(TZ)
    raw = raw or b""
    ensure_parks(db)
    with db.cursor() as cur:
        cur.execute("""INSERT INTO scenic_schedule_sources(park_id,source_type,source_ref,source_updated_on,fetched_at,
            content_sha256,http_status,fetch_status,error_code,raw_snapshot,raw_media_type,evidence_sha256,evidence_storage_ref)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING source_id""",
            (park_id, source_type, source_ref, source_updated_on, fetched_at, hashlib.sha256(raw).hexdigest(),
             http_status, fetch_status, error_code, raw, media_type, evidence_sha256, evidence_storage_ref))
        source_id = cur.fetchone()["source_id"]
    return {"source_id": source_id, "park_id": park_id, "fetch_status": fetch_status, "error_code": error_code,
            "http_status": http_status, "content_sha256": hashlib.sha256(raw).hexdigest()}


def latest(db, park_id: str, service_date: date, approved=True):
    with db.cursor() as cur:
        cur.execute("""SELECT * FROM scenic_schedules WHERE park_id=%s AND service_date=%s
            AND (%s=false OR status='approved_schedule') ORDER BY version DESC LIMIT 1""", (park_id, service_date, approved))
        return cur.fetchone()


def sessions_for(db, schedule_id: int) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM scenic_performances WHERE schedule_id=%s ORDER BY start_time NULLS LAST,program_name,venue_name,area", (schedule_id,))
        return cur.fetchall()


def diff(old: list[dict], new: list[NormalizedSession]) -> list[dict]:
    def key(row):
        return row["program_name"], row["venue_name"], row.get("area")
    def clock(row):
        value = row["start_time"]
        return value.isoformat(timespec="minutes") if hasattr(value, "isoformat") else value
    before = {}
    for row in old:
        before.setdefault(key(row), []).append(row)
    after = {}
    for row in new:
        row = asdict(row)
        after.setdefault(key(row), []).append(row)
    changes = []
    for identity in sorted(set(before) | set(after), key=str):
        a = sorted(before.get(identity, []), key=lambda row: (clock(row) is None, clock(row) or ""))
        b = sorted(after.get(identity, []), key=lambda row: (clock(row) is None, clock(row) or ""))
        common = min(len(a), len(b))
        for index in range(common):
            previous, current = a[index], b[index]
            if current.get("operating_status") == "cancelled" and previous["operating_status"] != "cancelled":
                kind = "cancelled"
            elif previous["operating_status"] == "cancelled" and current.get("operating_status") != "cancelled":
                kind = "restored"
            elif clock(previous) != clock(current):
                kind = "time_changed"
            else:
                continue
            changes.append({"program_name": identity[0], "venue_name": identity[1], "change_type": kind,
                            "old_start_time": clock(previous), "new_start_time": clock(current)})
        for row in a[common:]:
            changes.append({"program_name": identity[0], "venue_name": identity[1], "change_type": "removed",
                            "old_start_time": clock(row), "new_start_time": None})
        for row in b[common:]:
            changes.append({"program_name": identity[0], "venue_name": identity[1], "change_type": "added",
                            "old_start_time": None, "new_start_time": clock(row)})
    return changes


def create_schedule(db, *, park_id: str, source_id: int, service_date: date,
                    sessions: list[NormalizedSession], source_updated_on: date | None,
                    complete: bool, issues: list[str], fetched_at: datetime,
                    source_kind="official_html") -> dict:
    if any(row.service_date != service_date for row in sessions):
        raise ValueError("场次日期必须与采集版本的服务日期一致")
    ensure_parks(db)
    previous = latest(db, park_id, service_date)
    digest = source_fingerprint(source_updated_on, complete, issues, sessions, source_kind)
    # Reprocessing identical approved content is a no-op; every fetch still has a source row.
    if previous:
        old = sessions_for(db, previous["schedule_id"])
        if old and old[0]["source_fingerprint"] == digest:
            return {"schedule_id": previous["schedule_id"], "status": previous["status"], "unchanged": True,
                    "version": previous["version"], "service_date": service_date.isoformat()}
    with db.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"schedule:{park_id}:{service_date}",))
        # Recheck after waiting: concurrent imports must not manufacture duplicate versions.
        previous = latest(db, park_id, service_date)
        previous_rows = sessions_for(db, previous["schedule_id"]) if previous else []
        if previous_rows and previous_rows[0]["source_fingerprint"] == digest:
            return {"schedule_id": previous["schedule_id"], "status": previous["status"], "unchanged": True,
                    "version": previous["version"], "service_date": service_date.isoformat()}
        old = sessions_for(db, previous["schedule_id"]) if previous else []
        changes = diff(old, sessions) if previous else []
        if source_kind == "official_html" and source_updated_on != fetched_at.astimezone(TZ).date():
            status, reason = "stale_source", "网页发布日期不是中国当天，适用日期未经证实"
        elif not complete or not sessions:
            status, reason = "review_required", "页面不完整或结构变化：" + ",".join(issues)
        elif old and len(sessions) < len(old) * 0.5:
            status, reason = "review_required", "场次数量下降超过50%，疑似不完整页面"
        else:
            status, reason = "review_required", "需管理员明确核验日期适用性、完整性及内容后批准"
        cur.execute("SELECT COALESCE(MAX(version),0)+1 AS version FROM scenic_schedules WHERE park_id=%s AND service_date=%s", (park_id, service_date))
        version = cur.fetchone()["version"]
        cur.execute("""INSERT INTO scenic_schedules(park_id,service_date,version,source_id,status,source_updated_on,
            previous_schedule_id,review_reason,source_fingerprint) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING schedule_id""",
            (park_id, service_date, version, source_id, status, source_updated_on,
             previous["schedule_id"] if previous else None, reason, digest))
        schedule_id = cur.fetchone()["schedule_id"]
        for session in sessions:
            cur.execute("""INSERT INTO scenic_performances(schedule_id,park_id,service_date,program_name,venue_name,area,
                start_time,duration_minutes,end_time,entry_deadline,requires_booking,weather_sensitive,operating_status,schedule_note,natural_key,source_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (schedule_id, park_id, service_date, session.program_name, session.venue_name, session.area,
                 session.start_time, session.duration_minutes, session.end_time, session.entry_deadline,
                 session.requires_booking, session.weather_sensitive, session.operating_status, session.schedule_note, natural_key(session), digest))
        for change in changes:
            cur.execute("""INSERT INTO scenic_schedule_changes(schedule_id,park_id,service_date,program_name,venue_name,
                change_type,old_start_time,new_start_time,review_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (schedule_id, park_id, service_date, change["program_name"], change["venue_name"], change["change_type"],
                 change["old_start_time"], change["new_start_time"], status))
    return {"schedule_id": schedule_id, "status": status, "unchanged": False, "version": version,
            "service_date": service_date.isoformat(), "review_reason": reason, "session_count": len(sessions),
            "change_count": len(changes), "source_sha256": digest}


def content_hash_from_rows(rows: list[dict]) -> str:
    normalized = [NormalizedSession(program_name=row["program_name"], venue_name=row["venue_name"], service_date=row["service_date"],
        start_time=row["start_time"], schedule_note=row["schedule_note"], area=row["area"], duration_minutes=row["duration_minutes"],
        end_time=row["end_time"], entry_deadline=row["entry_deadline"], requires_booking=row["requires_booking"],
        weather_sensitive=row["weather_sensitive"], operating_status=row["operating_status"]) for row in rows]
    return content_hash(normalized)

