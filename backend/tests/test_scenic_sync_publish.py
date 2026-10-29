import json
from datetime import datetime, time
from pathlib import Path
from uuid import uuid4

import pytest

from app.city import facts
from app.city.engine.storage import import_operations
from app.city.engine.models import OperationalBundle
from app.city.engine.models import Goal, Requirements
from app.db import connect as city_connect
from app.memory.storage import init_memory
from app.memory.session import ensure
from app.scenic_sync import service
from app.scenic_sync.mapper import city_session_candidate
from app.scenic_sync.models import ReviewRequest
from app.scenic_sync.store import TZ, connect, migrate, register_source, create_schedule, sessions_for
from app.scenic_sync.models import NormalizedSession

URL = __import__('os').environ.get("SCENIC_TEST_DATABASE_URL")
pytestmark = pytest.mark.postgres
NOW = datetime.now(TZ).replace(second=0, microsecond=0)
DAY = NOW.date()


@pytest.fixture
def publishing_environment(tmp_path, monkeypatch, require_isolated_postgres):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "city.sqlite3"))
    monkeypatch.setenv("M7_DELETION_LEDGER_PATH", str(tmp_path / "deletion.sqlite3"))
    with connect(URL) as db, db.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE");cur.execute("CREATE SCHEMA public")
    migrate(URL)
    with connect(URL) as db, db.cursor() as cur:
        cur.execute("INSERT INTO scenic_parks(park_id,name) VALUES (%s,'合成测试园区')", (f"park-{DAY.isoformat()}",))
    synthetic = json.loads((Path(__file__).parent / "fixtures/engine_synthetic.json").read_text())
    park_id = f"park-{DAY.isoformat()}"
    records = []
    for item in synthetic["facts"]["records"]:
        item["is_demo"] = False
        if item["kind"] == "place" and item["fact_id"] == "museum":
            item.update(fact_id=park_id, place_type="scenic", parent_place_id=None)
        if item["kind"] == "place" and item["fact_id"] == "stage1":
            item.update(fact_id="verified-stage", place_type="stage", parent_place_id=park_id)
        if item["kind"] == "place" and item["fact_id"] in ("origin", "destination"):
            item.update(fact_id=f"{item['fact_id']}-{park_id}", parent_place_id=park_id)
        if item["kind"] == "open_rule":
            item.update(fact_id=f"open-{park_id}-{item['place_id']}", business_date=DAY.isoformat(),
                        opens_at=f"{DAY}T00:00:00+08:00", closes_at=f"{DAY}T23:59:00+08:00",
                        effective_from="2020-01-01T00:00:00+08:00", effective_to=f"{DAY}T23:59:59+08:00")
            item["place_id"] = {"museum": park_id, "stage1": "verified-stage", "origin": f"origin-{park_id}"}.get(item["place_id"], item["place_id"])
        if item["kind"] == "source":
            item["effective_to"] = f"{DAY}T23:59:59+08:00"
        if item["kind"] not in ("show_session", "program") and item.get("fact_id") in {park_id, "verified-stage", f"origin-{park_id}", f"open-{park_id}-museum", f"open-{park_id}-stage1", f"open-{park_id}-origin", "fixture:source"}:
            records.append(item)
    with city_connect() as db:
        init_memory()
        facts.import_bundle(facts.FactBundle.model_validate({"records": records}), db, apply=True)
    # Two explicit directed edges; no reverse or straight-line shortcut is manufactured.
    journeys = []
    for a,b in ((f"origin-{park_id}","verified-stage"),("verified-stage",f"origin-{park_id}")):
        journeys.append({"journey_id":f"walk-{a}-{b}","revision":1,"travel":{"origin_place_id":a,"destination_place_id":b,"mode":"walking","lower_seconds":60,"upper_seconds":60,
            "evidence":{"evidence_id":"walk-evidence","revision":"1","kind":"verified","source":"synthetic-test-not-real-routing","checked_at":NOW.isoformat(),"valid_from":"2020-01-01T00:00:00+08:00","valid_until":f"{DAY}T23:59:59+08:00"},"path_status":"verified","path_ref":"fixture-directed-edge","coordinate_system":"GCJ02"},
            "distance_m":100,"cost_lower_fen":0,"cost_upper_fen":0,"boundaries":[],"source_ids":["fixture:source"],"time_basis":"survey_envelope"})
    with city_connect() as db:
        import_operations(db, OperationalBundle.model_validate({"journeys":journeys}), facts.records(db), apply=True)
    return park_id


def prepare_schedule(park_id, cancelled=False):
    start = (NOW + __import__('datetime').timedelta(minutes=15)).time()
    session = NormalizedSession("合成测试节目", "测试场地", DAY, start, duration_minutes=30,
        entry_deadline=start, requires_booking=False, weather_sensitive=False,
        operating_status="cancelled" if cancelled else "scheduled")
    with connect(URL) as db:
        source = register_source(db, park_id=park_id, source_type="manual_csv", source_ref="synthetic-test-evidence", fetched_at=NOW, raw=b"fixture", source_updated_on=DAY)
        result = create_schedule(db, park_id=park_id, source_id=source["source_id"], service_date=DAY, sessions=[session], source_updated_on=DAY, complete=True, issues=[], fetched_at=NOW, source_kind="manual_csv")
        with db.cursor() as cur:
            cur.execute("INSERT INTO scenic_venue_bindings(park_id,venue_name,verified_poi_id,routing_verified,reviewer,reviewed_at) VALUES (%s,'测试场地','verified-stage',true,'test',now()) ON CONFLICT(park_id,venue_name) DO UPDATE SET verified_poi_id='verified-stage',routing_verified=true", (park_id,))
    service.review(URL, park_id, result["schedule_id"], ReviewRequest(request_id=uuid4().hex, decision="approve", applicability_verified=True, completeness_verified=True), "test")
    return result


def test_explicit_publish_and_cancel_replan_signal_preserves_formal_itinerary(publishing_environment):
    park_id = publishing_environment
    first = prepare_schedule(park_id)
    published = service.publish(URL, park_id, first["schedule_id"], __import__('app.scenic_sync.models', fromlist=['AdminRequest']).AdminRequest(request_id=uuid4().hex), "test")
    assert published["formal_itinerary_changed"] is False
    session_id = published["published_sessions"][0]
    with city_connect() as db:
        session = facts.records(db)["show_session", session_id]
    req = Requirements(business_date=DAY, starts_at=session.starts_at - __import__('datetime').timedelta(minutes=10), ends_at=session.ends_at + __import__('datetime').timedelta(minutes=20),
        origin_place_id=f"origin-{park_id}", destination_place_id=f"origin-{park_id}",
        goals=(Goal(goal_id="show", target_kind="session", target_id=session_id, kind="show", required=True),), modes=("walking",), alternatives=1)
    # Exercise the existing engine without changing its implementation.
    from app.city.engine.context import PlanningContext
    from app.city.engine.tools import plan_trip
    from app.city.engine.prepare import prepare
    from app.city.engine.storage import load_operations
    with city_connect() as db:
        context = PlanningContext(facts.records(db), load_operations(db), session.starts_at - __import__('datetime').timedelta(minutes=10))
        result = plan_trip(req, context)
    assert result.status == "feasible", {"result":result.model_dump(mode="json"),"blocked":prepare(req,context).blocked}
    second = prepare_schedule(park_id, cancelled=True)
    payload = service.changes(URL, park_id, DAY)
    assert any(signal["session_ids"] == [session_id] and not signal["formal_itinerary_changed"] for signal in payload["replan_signals"])


def test_publish_refuses_missing_duration(publishing_environment):
    park_id = publishing_environment
    with connect(URL) as db:
        source = register_source(db, park_id=park_id, source_type="manual_csv", source_ref="fixture", fetched_at=NOW, raw=b"x", source_updated_on=DAY)
        result = create_schedule(db, park_id=park_id, source_id=source["source_id"], service_date=DAY,
            sessions=[NormalizedSession("A","测试场地",DAY,time(15))], source_updated_on=DAY, complete=True, issues=[], fetched_at=NOW, source_kind="manual_csv")
        with db.cursor() as cur:
            cur.execute("INSERT INTO scenic_venue_bindings(park_id,venue_name,verified_poi_id,routing_verified) VALUES (%s,'测试场地','verified-stage',true)", (park_id,))
    service.review(URL, park_id, result["schedule_id"], ReviewRequest(request_id=uuid4().hex, decision="approve", applicability_verified=True, completeness_verified=True), "test")
    with pytest.raises(Exception, match="完整求解条件"):
        service.publish(URL, park_id, result["schedule_id"], __import__('app.scenic_sync.models', fromlist=['AdminRequest']).AdminRequest(request_id=uuid4().hex), "test")
