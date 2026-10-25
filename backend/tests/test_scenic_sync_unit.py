from datetime import date, time
from pathlib import Path

import httpx
import pytest

from app.scenic_sync.mapper import cancellation_signal, solver_eligibility
from app.scenic_sync.models import NormalizedSession, SyncSettings
from app.scenic_sync.parse import parse_schedule
from app.scenic_sync.provider import FetchRejected, QmsyunProvider
from app.scenic_sync.service import base_view
from app.scenic_sync.store import diff

FIXTURES = Path(__file__).parent / "fixtures/scenic_sync"
DAY = date(2026, 10, 24)


def test_public_table_multiple_times_and_untimed():
    parsed = parse_schedule((FIXTURES / "qmsy.html").read_text(), DAY)
    assert parsed.complete and parsed.source_updated_on == DAY
    assert len(parsed.sessions) == 6
    assert {s.start_time for s in parsed.sessions if s.program_name == "大宋·东京保卫战"} == {time(10), time(13, 30), time(17, 20)}
    untimed = next(s for s in parsed.sessions if s.start_time is None)
    assert untimed.schedule_note == "以景区公示为准"


def test_saved_live_response_matches_adapter_contract():
    parsed = parse_schedule((FIXTURES / "qmsy-live-20261024.html").read_text(), DAY)
    assert (parsed.complete, parsed.source_updated_on, len(parsed.sessions)) == (True, DAY, 464)
    assert len({s.program_name for s in parsed.sessions}) == 152


@pytest.mark.parametrize("html,issue", [
    ("<p>演艺部 2026年10月24日更新</p><div>明天节目 19:00</div>", "schedule_table_not_found"),
    ("<table><tr><th>节目</th><th>地点</th><th>时间</th></tr><tr><td>A</td><td>B</td><td>10:00</td></tr></table>", "schedule_table_not_found"),
    ("<table><tr><th>展示名称</th><th>地点</th><th>时间</th></tr><tr><td>A</td><td>B</td><td>10:00,25:xx</td></tr></table><p>2026年10月24日更新</p>", "unparsed_time:A"),
])
def test_changed_or_incomplete_html_fails_closed(html, issue):
    parsed = parse_schedule(html, DAY)
    assert not parsed.complete and issue in parsed.issues


@pytest.mark.parametrize("status", [403, 429])
def test_rejected_request_stops_without_retry(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status)
    provider = QmsyunProvider(SyncSettings(), transport=httpx.MockTransport(handler))
    with pytest.raises(FetchRejected) as error:
        provider.fetch()
    assert error.value.status == status
    with pytest.raises(FetchRejected, match="provider_stopped"):
        provider.fetch()
    assert len(calls) == 1


def test_composite_diff_same_name_different_venue_and_time_change():
    old = [{"program_name": "同名", "venue_name": "东区", "area": None, "start_time": time(10), "operating_status": "scheduled"},
           {"program_name": "同名", "venue_name": "西区", "area": None, "start_time": time(10), "operating_status": "scheduled"}]
    new = [NormalizedSession("同名", "东区", DAY, time(11)), NormalizedSession("同名", "西区", DAY, time(10))]
    changes = diff(old, new)
    assert len(changes) == 1 and changes[0]["change_type"] == "time_changed" and changes[0]["venue_name"] == "东区"


def test_unknown_fields_and_unbound_venue_never_enter_solver():
    session = {"start_time": time(10), "duration_minutes": None, "end_time": None,
               "entry_deadline": None, "requires_booking": None, "weather_sensitive": None, "operating_status": "scheduled"}
    eligible = solver_eligibility(session, None)
    assert not eligible["eligible_for_solver"]
    assert {"verified_poi_id", "duration_minutes_or_end_time", "entry_deadline", "requires_booking", "weather_sensitive"} <= set(eligible["missing_for_solver"])


def test_cancel_signal_does_not_mutate_itinerary():
    before = [{"program_name": "A", "venue_name": "B", "verified_city_session_id": "show-1"}]
    signal = cancellation_signal(before, [{"program_name": "A", "venue_name": "B", "change_type": "cancelled"}], DAY, "reviewer")
    assert signal["signal"] == "schedule_cancelled_replan_required"
    assert signal["formal_itinerary_changed"] is False
    assert "explicit_confirmation" in signal["next_step"]


@pytest.mark.parametrize("status,display", [
    ("review_required", "today_unconfirmed"),
    ("stale_source", "stale_reference_only"),
])
def test_public_status_preserves_unapproved_schedule_state(status, display):
    result = base_view({"status": status, "service_date": DAY, "version": 1, "source_id": 7,
                        "source_updated_on": DAY, "reviewed_at": None, "reviewer": None}, "qmsy", DAY)
    assert result["status"] == status
    assert result["display_status"] == display
    assert result["approved_schedule"] is False

