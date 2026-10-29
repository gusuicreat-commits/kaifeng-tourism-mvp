from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.scenic_sync import service
from app.scenic_sync.models import ReviewRequest, SyncSettings
from app.scenic_sync.provider import QmsyunProvider
from app.scenic_sync.store import TZ, connect, migrate

URL = __import__('os').environ.get("SCENIC_TEST_DATABASE_URL")
pytestmark = pytest.mark.postgres
FIXTURE = Path(__file__).parent / "fixtures/scenic_sync/qmsy.html"
NOW = datetime(2026, 10, 24, 9, tzinfo=TZ)
ADMIN = {"x-scenic-admin-key": "test-admin-key-012345678901234567890123456789", "x-scenic-reviewer": "test-reviewer"}


@pytest.fixture(autouse=True)
def clean_pg(monkeypatch, require_isolated_postgres):
    monkeypatch.setenv("SCENIC_SCHEDULE_DATABASE_URL", URL)
    monkeypatch.setenv("SCENIC_ADMIN_API_KEY", ADMIN["x-scenic-admin-key"])
    with connect(URL) as db, db.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
    migrate(URL)
    yield


def ingest(raw=None, now=NOW):
    if raw is None:
        raw = FIXTURE.read_bytes()
        text = raw.decode()
        for index in range(10):
            text = text.replace("</table>", f'<tr><td>补充节目{index}</td><td>补充场地{index}</td><td>10:00,11:00,12:00</td></tr></table>')
        raw = text.encode()
    provider = QmsyunProvider(SyncSettings(source_url="https://www.qmsyun.com/Mobile_page/o_program.php"))
    return service.sync_qmsy(URL, provider, now=now, raw=raw)


def review(schedule_id, request_id=None, version=1):
    return service.review(URL, "qmsy", schedule_id, ReviewRequest(request_id=request_id or uuid4().hex,
        expected_version=version, decision="approve", applicability_verified=True, completeness_verified=True), "test-reviewer")


def test_postgres_ingest_review_idempotency_stale_and_fallback():
    result = ingest()
    assert result["status"] == "review_required" and not result["approved_published"]
    pending = service.performances(URL, "qmsy", NOW.date())
    assert pending["status"] == "review_required" and pending["performances"]
    assert not any(row["eligible_for_solver"] for row in pending["performances"])
    assert ingest(now=NOW.replace(hour=10))["schedule_id"] == result["schedule_id"]
    review(result["schedule_id"])
    assert service.performances(URL, "qmsy", NOW.date())["approved_schedule"] is True
    repeated = ingest(now=NOW.replace(hour=11))
    assert repeated["unchanged"] and repeated["version"] == 1
    stale = ingest(now=NOW.replace(day=25), raw=FIXTURE.read_bytes())
    assert stale["status"] == "stale_source"
    assert service.performances(URL, "qmsy", NOW.date())["schedule_version"] == 1
    next_day = service.performances(URL, "qmsy", NOW.date().replace(day=25))
    assert next_day["status"] == "stale_source" and not next_day["approved_schedule"]
    assert all(row["service_date"] == NOW.date().replace(day=25) for row in next_day["performances"])
    with connect(URL) as db, db.cursor() as cur:
        cur.execute("SELECT octet_length(raw_snapshot),content_sha256 FROM scenic_schedule_sources WHERE raw_snapshot IS NOT NULL LIMIT 1")
        assert cur.fetchone()["octet_length"] > 0


def test_mass_drop_incomplete_and_stale_do_not_replace_approved():
    first = ingest()
    review(first["schedule_id"])
    html = FIXTURE.read_text()
    start, end = html.index("<tr>", html.index("</tr>") + 5), html.rindex("</table>")
    # Keep only a header and footer: a dramatic parsed-count drop is quarantined.
    partial = html[:start] + "<tr><td>A</td><td>B</td><td>10:00</td></tr>" + html[end:]
    dropped = ingest(raw=partial.encode(), now=NOW.replace(hour=10))
    assert dropped["status"] == "review_required"
    incomplete = ingest(raw=b"<p>2026\xe5\xb9\xb410\xe6\x9c\x8824\xe6\x97\xa5\xe6\x9b\xb4\xe6\x96\xb0</p>", now=NOW.replace(hour=11))
    assert incomplete["status"] == "review_required"
    assert service.performances(URL, "qmsy", NOW.date())["approved_schedule"]
    payload = service.performances(URL, "qmsy", NOW.date())
    assert payload["schedule_version"] == 1 and payload["latest_review_status"] == "review_required"


def test_mark_unavailable_hides_withdrawn_sessions():
    result = ingest()
    review(result["schedule_id"])
    service.review(URL, "qmsy", result["schedule_id"], ReviewRequest(request_id=uuid4().hex,
        decision="mark_unavailable", note="合成测试：官方撤回"), "test-reviewer")
    payload = service.performances(URL, "qmsy", NOW.date())
    assert payload["status"] == "unavailable" and not payload["approved_schedule"]
    assert payload["performances"] == []


def test_same_day_changes_review_result_and_concurrent_ingest():
    first = ingest()
    review(first["schedule_id"])
    changed = FIXTURE.read_text().replace("10:00,13:30,17:20", "10:00,13:45,17:20")
    for index in range(10):
        changed = changed.replace("</table>", f'<tr><td>补充节目{index}</td><td>补充场地{index}</td><td>10:00,11:00,12:00</td></tr></table>')
    changed = changed.encode()
    second = ingest(raw=changed, now=NOW.replace(hour=10))
    review(second["schedule_id"], version=second["version"])
    payload = service.changes(URL, "qmsy", NOW.date())
    assert any(row["change_type"] == "time_changed" and row["old_start_time"].strftime("%H:%M") == "13:30" for row in payload["changes"])
    # Two workers receive the same approved content; only one new schedule version survives.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ingest(raw=changed, now=NOW.replace(hour=11)), range(2)))
    assert all(result["version"] == 2 for result in results)
    with connect(URL) as db, db.cursor() as cur:
        cur.execute("SELECT count(*) FROM scenic_schedules WHERE park_id='qmsy' AND service_date='2026-10-24'")
        assert cur.fetchone()["count"] == 2


def test_http_permission_and_status_contract():
    client = TestClient(app)
    assert client.get(f"/api/scenic/parks/qmsy/performances?date={NOW.date()}").status_code == 200
    forbidden = client.post("/api/admin/scenic/parks/wansui/imports", files={"file": ("a.csv", b"x", "text/csv")}, data={"evidence_file": "a.png", "request_id": uuid4().hex})
    assert forbidden.status_code == 403
    assert client.get(f"/api/scenic/parks/qmsy/schedule-status?date={NOW.date()}").json()["display_status"] == "today_unconfirmed"
    assert client.get(f"/api/scenic/parks/wansui/schedule-changes?date={NOW.date()}").status_code == 200


def test_manual_wansui_evidence_hash_and_review(tmp_path, monkeypatch):
    screenshot = tmp_path / "official.png"
    screenshot.write_bytes(b"fixture-not-a-real-official-screenshot")
    import hashlib
    digest = hashlib.sha256(screenshot.read_bytes()).hexdigest()
    content = ("service_date,area,program_name,venue_name,start_time,source_updated_on,evidence_sha256\n"
               f"2026-10-24,东区,测试节目,测试场地,15:00,2026-10-24,{digest}\n").encode()
    imported = service.import_wansui(URL, content, screenshot.name, tmp_path, "test-reviewer", uuid4().hex)
    assert imported["source_api_status"] == "not_verified" and imported["status"] == "review_required"
    approved = service.review(URL, "wansui", imported["schedule_id"], ReviewRequest(request_id=uuid4().hex,
        decision="approve", applicability_verified=True, completeness_verified=True), "test-reviewer")
    assert approved["approved_schedule"]
    output = service.performances(URL, "wansui", NOW.date())
    assert output["performances"][0]["area"] == "东区" and output["performances"][0]["source"]["evidence_sha256"] == digest
    assert output["performances"][0]["verified_poi_id"] is None


def test_manual_wrong_hash_rejected(tmp_path):
    evidence = tmp_path / "official.png"
    evidence.write_bytes(b"fixture")
    content = "service_date,area,program_name,venue_name,start_time,source_updated_on,evidence_sha256\n2026-10-24,东区,A,B,15:00,2026-10-24,wrong\n".encode()
    with pytest.raises(ValueError, match="哈希"):
        service.import_wansui(URL, content, evidence.name, tmp_path, "test-reviewer", uuid4().hex)

