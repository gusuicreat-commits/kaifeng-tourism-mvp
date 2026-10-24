"""Reference transport tests use synthetic provider replies, never real data."""
import itertools
import json

import httpx
import pytest

from app.city.engine.amap import AmapProvider
from collector.route_transport_probe import probe


def targets():
    return {
        "route_id": "test-only",
        "locations": [{"ref": ref, "query": name, "allowed_names": [name]}
                      for ref, name in (("a", "SYNTHETIC_A"), ("b", "SYNTHETIC_B"),
                                        ("c", "SYNTHETIC_C"))],
        "legs": [{"leg_id": str(i), "from": a, "to": b}
                 for i, (a, b) in enumerate((("a", "a"), ("a", "b"),
                                            ("b", "c"), ("c", "a")))],
    }


def provider(*, duplicate=False, unauthorized=False, enabled=True):
    def reply(request):
        if unauthorized:
            return httpx.Response(200, json={"status": "0", "infocode": "10001",
                                            "info": "INVALID_USER_KEY"})
        if "/place/" in request.url.path:
            name = request.url.params["keywords"]
            row = {"id": "SENSITIVE_PROVIDER_ID", "name": name,
                   "location": "114.300123,34.800456", "adcode": "410202",
                   "address": "SENSITIVE_PROVIDER_ADDRESS"}
            rows = [row, dict(row)] if duplicate and name == "SYNTHETIC_A" else [row]
            return httpx.Response(200, json={"status": "1", "pois": rows})
        return httpx.Response(200, json={"status": "1", "route": {"paths": [{
            "distance": "1234", "cost": {"duration": "987"},
            "steps": [{"polyline": "114.300123,34.800456;114.300987,34.800999",
                       "instruction": "SENSITIVE_ROUTE_INSTRUCTION"}],
        }]}})
    ticks = itertools.count()
    return AmapProvider("SENSITIVE_TEST_KEY", enabled=enabled, terms_reviewed=True,
                        api_version="v5", retries=0, max_requests=8, qps=1,
                        clock=lambda: next(ticks), transport=httpx.MockTransport(reply))


def test_reference_success_never_becomes_formal_or_persists_provider_data():
    with_provider = provider()
    try:
        report = probe(targets(), with_provider, sleeper=lambda _: None)
    finally:
        with_provider.close()
    assert report["requests"] == 6
    assert report["status"] == "reference_partial_formal_blocked"
    assert report["legs"][0]["status"] == "same_place_entry_connection_unverified"
    assert all(r["formal_admission"] == "needs_data" for r in report["legs"])
    assert sum(r["status"] == "estimated" for r in report["legs"]) == 3
    assert report["formal_operations_generated"] is False
    assert report["runtime_database_written"] is False
    saved = json.dumps(report)
    for restricted in ("114.300123", "34.800456", "987", "1234", "SENSITIVE_",
                       "duration_seconds", "distance_m", "polyline"):
        assert restricted not in saved


def test_ambiguous_name_is_not_silently_selected_for_routing():
    with_provider = provider(duplicate=True)
    try:
        report = probe(targets(), with_provider, sleeper=lambda _: None)
    finally:
        with_provider.close()
    assert report["locations"][0]["matching_candidates"] == 2
    assert report["locations"][0]["identity_unique"] is False
    assert sum(r["status"] == "unresolved_location" for r in report["legs"]) == 2
    assert report["requests"] == 4


@pytest.mark.parametrize("authorized,enabled,requests", [(False, True, 1), (True, False, 0)])
def test_key_failure_stops_requests_and_disabled_probe_stays_offline(authorized, enabled, requests):
    with_provider = provider(unauthorized=not authorized, enabled=enabled)
    try:
        report = probe(targets(), with_provider, sleeper=lambda _: None)
    finally:
        with_provider.close()
    assert report["requests"] == requests
    assert not any(r["status"] == "estimated" for r in report["legs"])
    assert report["formal_operations_generated"] is False
