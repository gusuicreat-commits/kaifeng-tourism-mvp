"""Release assessment cannot promote candidates or fabricate missing trip inputs."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from collector.real_route import assess


def candidate_bundle(tmp_path):
    payload = {
        "schema_version": "city-facts/1",
        "records": [{
            "kind": "source", "fact_id": "source",
            "name": "Synthetic source for test only", "source_type": "other",
            "locator": "test fixture", "collected_at": "2020-01-01T00:00:00+08:00",
        }, {
            "kind": "place", "fact_id": "place", "name": "Synthetic place",
            "place_type": "poi", "source_ids": ["source"],
        }],
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(payload))
    return path


def manifest():
    return {
        "route_id": "test-only", "visitor_date": None,
        "sequence": [
            {"step": 1, "kind": "poi", "fact_ref": "place:place"},
            {"step": 2, "kind": "return_endpoint", "fact_ref": None},
        ],
    }


def test_real_candidate_stays_blocked_and_original_bytes_unchanged(tmp_path):
    path = candidate_bundle(tmp_path)
    before = path.read_bytes()
    report = assess(manifest(), path)
    assert report["status"] == "blocked"
    assert report["runtime_database_written"] is False
    assert report["field_acceptance"] == "not_performed"
    issues = report["required_facts"][0]["issues"]
    assert "UNVERIFIED" in issues and "LOCATION_UNKNOWN" in issues
    assert {b["code"] for b in report["blockers"]} >= {
        "REQUIREMENTS_REQUIRED", "JOURNEYS_REQUIRED", "MISSING_ENDPOINT",
    }
    assert path.read_bytes() == before


def test_real_engine_refuses_candidates_even_with_complete_trip_inputs(tmp_path):
    target = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=1)).date()
    request = {
        "business_date": target.isoformat(),
        "starts_at": f"{target}T09:00:00+08:00",
        "ends_at": f"{target}T12:00:00+08:00",
        "origin_place_id": "place", "destination_place_id": "place",
        "goals": [{"goal_id": "visit", "target_kind": "place",
                   "target_id": "place", "required": True}],
    }
    req_path = tmp_path / "request.json"
    req_path.write_text(json.dumps(request))
    selected = manifest()
    selected["visitor_date"] = target.isoformat()
    selected["requirements_basis"] = "agent_simulated_user_authorized"
    selected["sequence"][1]["fact_ref"] = "place:place"
    report = assess(selected, candidate_bundle(tmp_path), requirements_path=req_path)
    assert report["status"] == "blocked"
    assert report["requirements_basis"] == "agent_simulated_user_authorized"
    assert report["planning"]["status"] == "needs_data"
    assert not report["planning"]["itineraries"]
    assert not {b["code"] for b in report["blockers"]} & {
        "REQUIREMENTS_REQUIRED", "MISSING_ENDPOINT", "VISITOR_DATE_NOT_CONFIRMED",
    }


@pytest.mark.parametrize("rule_offset", [0, -1])
def test_fact_readiness_uses_requested_day_without_rewriting_rules(tmp_path, rule_offset):
    moment = datetime.now(ZoneInfo("Asia/Shanghai"))
    target = (moment + timedelta(days=1)).date()
    rule_date = target + timedelta(days=rule_offset)
    path = candidate_bundle(tmp_path)
    payload = json.loads(path.read_text())
    payload["records"][1].update(longitude=0, latitude=0, coordinate_system="WGS84")
    payload["records"].append({
        "kind": "open_rule", "fact_id": "rule", "place_id": "place",
        "source_ids": ["source"], "business_date": rule_date.isoformat(),
        "opens_at": f"{rule_date}T08:00:00+08:00",
        "closes_at": f"{rule_date}T18:00:00+08:00",
    })
    for record in payload["records"]:
        record.update(
            validation_status="verified", rights_status="permitted",
            reviewer="test fixture only", verification_method="synthetic test fixture",
            verified_at=(moment - timedelta(minutes=1)).isoformat(),
            effective_from=(moment - timedelta(days=1)).isoformat(),
            effective_to=(moment + timedelta(days=3)).isoformat(),
        )
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    req_path = tmp_path / "request.json"
    req_path.write_text(json.dumps({
        "business_date": target.isoformat(),
        "starts_at": f"{target}T09:00:00+08:00",
        "ends_at": f"{target}T12:00:00+08:00",
        "origin_place_id": "place", "destination_place_id": "place",
        "goals": [{"goal_id": "visit", "target_kind": "place",
                   "target_id": "place", "required": True}],
    }))
    selected = manifest()
    selected["visitor_date"] = target.isoformat()
    selected["sequence"][0]["required_rule_ref"] = "open_rule:rule"
    selected["sequence"][1]["fact_ref"] = "place:place"
    report = assess(selected, path, requirements_path=req_path)
    rule = next(r for r in report["required_facts"] if r["ref"] == "open_rule:rule")
    mismatches = [b for b in report["blockers"] if b["code"] == "FACT_DATE_MISMATCH"]
    assert rule["ready"] is (rule_offset == 0)
    if rule_offset:
        assert "BUSINESS_DATE_NOT_APPLICABLE" in rule["issues"]
        assert mismatches == [{"code": "FACT_DATE_MISMATCH", "ref": "open_rule:rule",
                               "fact_date": rule_date.isoformat(),
                               "requested_date": target.isoformat()}]
    else:
        assert not mismatches
    assert path.read_bytes() == before


def test_broken_fact_dependency_rejected_before_planning(tmp_path):
    path = candidate_bundle(tmp_path)
    data = json.loads(path.read_text())
    data["records"] = [r for r in data["records"] if r["kind"] != "source"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        assess(manifest(), path)
