"""Explicit solver admission and cancellation signal, with no planner mutations."""
from __future__ import annotations

from datetime import datetime

from ..city import facts
from .store import TZ


def solver_eligibility(performance: dict, binding: dict | None) -> dict:
    missing = []
    if performance.get("start_time") is None: missing.append("start_time")
    if performance.get("duration_minutes") is None and performance.get("end_time") is None: missing.append("duration_minutes_or_end_time")
    if performance.get("entry_deadline") is None: missing.append("entry_deadline")
    if performance.get("requires_booking") is None: missing.append("requires_booking")
    if performance.get("weather_sensitive") is None: missing.append("weather_sensitive")
    if not binding or not binding.get("verified_poi_id"): missing.append("verified_poi_id")
    elif not binding.get("routing_verified"): missing.append("verified_directed_walking_route")
    if performance.get("operating_status") != "scheduled": missing.append("operating_status")
    return {"eligible_for_solver": not missing, "missing_for_solver": missing,
            "basis": "verified_fields_only" if not missing else "catalog_only_not_a_solver_task"}


def cancellation_signal(before: list[dict], changes: list[dict], service_date, reviewer: str) -> dict | None:
    affected = {(change["program_name"], change["venue_name"]) for change in changes if change["change_type"] in ("removed", "cancelled")}
    sessions = [row["verified_city_session_id"] for row in before
                if (row["program_name"], row["venue_name"]) in affected and row.get("verified_city_session_id")]
    if not sessions:
        return None
    return {"signal": "schedule_cancelled_replan_required", "service_date": service_date.isoformat(),
            "session_ids": sorted(set(sessions)), "reviewer": reviewer, "formal_itinerary_changed": False,
            "next_step": "existing_replan_preview_then_explicit_confirmation"}


def city_session_candidate(performance: dict, binding: dict, source_id: str, program_id: str,
                           reviewer: str, verified_at: datetime, effective_end: datetime) -> dict:
    """Mapper output remains a draft until existing import_city_facts.py --apply."""
    if not solver_eligibility(performance, binding)["eligible_for_solver"]:
        raise ValueError("该场次缺少完整求解条件，不能映射为已核验城市事实")
    start = datetime.combine(performance["service_date"], performance["start_time"], TZ)
    end = datetime.combine(performance["service_date"], performance["end_time"], TZ) if performance["end_time"] else start + __import__('datetime').timedelta(minutes=performance["duration_minutes"])
    if end <= start or end.date() != start.date():
        raise ValueError("场次结束时间无效")
    deadline = datetime.combine(performance["service_date"], performance["entry_deadline"], TZ)
    fact_id = f"scenic-{performance['park_id']}-{performance['natural_key'][:32]}"
    return {"kind": "show_session", "fact_id": fact_id, "version": 1, "source_ids": [source_id],
            "validation_status": "verified", "business_status": "active", "is_demo": False,
            "verified_at": verified_at.isoformat(), "reviewer": reviewer, "verification_method": "approved scenic schedule plus verified venue/routing review",
            "effective_from": verified_at.isoformat(), "effective_to": effective_end.isoformat(), "rights_status": "permitted",
            "program_id": program_id, "stage_place_id": binding["verified_poi_id"], "business_date": performance["service_date"].isoformat(),
            "starts_at": start.isoformat(), "ends_at": end.isoformat(), "admission_deadline": deadline.isoformat()}

