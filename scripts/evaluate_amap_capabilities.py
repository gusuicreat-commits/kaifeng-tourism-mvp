"""M4 capability probe. Default: synthetic Mock only. --live: <=20 real requests.

No database, web server, Kimi, raw-response files, persistent matrix or geometry.
Only allowlisted capability verdicts/diagnostics may leave this process.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.city.engine.amap import AmapProvider, quote_to_engine_estimate

CAPABILITIES = ('poi_v3', 'poi_v5', 'restaurant_search', 'entrance_identity', 'walking',
                'driving', 'transit', 'poi_id_routing', 'real_engine_integration')


def synthetic_transport(request):
    """Protocol fixture made here, not copied from a provider response."""
    if '/place/' in request.url.path:
        name = request.url.params.get('keywords') or '[TEST] 餐饮'
        return httpx.Response(200, json={'status': '1', 'info': 'OK', 'infocode': '10000', 'pois': [
            {'id': 'TEST-Q' if name == '清明上河园' else 'TEST-K' if name == '开封府' else 'TEST-R',
             'name': name, 'location': '114.300000,34.800000', 'adcode': '410202',
             'citycode': '999', 'address': '[SYNTHETIC]', 'type': '[SYNTHETIC]'}]})
    transit = '/transit/' in request.url.path
    p = {'distance': '800', 'cost': {'duration': '600'}, 'steps': [
        {'polyline': '114.3,34.8;114.301,34.801'}]}
    if transit:
        p['segments'] = [{'walking': {'duration': '120', 'distance': '100', 'steps': []},
                          'bus': {'buslines': [{'name': '[TEST]'}]}}]
    return httpx.Response(200, json={'status': '1', 'info': 'OK', 'infocode': '10000',
                                   'route': {'transits' if transit else 'paths': [p]}})


def engine_gate_check(quote, mode):
    """No third-party payload enters a DB or is upgraded to a surveyed fact.

    Uses existing pure plan_trip with empty operations, synthetic requirements/facts.
    A real quote is inspected by the admission gate only; it must remain unknown.
    """
    from app.city import facts
    from app.city.engine.context import PlanningContext
    from app.city.engine.models import OperationalBundle, Requirements
    from app.city.engine.tools import plan_trip
    fixture = json.loads((ROOT / 'backend/tests/fixtures/engine_synthetic.json').read_text())
    records = facts.FactBundle.model_validate(fixture['facts']).records
    req = Requirements.model_validate(fixture['requirements']).model_copy(update={'modes': (mode,)})
    estimate = quote_to_engine_estimate(quote, req.origin_place_id, req.destination_place_id, mode)
    context = PlanningContext({(r.kind, r.fact_id): r for r in records},
                              OperationalBundle(), datetime.fromisoformat(fixture['assessed_at']), True)
    result = plan_trip(req, context)
    return {'quote_received': quote.status == 'estimated',
            'bounds_unknown': estimate.lower_seconds is None and estimate.upper_seconds is None,
            'solver_status_without_approved_journeys': result.status,
            'formal_integration': 'requires_additional_data',
            'database_accessed': False, 'navigation_certified': False,
            'reason': 'verified entrances/path + licensed validity/envelope policy required',
            'transit_walking_limit_supported': False if mode == 'transit' else None}


def offline_engine_check():
    """Existing engine smoke test with ONLY its pre-existing synthetic fixture."""
    from app.city import facts
    from app.city.engine.context import PlanningContext
    from app.city.engine.models import OperationalBundle, Requirements
    from app.city.engine.tools import plan_trip
    f = json.loads((ROOT / 'backend/tests/fixtures/engine_synthetic.json').read_text())
    records = facts.FactBundle.model_validate(f['facts']).records
    ctx = PlanningContext({(r.kind, r.fact_id): r for r in records}, OperationalBundle.model_validate(f['operations']),
                          datetime.fromisoformat(f['assessed_at']), True)
    req = Requirements.model_validate(f['requirements'])
    result = plan_trip(req, ctx)
    limited = plan_trip(req.model_copy(update={'max_walking_seconds': 1}), ctx)
    return {'data': 'synthetic_only', 'status': result.status,
            'walking_limit_rejects': limited.status != 'feasible',
            'database_accessed': False, 'real_transport_consumed': False}


def run(*, live=False, request_cap=20):
    if not 1 <= request_cap <= 20:
        raise ValueError('request cap must be 1..20')
    configured = bool(os.getenv('AMAP_WEB_SERVICE_KEY', '').strip())
    report = {'schema_version': 'amap-capability/1', 'tested_at': datetime.now().astimezone().isoformat(),
              'mode': 'live' if live else 'mock_only', 'key_configured': configured,
              'status': 'NOT_TESTED' if live else 'OFFLINE_ONLY',
              'request_cap': request_cap, 'real_requests': 0, 'mock_requests': 0,
              'capabilities': {k: {'status': 'NOT_TESTED', 'reason': 'no live evidence'} for k in CAPABILITIES},
              'raw_provider_data_persisted': False, 'production_provider_enabled': False,
              'database_accessed': False, 'kimi_requests': 0, 'fee_verified': False, 'fee': None,
              'events': [], 'cases': {}}
    if live and not configured:
        report.update(status='BLOCKED_MISSING_WEB_SERVICE_KEY', successful_requests=0, failed_requests=0,
                      elapsed_ms=0)
        return report
    provider = AmapProvider.from_environment(enabled=live, terms_reviewed=live, api_version='v5',
                    retries=0, max_requests=request_cap, qps=1, timeout_seconds=5) if live else AmapProvider(
                    'SYNTHETIC-ONLY', enabled=True, terms_reviewed=True, api_version='v5',
                    retries=0, max_requests=request_cap, qps=1,
                    transport=httpx.MockTransport(synthetic_transport), clock=lambda: time.monotonic())
    started = time.monotonic()
    def call(fn, *args, **kwargs):
        # Low frequency, shared across ALL POI and route calls; retries=0.
        if provider.request_count and not provider.halted:
            time.sleep(1.05)
        return fn(*args, **kwargs)
    def capability(name, ok, status=None):
        local_failure = status in ('disabled', 'request_budget_exhausted', 'local_rate_limited',
                                  'citycode_required', 'coordinate_mismatch', 'poi_id_not_supported')
        report['capabilities'][name] = {
            'status': ('AVAILABLE' if ok else 'NOT_TESTED' if local_failure else 'PARTIAL'
                       if status in ('no_route', 'no_poi', 'invalid_poi', 'timeout_or_network',
                                     'provider_unavailable', 'invalid_response', 'http_error') else 'UNAVAILABLE') if live else 'NOT_TESTED',
            'reason': status if live and status else 'real API result' if live else 'synthetic Mock is not live evidence'}
    def pick(result, exact):
        matches = [p for p in result.pois if p.name == exact]
        # Ambiguous names stay unresolved. No automatic first-result matching.
        return matches[0] if len(matches) == 1 else None
    try:
        q = call(provider.search_pois, '清明上河园', version='v3')
        report['cases']['qingming_search'] = {'status': q.status, 'exact_identity_unique': pick(q, '清明上河园') is not None,
                                             'multiple_candidates': len(q.pois) > 1, 'entrance_verified': False}
        capability('poi_v3', q.status == 'found', q.status)
        # Stop immediately after key/auth/quota failure; never try alternative accounts.
        if provider.halted:
            return finish(report, provider, started, live)
        k = call(provider.search_pois, '开封府', version='v3')
        report['cases']['kaifengfu_search'] = {'status': k.status, 'exact_identity_unique': pick(k, '开封府') is not None,
                                             'multiple_candidates': len(k.pois) > 1, 'entrance_verified': False}
        if provider.halted:
            return finish(report, provider, started, live)
        # v5 is permission-tested separately; its result does not overwrite v3 identity.
        v5 = call(provider.search_pois, '清明上河园', version='v5')
        capability('poi_v5', v5.status == 'found', v5.status)
        if provider.halted:
            return finish(report, provider, started, live)
        origin, destination = pick(q, '清明上河园'), pick(k, '开封府')
        if not origin or not destination:
            report['cases']['routing'] = {'status': 'skipped_ambiguous_or_missing_identity'}
            return finish(report, provider, started, live)
        restaurants = call(provider.search_pois, '', around=origin, types='050000', version='v3')
        capability('restaurant_search', restaurants.status == 'found', restaurants.status)
        if provider.halted:
            return finish(report, provider, started, live)
        report['cases']['entrances'] = {'A': 'POI coordinate only; not verified entrance',
                'B': 'NOT_TESTED_no_manually_verified_entrance',
                'C': 'driving POI IDs supported by schema; evaluated below',
                'child_pois_present': bool(origin.children or destination.children),
                'entrance_verified': False}
        if live:
            report['capabilities']['entrance_identity'] = {'status': 'PARTIAL', 'reason': 'POI identity is not entrance verification'}
        quotes = {}
        now = datetime.now().astimezone()
        pairs = [('walking_forward', origin, destination, 'walking'),
                 ('walking_reverse', destination, origin, 'walking'),
                 ('driving', origin, destination, 'driving'),
                 ('transit', origin, destination, 'transit')]
        if restaurants.pois:
            pairs.append(('walking_to_dining_candidate', origin, restaurants.pois[0], 'walking'))
        for label, a, b, mode in pairs:
            citycode = a.citycode if a.citycode == b.citycode else None
            quote = call(provider.estimate, a, b, mode, now, now, citycode=citycode)
            quotes[label] = quote
            # Only booleans/status in reports: no coordinates, path, line name, POI id, ETA or matrix.
            report['cases'][label] = {'status': quote.status, 'distance_available': quote.distance_m is not None,
                'duration_available': quote.duration_seconds is not None, 'geometry_available': quote.geometry_available,
                'walking_seconds_available': quote.walking_seconds is not None,
                'cost_available': quote.cost_fen is not None, 'wait_included_per_v5_contract': quote.total_includes_wait}
            if label in ('walking_forward', 'driving', 'transit'):
                capability('walking' if label == 'walking_forward' else mode, quote.status == 'estimated', quote.status)
            if quote.status == 'estimated':
                report['cases'][label]['engine_gate'] = engine_gate_check(quote, mode)
            if provider.halted:
                return finish(report, provider, started, live)
        a, b = quotes['walking_forward'], quotes['walking_reverse']
        report['cases']['direction_comparison'] = {'both_returned': a.status == b.status == 'estimated',
            'different': (a.distance_m, a.duration_seconds) != (b.distance_m, b.duration_seconds) if a.status == b.status == 'estimated' else None,
            'symmetric_assumed': False}
        ids = call(provider.estimate, origin, destination, 'driving', now, now, use_poi_ids=True)
        capability('poi_id_routing', ids.status == 'estimated', ids.status)
        baseline = quotes['driving']
        report['cases']['poi_id_comparison'] = {'status': ids.status,
            'changed': (baseline.distance_m, baseline.duration_seconds) != (ids.distance_m, ids.duration_seconds) if baseline.status == ids.status == 'estimated' else None,
            'entrance_verified': False}
        if not provider.halted:
            strategy = call(provider.estimate, origin, destination, 'driving', now, now, strategy=1)
            report['cases']['driving_strategy_comparison'] = {'status': strategy.status,
                'changed': (baseline.distance_m, baseline.duration_seconds) != (strategy.distance_m, strategy.duration_seconds) if baseline.status == strategy.status == 'estimated' else None}
        if live and any(v.status == 'estimated' for v in quotes.values()):
            report['capabilities']['real_engine_integration'] = {'status': 'PARTIAL',
                'reason': 'admission gate only; verified entrances and horizon bounds unavailable'}
        return finish(report, provider, started, live)
    finally:
        provider.close()


def finish(report, provider, started, live):
    report['events'] = list(provider.events)
    report['real_requests' if live else 'mock_requests'] = provider.request_count
    report['successful_requests'] = sum(e['status'] == 'success' for e in provider.events)
    report['failed_requests'] = provider.request_count - report['successful_requests']
    report['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
    if live:
        report['status'] = 'PARTIAL' if report['successful_requests'] else 'BLOCKED'
    else:
        report['synthetic_engine'] = offline_engine_check()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly allow a single bounded run of real AMap requests')
    parser.add_argument('--request-cap', type=int, choices=range(1, 21), default=20)
    parser.add_argument('--output', type=Path, help='Write allowlisted verdicts only, never raw POI/route data')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    report = run(live=args.live, request_cap=args.request_cap)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n')
    print(text)
    return 1 if report['status'].startswith('BLOCKED') else 0


if __name__ == '__main__':
    raise SystemExit(main())
