"""Explicit bridge into existing fact imports; all atomic writes stay in SQLite."""
from __future__ import annotations

from datetime import datetime, time

from fastapi import HTTPException

from ..city import facts
from ..city.db_compat import verified_poi
from ..db import connect as city_connect
from .mapper import city_session_candidate, solver_eligibility
from .store import TZ, connect, sessions_for


def source_fact(schedule, source, reviewer, moment):
    end = datetime.combine(schedule["service_date"], time(23, 59, 59), TZ)
    return {"kind": "source", "fact_id": f"scenic-schedule-source-{schedule['schedule_id']}", "version": 1,
            "source_ids": [], "validation_status": "verified", "business_status": "active", "is_demo": False,
            "verified_at": moment.isoformat(), "reviewer": reviewer, "verification_method": "admin schedule date/content review",
            "effective_from": moment.isoformat(), "effective_to": end.isoformat(), "rights_status": "permitted",
            "name": f"{schedule['park_id']} {schedule['service_date']} approved schedule", "source_type": "official" if source["source_type"] == "official_html" else "operator",
            "url": source["source_ref"] if source["source_ref"].startswith("http") else None,
            "locator": f"scenic_schedule_sources:{source['source_id']}", "publisher": "official source reviewed by operator",
            "collected_at": source["fetched_at"].isoformat(), "published_at": datetime.combine(source["source_updated_on"], time(), TZ).isoformat() if source["source_updated_on"] else None}


def publish_schedule(url, park_id, schedule_id, reviewer, request_id):
    from .service import admin_tx
    def payload():
        with connect(url) as pg, pg.cursor() as cur:
            cur.execute("SELECT * FROM scenic_schedules WHERE schedule_id=%s AND park_id=%s", (schedule_id, park_id))
            schedule = cur.fetchone()
            if not schedule or schedule["status"] != "approved_schedule": raise HTTPException(409, detail="只能发布已审核日程版本。")
            cur.execute("SELECT * FROM scenic_schedule_sources WHERE source_id=%s", (schedule["source_id"],))
            source = cur.fetchone()
            rows = sessions_for(pg, schedule_id)
            for row in rows:
                if row["operating_status"] != "scheduled": continue
                cur.execute("SELECT * FROM scenic_venue_bindings WHERE park_id=%s AND venue_name=%s", (park_id, row["venue_name"]))
                binding = cur.fetchone()
                if not solver_eligibility(row, binding)["eligible_for_solver"]:
                    raise HTTPException(422, detail={"message": "仍有场次不具备完整求解条件，未写入任何城市事实。", "performance_id": row["performance_id"], "missing": solver_eligibility(row, binding)["missing_for_solver"]})
                verified_poi(binding["verified_poi_id"], park_id, True)
            return schedule, source, rows
    schedule, source, rows = payload()
    moment = datetime.now(TZ)
    effective_end = datetime.combine(schedule["service_date"], time(23, 59, 59), TZ)
    if effective_end <= moment: raise HTTPException(409, detail="日程适用日期已结束，不能发布新事实。")
    def action(pg):
        records = []
        source_record = source_fact(schedule, source, reviewer, moment)
        with city_connect() as db:
            existing = facts.records(db)
            old = existing.get(("source", source_record["fact_id"]))
            if old:
                if old.model_dump(mode="json") == source_record: pass
                else:
                    source_record["version"] = old.version + 1
            records.append(source_record)
            mapped = []
            for row in rows:
                if row["operating_status"] != "scheduled": continue
                with pg.cursor() as cur:
                    cur.execute("SELECT * FROM scenic_venue_bindings WHERE park_id=%s AND venue_name=%s", (park_id, row["venue_name"]))
                    binding = cur.fetchone()
                program_id = f"scenic-program-{park_id}-{row['program_name']}"
                # Program ids use a bounded stable digest, not arbitrary long Chinese names.
                import hashlib
                program_id = f"scenic-program-{park_id}-{hashlib.sha256(row['program_name'].encode()).hexdigest()[:24]}"
                program = {"kind": "program", "fact_id": program_id, "version": 1, "source_ids": [source_record["fact_id"]],
                    "validation_status": "verified", "business_status": "active", "is_demo": False, "verified_at": moment.isoformat(),
                    "reviewer": reviewer, "verification_method": "approved schedule program name", "effective_from": moment.isoformat(),
                    "effective_to": effective_end.isoformat(), "rights_status": "permitted", "name": row["program_name"]}
                if ("program", program_id) in existing:
                    before = existing["program", program_id]
                    if before.name != program["name"]: raise HTTPException(409, detail="节目实体映射冲突。")
                    program["version"] = before.version + ({k:v for k,v in before.model_dump(mode="json").items() if k != "version"} != {k:v for k,v in program.items() if k != "version"})
                session = city_session_candidate(row, binding, source_record["fact_id"], program_id, reviewer, moment, effective_end)
                if ("show_session", session["fact_id"]) in existing:
                    before = existing["show_session", session["fact_id"]]
                    session["version"] = before.version + ({k:v for k,v in before.model_dump(mode="json").items() if k != "version"} != {k:v for k,v in session.items() if k != "version"})
                records.extend([program, session]); mapped.append(session["fact_id"])
            bundle = facts.FactBundle.model_validate({"records": records})
            with db:
                result = facts.import_bundle(bundle, db, apply=True)
            if not all(item["ready"] for item in result["records"] if item["kind"] == "show_session"):
                raise HTTPException(422, detail={"message": "既有事实校验未通过，请检查公园营业/预约规则、有效期与来源。", "result": result})
        with pg.cursor() as cur:
            cur.execute("UPDATE scenic_performances SET verified_city_session_id=NULL WHERE schedule_id=%s", (schedule_id,))
            for row in rows:
                if row["operating_status"] != "scheduled": continue
                fact_id = f"scenic-{park_id}-{row['natural_key'][:32]}"
                cur.execute("UPDATE scenic_performances SET verified_city_session_id=%s WHERE performance_id=%s", (fact_id, row["performance_id"]))
        return {"schedule_id": schedule_id, "published_sessions": mapped, "city_fact_import": result,
                "formal_itinerary_changed": False, "new_route_preview_required": True}
    return admin_tx(url, f"publish:{schedule_id}", request_id, {"schedule_id": schedule_id, "reviewer": reviewer}, action)


def replan_signals(pg, schedule_id):
    with pg.cursor() as cur:
        cur.execute("SELECT * FROM scenic_schedule_signals WHERE schedule_id=%s", (schedule_id,))
        return [{"signal": row["signal_type"], "service_date": row["service_date"],
                 "session_ids": row["affected_session_ids"], "reviewer": row["review_status"],
                 "formal_itinerary_changed": row["formal_itinerary_changed"],
                 "next_step": "existing_replan_preview_then_explicit_confirmation"} for row in cur.fetchall()]

