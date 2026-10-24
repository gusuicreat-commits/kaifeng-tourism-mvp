from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path

from fastapi import Header, HTTPException, Security
from fastapi.security import APIKeyHeader
from psycopg import errors

from .manual_import import evidence_file, parse_csv
from .models import ReviewRequest, SyncSettings, VenueBindingRequest
from .parse import parse_schedule
from .provider import FetchRejected, QmsyunProvider
from .store import TZ, connect, create_schedule, latest, register_source, sessions_for
from .mapper import solver_eligibility


def database_url() -> str:
    url = os.getenv("SCENIC_SCHEDULE_DATABASE_URL", "").strip()
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise HTTPException(503, detail={"code": "SCENIC_POSTGRESQL_NOT_CONFIGURED", "message": "演出日程库尚未配置。"})
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def source_view(row: dict) -> dict:
    return {"source_id": row["source_id"], "source_type": row["source_type"], "source_ref": row["source_ref"],
            "source_updated_on": row["source_updated_on"], "fetched_at": row["fetched_at"],
            "content_sha256": row["content_sha256"], "evidence_sha256": row["evidence_sha256"]}


def base_view(schedule: dict | None, park_id: str, service_date: date) -> dict:
    if not schedule:
        return {"park_id": park_id, "service_date": service_date, "approved_schedule": False,
                "status": "unavailable", "display_status": "today_unconfirmed",
                "schedule_version": None, "source": None, "source_freshness": "unavailable",
                "notice": "没有该日期经适用性与完整性核验的节目表。"}
    approved = schedule["status"] == "approved_schedule" and schedule["service_date"] == service_date
    freshness = "approved_schedule" if approved else schedule["status"]
    return {"park_id": park_id, "service_date": service_date, "approved_schedule": approved,
            "status": schedule["status"] if approved else "stale_source",
            "display_status": "official_schedule_reviewed" if approved else "stale_reference_only",
            "schedule_version": schedule["version"], "source_id": schedule["source_id"],
            "source_updated_on": schedule["source_updated_on"], "last_fetched_at": None,
            "reviewed_at": schedule["reviewed_at"], "reviewer": schedule["reviewer"],
            "source_freshness": freshness,
            "notice": "日期与内容已审核；不等于现场保证演出，临时变动以景区广播为准。" if approved else "仅保留旧日程参考；不能视为该日期已确认节目表。"}


def schedule_with_source(db, schedule):
    if not schedule:
        return None
    with db.cursor() as cur:
        cur.execute("SELECT * FROM scenic_schedule_sources WHERE source_id=%s", (schedule["source_id"],))
        source = cur.fetchone()
        cur.execute("SELECT * FROM scenic_schedule_sources WHERE park_id=%s ORDER BY fetched_at DESC,source_id DESC LIMIT 1", (schedule["park_id"],))
        recent = cur.fetchone()
    return schedule, source, recent


def performance_view(db, row: dict, schedule: dict, source: dict) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM scenic_venue_bindings WHERE park_id=%s AND venue_name=%s", (row["park_id"], row["venue_name"]))
        binding = cur.fetchone()
    return {"performance_id": row["performance_id"], "program_name": row["program_name"], "venue_name": row["venue_name"],
            "area": row["area"], "service_date": row["service_date"], "start_time": row["start_time"],
            "duration_minutes": row["duration_minutes"], "end_time": row["end_time"], "entry_deadline": row["entry_deadline"],
            "requires_booking": row["requires_booking"], "weather_sensitive": row["weather_sensitive"],
            "operating_status": row["operating_status"], "schedule_note": row["schedule_note"],
            "verified_poi_id": binding["verified_poi_id"] if binding else None,
            "venue_binding_status": "verified" if binding and binding["verified_poi_id"] else "unbound",
            **solver_eligibility(row, binding), "source": source_view(source),
            "source_freshness": schedule["status"], "confidence_status": "approved_schedule" if schedule["status"] == "approved_schedule" else "stale_source",
            "onsite_performance_guaranteed": False}


def performances(url: str, park_id: str, service_date: date) -> dict:
    with connect(url) as db:
        schedule = latest(db, park_id, service_date)
        if not schedule:
            return {**base_view(None, park_id, service_date), "performances": [], "agent_citation": citation(park_id, service_date, None, "unavailable")}
        schedule, source, recent = schedule_with_source(db, schedule)
        base = base_view(schedule, park_id, service_date)
        base["source"] = source_view(source)
        base["last_fetched_at"] = recent["fetched_at"] if recent else None
        rows = [performance_view(db, row, schedule, source) for row in sessions_for(db, schedule["schedule_id"])]
        return {**base, "performances": rows, "agent_citation": citation(park_id, service_date, source, schedule["status"])}


def citation(park_id, service_date, source, status):
    return {"query_date": service_date.isoformat(), "park_id": park_id,
            "source": source_view(source) if source else None, "confidence_status": status,
            "answer_policy": "仅按该查询日期及状态回答；无法确认就说不知道，不推断其他日期或现场演出。"}


def status(url: str, park_id: str, service_date: date) -> dict:
    result = performances(url, park_id, service_date)
    result.pop("performances")
    with connect(url) as db, db.cursor() as cur:
        cur.execute("""SELECT * FROM scenic_schedules WHERE park_id=%s AND service_date=%s
            ORDER BY version DESC LIMIT 1""", (park_id, service_date))
        pending = cur.fetchone()
    result["latest_review_status"] = pending["status"] if pending else "unavailable"
    result["pending_review_schedule_id"] = pending["schedule_id"] if pending and pending["status"] == "review_required" else None
    result["stale_warning"] = not result["approved_schedule"]
    return result


def changes(url: str, park_id: str, service_date: date) -> dict:
    result = status(url, park_id, service_date)
    with connect(url) as db, db.cursor() as cur:
        cur.execute("""SELECT c.*,s.version,s.review_reason FROM scenic_schedule_changes c
            JOIN scenic_schedules s ON s.schedule_id=c.schedule_id
            WHERE c.park_id=%s AND c.service_date=%s ORDER BY c.change_id DESC LIMIT 1000""", (park_id, service_date))
        rows = cur.fetchall()
        from .publish import replan_signals
        signals = []
        with db.cursor() as cur:
            cur.execute("SELECT schedule_id FROM scenic_schedules WHERE park_id=%s AND service_date=%s AND status='approved_schedule'", (park_id, service_date))
            for item in cur.fetchall(): signals.extend(replan_signals(db, item["schedule_id"]))
    return {**result, "changes": rows, "replan_signals": signals, "formal_itinerary_changed": False}


admin_key = APIKeyHeader(name="x-scenic-admin-key", auto_error=False)


def require_admin(supplied: str | None = Security(admin_key), reviewer: str | None = Header(None, alias="x-scenic-reviewer")) -> str:
    import secrets
    expected = os.getenv("SCENIC_ADMIN_API_KEY", "")
    reviewer = (reviewer or "").strip()
    if len(expected) < 32 or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(403, detail="需要有效的管理员凭证。")
    if not reviewer or len(reviewer) > 100:
        raise HTTPException(422, detail="必须填写审核人标识。")
    return reviewer


def admin_tx(url, scope, request_id, payload, action):
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    try:
        with connect(url) as db, db.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"admin:{scope}",))
            cur.execute("SELECT * FROM scenic_admin_receipts WHERE scope=%s AND request_id=%s", (scope, request_id))
            receipt = cur.fetchone()
            if receipt:
                if receipt["request_sha256"] != digest:
                    raise HTTPException(409, detail="request_id 已用于其他内容。")
                return receipt["response_json"]
            result = action(db)
            cur.execute("INSERT INTO scenic_admin_receipts VALUES (%s,%s,%s,%s)",
                        (scope, request_id, digest, json.dumps(result, ensure_ascii=False, default=str)))
            return result
    except errors.UniqueViolation as error:
        raise HTTPException(409, detail="版本冲突或重复采集，请刷新后重试。") from error


def sync_qmsy(url: str, provider: QmsyunProvider, now: datetime | None = None, raw: bytes | None = None) -> dict:
    now = now or datetime.now(TZ)
    service_date = now.astimezone(TZ).date()
    # Serialize attempts without holding a database transaction across HTTP I/O.
    with provider._lock:
        with connect(url) as db, db.cursor() as cur:
            cur.execute("SELECT fetched_at FROM scenic_schedule_sources WHERE park_id='qmsy' ORDER BY fetched_at DESC LIMIT 1")
            previous = cur.fetchone()
        if previous and (now.astimezone(TZ) - previous["fetched_at"].astimezone(TZ)).total_seconds() < provider.settings.minimum_interval_minutes * 60:
            return {"status": "skipped_rate_limit", "service_date": service_date, "approved_published": False}
        try:
            fetched = provider.fetch(now) if raw is None else {"raw": raw, "http_status": 200, "content_type": "text/html; charset=utf-8", "fetched_at": now}
            parsed = parse_schedule(fetched["raw"].decode("utf-8"), service_date)
            if parsed.complete and len({row.program_name for row in parsed.sessions}) < 10:
                parsed.complete = False
                parsed.issues.append("too_few_programs_possible_partial_page")
            if sum(row.start_time is not None for row in parsed.sessions) < 20:
                parsed.complete = False
                parsed.issues.append("too_few_timed_sessions_possible_partial_page")
        except (FetchRejected, UnicodeDecodeError) as error:
            code = error.code if isinstance(error, FetchRejected) else "invalid_encoding"
            with connect(url) as db:
                source = register_source(db, park_id="qmsy", source_type="official_html", source_ref=provider.settings.source_url,
                    fetched_at=now, raw=None, http_status=getattr(error, "status", None), fetch_status="blocked" if "403" in code or "429" in code else "rejected", error_code=code)
            return {"status": "unavailable", "fetch_status": "failed_previous_schedule_retained", "error_code": code,
                    "source_id": source["source_id"], "approved_published": False}
    with connect(url) as db:
        source = register_source(db, park_id="qmsy", source_type="official_html", source_ref=provider.settings.source_url,
            fetched_at=now, raw=fetched["raw"], source_updated_on=parsed.source_updated_on,
            http_status=fetched["http_status"], media_type=fetched["content_type"])
        result = create_schedule(db, park_id="qmsy", source_id=source["source_id"], service_date=service_date,
            sessions=parsed.sessions, source_updated_on=parsed.source_updated_on, complete=parsed.complete,
            issues=parsed.issues, fetched_at=now)
    result.update(fetch_status="received", http_status=200, source_id=source["source_id"], approved_published=False)
    return result


def import_wansui(url: str, csv_content: bytes, evidence_name: str, csv_base: Path, reviewer: str, request_id: str) -> dict:
    evidence, digest = evidence_file(csv_base, evidence_name)
    service_date, source_updated_on, sessions = parse_csv(csv_content, evidence, digest)
    def action(db):
        source = register_source(db, park_id="wansui", source_type="manual_csv", source_ref=f"official_evidence:{evidence_name}",
            fetched_at=datetime.now(TZ), raw=csv_content, source_updated_on=source_updated_on,
            media_type="text/csv; charset=utf-8", evidence_sha256=digest, evidence_storage_ref=evidence_name)
        result = create_schedule(db, park_id="wansui", source_id=source["source_id"], service_date=service_date,
            sessions=sessions, source_updated_on=source_updated_on, complete=True, issues=[], fetched_at=datetime.now(TZ), source_kind="manual_csv")
        return {**result, "evidence_sha256": digest, "source_api_status": "not_verified", "reviewer": reviewer}
    return admin_tx(url, f"import:wansui:{service_date}", request_id, {"csv_sha256": hashlib.sha256(csv_content).hexdigest(), "evidence_sha256": digest}, action)


def review(url: str, park_id: str, schedule_id: int, body: ReviewRequest, reviewer: str) -> dict:
    def action(db):
        with db.cursor() as cur:
            cur.execute("SELECT * FROM scenic_schedules WHERE schedule_id=%s AND park_id=%s FOR UPDATE", (schedule_id, park_id))
            current = cur.fetchone()
            if not current: raise HTTPException(404, detail="采集版本不存在。")
            if body.expected_version is not None and current["version"] != body.expected_version:
                raise HTTPException(409, detail="审核版本已变化。")
            if current["status"] == "approved_schedule" and body.decision == "approve":
                raise HTTPException(409, detail="该版本已经审核，请用显式撤销后重新审核，而非重复改写。")
            if current["status"] != "review_required" and body.decision == "approve":
                raise HTTPException(409, detail="只有待复核版本可直接批准。")
            if body.decision == "approve" and not (body.applicability_verified and body.completeness_verified):
                raise HTTPException(422, detail="必须分别确认日期适用性和页面完整性。")
            status = "approved_schedule" if body.decision == "approve" else "unavailable" if body.decision == "mark_unavailable" else "review_required"
            cur.execute("""UPDATE scenic_schedules SET status=%s,applicability_verified=%s,completeness_verified=%s,
                reviewer=%s,reviewed_at=now(),review_reason=%s WHERE schedule_id=%s""",
                (status, body.applicability_verified, body.completeness_verified, reviewer, body.note or "管理员复核", schedule_id))
            cur.execute("UPDATE scenic_schedule_changes SET review_status=%s WHERE schedule_id=%s", (status, schedule_id))
            if status == "approved_schedule":
                from .mapper import cancellation_signal
                cur.execute("SELECT previous_schedule_id FROM scenic_schedules WHERE schedule_id=%s", (schedule_id,))
                previous_id = cur.fetchone()["previous_schedule_id"]
                if previous_id:
                    cur.execute("SELECT * FROM scenic_schedule_changes WHERE schedule_id=%s AND change_type IN ('cancelled','removed')", (schedule_id,))
                    change_rows = cur.fetchall()
                    signal = cancellation_signal(sessions_for(db, previous_id), change_rows, current["service_date"], reviewer)
                    if signal:
                        cur.execute("""INSERT INTO scenic_schedule_signals(schedule_id,service_date,signal_type,affected_session_ids,review_status)
                            VALUES (%s,%s,%s,%s,%s)""", (schedule_id, current["service_date"], signal["signal"], json.dumps(signal["session_ids"]), reviewer))
        return {"schedule_id": schedule_id, "status": status, "reviewer": reviewer, "approved_schedule": status == "approved_schedule"}
    return admin_tx(url, f"review:{schedule_id}", body.request_id, body.model_dump(mode="json") | {"reviewer": reviewer}, action)


def bind_venue(url: str, park_id: str, body: VenueBindingRequest, reviewer: str) -> dict:
    # A POI id alone is not enough: it must exist, be verified and belong to this park.
    if body.verified_poi_id:
        from ..city.db_compat import verified_poi
        verified_poi(body.verified_poi_id, park_id, body.routing_verified)
    def action(db):
        with db.cursor() as cur:
            cur.execute("""INSERT INTO scenic_venue_bindings(park_id,venue_name,verified_poi_id,routing_verified,
                verification_method,evidence_ref,reviewer,reviewed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,now())
                ON CONFLICT(park_id,venue_name) DO UPDATE SET verified_poi_id=excluded.verified_poi_id,
                routing_verified=excluded.routing_verified,verification_method=excluded.verification_method,
                evidence_ref=excluded.evidence_ref,reviewer=excluded.reviewer,reviewed_at=excluded.reviewed_at,updated_at=now()
                RETURNING *""", (park_id, body.venue_name, body.verified_poi_id, body.routing_verified,
                                  body.verification_method, body.evidence_ref, reviewer))
            row = cur.fetchone()
        return {**row, "solver_admission": "only_per_session_after_all_fields_verified"}
    return admin_tx(url, f"venue:{park_id}:{body.venue_name}", body.request_id, body.model_dump(mode="json") | {"reviewer": reviewer}, action)


def publish(url: str, park_id: str, schedule_id: int, body: AdminRequest, reviewer: str) -> dict:
    from .publish import publish_schedule
    return publish_schedule(url, park_id, schedule_id, reviewer, body.request_id)

