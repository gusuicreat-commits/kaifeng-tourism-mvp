"""Bounded continuation of M4 first-pass probe; aggregate budget stays <=20.

No raw map content is written. Uses scripts/evaluate_amap_capabilities.py formats.
A synthetic snapshot experiment is separate from formal traffic admission.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time

from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.city.engine.amap import AmapProvider
from evaluate_amap_capabilities import engine_gate_check
from amap_snapshot_experiment import snapshot_checks


def complete(prior, *, provider=None, pause=time.sleep):
    used = prior.get('real_requests', 0)
    if prior.get('mode') != 'live' or not isinstance(used, int) or not 1 <= used < 20:
        raise ValueError('requires a live first-pass report with remaining aggregate budget')
    own_provider = provider is None
    if own_provider:
        provider = AmapProvider.from_environment(enabled=True, terms_reviewed=True,
            api_version='v5', max_requests=20-used, retries=0, qps=1, timeout_seconds=10)
    if provider.max_requests > 20-used:
        raise ValueError('provider exceeds remaining aggregate budget')
    if not provider.enabled:
        raise ValueError('Web Service credential missing or provider disabled')
    report = json.loads(json.dumps(prior))
    started = time.monotonic()
    quotes = {}
    def call(fn, *args, **kwargs):
        if provider.request_count and not provider.halted:
            pause(1.05)
        return fn(*args, **kwargs)
    def pick(search, name):
        matched = [p for p in search.pois if p.name == name]
        return matched[0] if len(matched) == 1 else None
    def capability(name, status):
        state = ('AVAILABLE' if status in ('estimated', 'found') else 'NOT_TESTED'
                 if status in ('request_budget_exhausted', 'local_rate_limited', 'disabled', 'citycode_required')
                 else 'PARTIAL' if status in ('timeout_or_network', 'provider_unavailable', 'invalid_response',
                                             'no_route', 'no_poi', 'invalid_poi', 'http_error') else 'UNAVAILABLE')
        report['capabilities'][name] = {'status': state, 'reason': status}
    def record_quote(label, q):
        report['cases'][label] = {'status': q.status, 'distance_available': q.distance_m is not None,
            'duration_available': q.duration_seconds is not None, 'geometry_available': q.geometry_available,
            'walking_seconds_available': q.walking_seconds is not None,
            'cost_available': q.cost_fen is not None, 'wait_included_per_v5_contract': q.total_includes_wait}
        if q.status == 'estimated':
            report['cases'][label]['engine_gate'] = engine_gate_check(q, label if label in ('driving','transit') else 'walking')
    try:
        q = call(provider.search_pois, '清明上河园', version='v5')
        capability('poi_v5', q.status)
        if provider.halted:
            return report
        k = call(provider.search_pois, '开封府', version='v3')
        if provider.halted:
            return report
        if not pick(q, '清明上河园'):
            q = call(provider.search_pois, '清明上河园', version='v3')
        a, b = pick(q, '清明上河园'), pick(k, '开封府')
        if not a or not b or provider.halted:
            report['followup_stop_reason'] = 'missing_or_ambiguous_identity_or_provider_halted'
            return report
        now = datetime.now().astimezone()
        report['cases']['followup_identity'] = {'both_exact_unique': True, 'manual_verification': False,
            'business_fields_present': bool(a.business or b.business),
            'children_present': bool(a.children or b.children)}
        for label, origin, dest, mode in [('walking_forward',a,b,'walking'), ('walking_reverse',b,a,'walking'),
                                          ('driving',a,b,'driving'), ('transit',a,b,'transit')]:
            citycode = origin.citycode if origin.citycode == dest.citycode else None
            quotes[label] = call(provider.estimate, origin, dest, mode, now, now, citycode=citycode)
            record_quote(label, quotes[label])
            if label != 'walking_reverse':
                capability('walking' if label == 'walking_forward' else mode, quotes[label].status)
            if provider.halted:
                return report
        forward, reverse = quotes['walking_forward'], quotes['walking_reverse']
        report['cases']['direction_comparison'] = {'both_returned': forward.status == reverse.status == 'estimated',
            'different': (forward.distance_m,forward.duration_seconds) != (reverse.distance_m,reverse.duration_seconds)
                         if forward.status == reverse.status == 'estimated' else None,
            'symmetric_assumed': False}
        dining = call(provider.search_pois, '', around=a, types='050000', version='v3')
        capability('restaurant_search', dining.status)
        meal_quotes = None
        if dining.pois and not provider.halted:
            meal = dining.pois[0]
            report['cases']['dining_fields'] = {'provider_business_present': bool(meal.business),
                'dish_supply_verified': False, 'opening_verified': False, 'price_verified': False}
            first = call(provider.estimate, a, meal, 'walking', now, now)
            record_quote('walking_to_dining_candidate', first)
            second = call(provider.estimate, meal, b, 'walking', now, now)
            record_quote('walking_dining_to_destination', second)
            meal_quotes = (first,second)
        report['snapshot_experiment'] = snapshot_checks({
            'walking': quotes['walking_forward'], 'driving': quotes['driving'], 'transit': quotes['transit']},
            dining_quotes=meal_quotes)
        report['capabilities']['real_engine_integration'] = {'status': 'PARTIAL',
            'reason': 'API numeric snapshot consumed by synthetic CP-SAT scenario; formal admission still needs data'}
        return report
    except Exception as exc:
        # Account for every attempt even when local parsing/experiments fail.
        # Never serialize exception messages, provider URLs or payloads.
        report['followup_stop_reason'] = 'local_experiment_error'
        report['local_error_type'] = type(exc).__name__
        return report
    finally:
        events = []
        for item in provider.events:
            event = dict(item); event['sequence'] += used; events.append(event)
        report['events'] += events
        report['real_requests'] = used + provider.request_count
        report['successful_requests'] = sum(e['status'] == 'success' for e in report['events'])
        report['failed_requests'] = report['real_requests'] - report['successful_requests']
        report['elapsed_ms'] = round(prior['elapsed_ms'] + (time.monotonic()-started)*1000, 2)
        report['continuation'] = {'requests': provider.request_count, 'timeout_seconds': provider.timeout,
                                  'automatic_retries': 0, 'aggregate_cap': 20}
        report['completed_at'] = datetime.now().astimezone().isoformat()
        if own_provider:
            provider.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--report', type=Path, default=ROOT/'docs/reports/amap-real-api-capability.json')
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({'status':'NOT_REQUESTED','real_requests':0})); return 0
    load_dotenv(ROOT/'.env')
    prior = json.loads(args.report.read_text())
    try:
        report = complete(prior)
    except ValueError as exc:
        print(json.dumps({'status':'NOT_STARTED','reason':str(exc)})); return 1
    # Allowlisted report only; no provider payloads retained.
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
