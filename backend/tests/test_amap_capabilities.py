"""M4 synthetic protocol/security tests; never use a real map credential or network."""
from dataclasses import replace
from datetime import datetime
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from app.city.engine.amap import AmapProvider, Poi, Quote, quote_to_engine_estimate

NOW = datetime.fromisoformat('2030-05-01T10:00:00+08:00')
SECRET = 'only-a-fake-test-key'


def place(**changes):
    return SimpleNamespace(**dict(fact_id='TEST-A', poi_id='TEST-POI', longitude=114.3,
                                   latitude=34.8, coordinate_system='GCJ02') | changes)


def provider(handler, **kwargs):
    return AmapProvider(SECRET, enabled=True, terms_reviewed=True, api_version='v5',
                        transport=httpx.MockTransport(handler), retries=0, clock=lambda: 100, **kwargs)


def data(mode='walking', **changes):
    path = {'distance': '800.1', 'cost': {'duration': '600.2'},
            'steps': [{'polyline': '114.3,34.8;114.31,34.81'}]}
    if mode == 'transit':
        path['segments'] = [{'walking': {'distance': '100', 'duration': '90'},
                             'bus': {'buslines': [{'name': '[TEST]'}, {'name': '[TEST alternative]'}]}},
                            {'walking': {'distance': '50', 'duration': '60'},
                             'bus': {'buslines': [{'name': '[TEST second bus]'}]}}]
    path.update(changes)
    return {'status': '1', 'infocode': '10000', 'info': 'OK',
            'route': {'transits' if mode == 'transit' else 'paths': [path]}}


def quote(p, mode='walking', **kwargs):
    return p.estimate(place(), place(fact_id='TEST-B'), mode, NOW, NOW, **kwargs)


@pytest.mark.parametrize('mode', ['walking', 'driving', 'transit'])
def test_v5_units_optional_fields_and_request_parameters(mode):
    def handler(request):
        assert request.url.path.startswith('/v5/direction/')
        assert request.url.params['show_fields'] == 'cost,polyline'
        if mode == 'transit':
            assert request.url.params['city1'] == request.url.params['city2'] == '999'
            assert request.url.params['time'] == '10-00'
            assert request.url.params['date'] == '2030-05-01'
            assert 'city' not in request.url.params
        return httpx.Response(200, json=data(mode))
    p = provider(handler)
    try:
        q = quote(p, mode, citycode='999')
        assert q.status == 'estimated' and q.duration_seconds == 601 and q.distance_m == 800.1
        assert q.cost_fen == (0 if mode == 'walking' else None)
        assert q.source_reference.startswith('/v5/') and '?' not in q.source_reference
        assert q.walking_seconds == (601 if mode == 'walking' else 150 if mode == 'transit' else None)
        if mode == 'transit':
            assert q.transfers == 1 and q.transit_lines_count == 2 and q.total_includes_wait is True
        assert quote_to_engine_estimate(q, 'TEST-A', 'TEST-B', mode).lower_seconds is None
    finally:
        p.close()


@pytest.mark.parametrize('path_changes', [
    {'cost': {}}, {'cost': []}, {'cost': {'duration': 'nan'}}, {'distance': []},
    {'cost': {'duration': True}}, {'distance': '-1'}, {'steps': {}}, {'distance': '0'}])
def test_malformed_required_route_fields_never_become_zero(path_changes):
    p = provider(lambda _: httpx.Response(200, json=data(**path_changes)))
    q = quote(p); p.close()
    assert q.status == 'invalid_response' and q.duration_seconds is None


@pytest.mark.parametrize('code,status,halts', [('10001', 'authorization_or_provider_error', True),
    ('10009', 'authorization_or_provider_error', True), ('10002', 'permission_denied', False),
    ('10012', 'permission_denied', False), ('10041', 'permission_denied', False),
    ('10003', 'provider_rate_limited', True), ('10044', 'provider_rate_limited', True),
    ('40000', 'quota_or_service_expired', True), ('40002', 'quota_or_service_expired', True),
    ('20802', 'no_route', False), ('20000', 'invalid_parameters', False)])
def test_http_200_business_error_and_stop_policy(code, status, halts):
    p = provider(lambda _: httpx.Response(200, json={
        'status': '0', 'infocode': code, 'info': 'URL?key=' + SECRET}))
    q = quote(p)
    assert q.status == status and q.duration_seconds is None
    assert bool(p.halted) == halts
    assert p.events[0]['http_status'] == 200 and p.events[0]['infocode'] == code
    assert SECRET not in json.dumps(p.events) and p.events[0]['info'] is None
    if halts:
        quote(p)
        assert p.request_count == 1
    p.close()


@pytest.mark.parametrize('http_status,expected', [(401, 'authorization_or_provider_error'),
    (403, 'authorization_or_provider_error'), (429, 'provider_rate_limited'),
    (503, 'provider_unavailable'), (302, 'http_error')])
def test_http_errors_have_no_hidden_retries(http_status, expected):
    p = provider(lambda _: httpx.Response(http_status))
    assert quote(p).status == expected and p.request_count == 1
    p.close()


def test_timeout_invalid_json_oversized_and_nonobject():
    def timeout(request):
        raise httpx.ReadTimeout('https://example.invalid/?key=' + SECRET, request=request)
    for handler, expected in [(timeout, 'timeout_or_network'),
        (lambda _: httpx.Response(200, text='no json'), 'invalid_response'),
        (lambda _: httpx.Response(200, json=[]), 'invalid_response'),
        (lambda _: httpx.Response(200, content=b'x' * 2_000_001), 'response_too_large')]:
        p = provider(handler); q = quote(p); p.close()
        assert q.status == expected and p.request_count == 1
        assert SECRET not in repr(q) + json.dumps(p.events)


@pytest.mark.parametrize('changes', [{'longitude': 34.8, 'latitude': 114.3},
    {'coordinate_system': 'WGS84'}, {'coordinate_system': 'unknown'}, {'longitude': None},
    {'longitude': 116.4, 'latitude': 39.9}, {'longitude': float('nan')}])
def test_invalid_or_nonlocal_coordinates_never_send(changes):
    p = provider(lambda _: pytest.fail('should not request'))
    q = p.estimate(place(**changes), place(), 'walking', NOW, NOW); p.close()
    assert q.status == 'coordinate_mismatch' and p.request_count == 0


def test_no_key_and_disabled_and_js_key_cannot_activate(monkeypatch):
    monkeypatch.delenv('AMAP_WEB_SERVICE_KEY', raising=False)
    monkeypatch.setenv('VITE_AMAP_KEY', SECRET)
    p = AmapProvider.from_environment(enabled=True, terms_reviewed=True)
    assert quote(p).status == 'disabled' and p.request_count == 0
    p.close()
    for settings in [dict(enabled=False, terms_reviewed=True), dict(enabled=True, terms_reviewed=False)]:
        p = AmapProvider(SECRET, **settings)
        assert quote(p).status == 'disabled'; p.close()


def test_missing_segments_are_unknown_not_total_duration_or_zero():
    payload = data('transit', segments=[])
    p = provider(lambda _: httpx.Response(200, json=payload))
    q = quote(p, 'transit', citycode='999'); p.close()
    assert q.status == 'estimated' and q.walking_seconds is None and q.walking_distance_m is None


def test_missing_walking_segment_duration_is_unknown():
    payload = data('transit', segments=[{'walking': {'distance': '50'}}])
    p = provider(lambda _: httpx.Response(200, json=payload))
    q = quote(p, 'transit', citycode='999'); p.close()
    assert q.status == 'estimated' and q.walking_seconds is None and q.walking_distance_m == 50


def test_no_route_and_missing_citycode_and_poi_parameters():
    p = provider(lambda _: httpx.Response(200, json={'status': '1', 'route': {'paths': []}}))
    assert quote(p).status == 'no_route'; p.close()
    p = provider(lambda _: pytest.fail('should not send'))
    assert quote(p, 'transit').status == 'citycode_required'
    assert quote(p, use_poi_ids=True).status == 'poi_id_not_supported'
    assert p.request_count == 0; p.close()
    def handler(request):
        assert request.url.params['origin_id'] == request.url.params['destination_id'] == 'TEST-POI'
        return httpx.Response(200, json=data('driving'))
    p = provider(handler)
    assert quote(p, 'driving', use_poi_ids=True).status == 'estimated'; p.close()


def poi_row(**changes):
    return {'id': 'TEST-POI', 'name': '[TEST]', 'location': '114.3,34.8', 'adcode': '410202',
            'citycode': '999', 'children': [{'name': '[TEST] entrance candidate'}], 'biz_ext': {'cost': '20'}} | changes


@pytest.mark.parametrize('version', ['v3', 'v5'])
def test_poi_city_limit_version_and_entrance_boundary(version):
    def handler(request):
        params = request.url.params
        assert params['citylimit' if version == 'v3' else 'city_limit'] == 'true'
        assert params['city' if version == 'v3' else 'region'] == '410200'
        assert params['page' if version == 'v3' else 'page_num'] == '1'
        return httpx.Response(200, json={'status': '1', 'pois': [poi_row(), poi_row(adcode='110101')]})
    p = provider(handler)
    result = p.search_pois('[TEST]', version=version); p.close()
    assert result.status == 'found' and len(result.pois) == result.rejected_count == 1
    assert result.pois[0].children and not result.pois[0].entrance_verified
    assert result.pois[0].coordinate_role == 'provider_poi_not_verified_entrance'
    assert '[TEST]' not in repr(result)


@pytest.mark.parametrize('rows,status', [([], 'no_poi'), (None, 'invalid_response'),
    ([poi_row(location='34.8,114.3')], 'invalid_poi'), ([poi_row(location=[])], 'invalid_poi'),
    ([poi_row(adcode=[])], 'invalid_poi'), ([None], 'invalid_poi')])
def test_empty_and_invalid_pois(rows, status):
    p = provider(lambda _: httpx.Response(200, json={'status': '1', 'pois': rows}))
    assert p.search_pois('[TEST]').status == status; p.close()


def test_search_wrong_city_and_invalid_input_no_send():
    p = provider(lambda _: pytest.fail('should not send'))
    assert p.search_pois('[TEST]', city='110000').status == 'wrong_city'
    assert p.search_pois('').status == 'invalid_parameters'
    assert p.search_pois('[TEST]', around=place(coordinate_system='WGS84')).status == 'coordinate_mismatch'
    p.close()


def test_shared_budget_across_search_and_routes_and_log_redaction(caplog):
    moment = [100]
    def handler(request):
        return httpx.Response(200, json=data())
    p = AmapProvider(SECRET, enabled=True, terms_reviewed=True, api_version='v5', retries=0,
                     max_requests=1, transport=httpx.MockTransport(handler), clock=lambda: moment[0])
    with caplog.at_level(logging.INFO, logger='httpx'):
        assert quote(p).status == 'estimated'
    moment[0] += 2
    assert p.search_pois('[TEST]').status == 'request_budget_exhausted'
    assert p.request_count == 1 and SECRET not in caplog.text and '[REDACTED]' in caplog.text
    p.close()


def load_script():
    path = Path(__file__).resolve().parents[2] / 'scripts/evaluate_amap_capabilities.py'
    spec = importlib.util.spec_from_file_location('amap_probe_test', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_live_without_server_key_has_zero_requests(monkeypatch):
    monkeypatch.delenv('AMAP_WEB_SERVICE_KEY', raising=False)
    report = load_script().run(live=True)
    assert report['status'] == 'BLOCKED_MISSING_WEB_SERVICE_KEY' and report['real_requests'] == 0
    assert all(c['status'] == 'NOT_TESTED' for c in report['capabilities'].values())


def test_offline_experiment_full_path_is_synthetic_and_report_has_no_map_payload(monkeypatch):
    module = load_script()
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    # Advance fake clock per request, avoiding sleeps and real network in unit tests.
    original = module.AmapProvider
    def build(*args, **kwargs):
        ticks = iter(range(100, 500, 2)); kwargs['clock'] = lambda: next(ticks)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'AmapProvider', build)
    report = module.run()
    assert report['real_requests'] == 0 and report['mock_requests'] == 11
    assert report['synthetic_engine']['status'] == 'feasible'
    assert report['synthetic_engine']['walking_limit_rejects']
    assert report['cases']['transit']['status'] == 'estimated'
    assert report['cases']['walking_forward']['engine_gate']['solver_status_without_approved_journeys'] == 'needs_data'
    assert all(c['status'] == 'NOT_TESTED' for c in report['capabilities'].values())
    serialized = json.dumps(report)
    assert all(x not in serialized for x in ('114.3', 'TEST-Q', 'TEST-K', 'TEST-R', 'SYNTHETIC-ONLY', 'polyline', '800'))
    assert not report['database_accessed'] and not report['raw_provider_data_persisted']


def test_request_cap_is_global_not_per_endpoint(monkeypatch):
    module = load_script()
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    report = module.run(request_cap=1)
    assert report['mock_requests'] == 1 and report['real_requests'] == 0
    with pytest.raises(ValueError):
        module.run(request_cap=21)
