"""Pure in-memory numeric compatibility experiment. NEVER an operational adapter.

The API ETA is deliberately a fixed TEST CONSTANT in a synthetic calendar,
not a calibrated bound, future forecast, surveyed corridor or verified entrance.
Only booleans/status may be exported; no numeric traffic matrix or POIs are saved.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path


def snapshot_checks(quotes, *, dining_quotes=None):
    from app.city import facts
    from app.city.engine.context import PlanningContext
    from app.city.engine.models import Goal, Journey, OperationalBundle, Requirements
    from app.city.engine.tools import plan_trip
    from app.city.schema import Evidence, TravelEstimate
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root / 'backend/tests/fixtures/engine_synthetic.json').read_text())
    records = facts.FactBundle.model_validate(fixture['facts']).records
    ctx_facts = {(r.kind, r.fact_id): r for r in records}
    base = Requirements.model_validate(fixture['requirements'])
    # Synthetic date/hours, visit durations, venue rules and fixed activity window.
    # The two scenario nodes are mapped to the matched API endpoints by the caller.
    base = base.model_copy(update={
        'ends_at': base.starts_at + timedelta(hours=4), 'alternatives': 1,
        'goals': (Goal(goal_id='visit_a', target_kind='place', target_id='origin',
                       required=True, duration_seconds=1800),
                  Goal(goal_id='visit_b', target_kind='place', target_id='hotel', required=True,
                       duration_seconds=1800, window_start=base.starts_at + timedelta(hours=2),
                       window_end=base.starts_at + timedelta(hours=3)))})
    evidence = Evidence(evidence_id='snapshot:test-assumption', revision='1', kind='demo',
        source='SYNTHETIC fixed-duration scenario using API point ETA; NOT a calibrated bound',
        checked_at=datetime.fromisoformat('2020-01-01T00:00:00+08:00'),
        valid_from=datetime.fromisoformat('2020-01-01T00:00:00+08:00'),
        valid_until=datetime.fromisoformat('2035-01-01T00:00:00+08:00'))
    def arc(a, b, mode, seconds, distance):
        return Journey(journey_id=f'snapshot:{a}:{b}:{mode}', time_basis='synthetic',
            distance_m=distance, travel=TravelEstimate(origin_place_id=a, destination_place_id=b,
            mode=mode, lower_seconds=seconds, upper_seconds=seconds, evidence=evidence,
            path_status='demo', path_ref='snapshot:test-constant', coordinate_system='local_demo'))
    output = {'scope': 'numeric_compatibility_only', 'data': 'API point estimates + synthetic scenario',
              'calendar_opening_visit_windows': 'synthetic_2030_fixture_not_a_forecast',
              'api_point_eta_treated_as': 'fixed_test_constant_NOT_confidence_bounds',
              'formal_admission_bypassed': False, 'production_adapter_used': False,
              'database_accessed': False, 'navigation_certified': False, 'modes': {}}
    for mode, q in quotes.items():
        if q.status != 'estimated':
            output['modes'][mode] = {'status': 'NOT_TESTED', 'reason': q.status}
            continue
        journeys = [arc('origin', 'origin', mode, 0, 0), arc('hotel', 'hotel', mode, 0, 0),
                    arc('origin', 'hotel', mode, q.duration_seconds, q.distance_m)]
        ctx = PlanningContext(ctx_facts, OperationalBundle(journeys=tuple(journeys)),
                              datetime.fromisoformat(fixture['assessed_at']), True)
        req = base.model_copy(update={'modes': (mode,)})
        result = plan_trip(req, ctx)
        case = {'status': result.status, 'quote_numbers_consumed': False,
                'required_visits_preserved': False, 'fixed_window_and_return_checked': False,
                'result_evidence': 'synthetic', 'navigation_certified': False}
        if result.itineraries:
            plan = result.itineraries[0]
            case.update(quote_numbers_consumed=any(
                leg.journey.travel.upper_seconds == q.duration_seconds and leg.journey.distance_m == q.distance_m
                for leg in plan.legs), required_visits_preserved=len(plan.activities) == 2,
                fixed_window_and_return_checked=plan.returns_at <= req.ends_at and
                req.goals[1].window_start <= plan.activities[1].starts_at and
                plan.activities[1].departs_at <= req.goals[1].window_end)
        shorter = req.model_copy(update={'ends_at': req.starts_at + timedelta(minutes=30)})
        case['insufficient_time_rejected'] = plan_trip(shorter, ctx).status != 'feasible'
        missing = PlanningContext(ctx_facts, OperationalBundle(), ctx.assessed_at, True)
        case['missing_transport_rejected'] = plan_trip(req, missing).status == 'needs_data'
        if mode == 'walking':
            limited = req.model_copy(update={'max_walking_seconds': max(1, q.duration_seconds - 1)})
            case['hard_walking_limit_rejected'] = plan_trip(limited, ctx).status != 'feasible'
        else:
            case['mixed_mode_total_walking_limit'] = 'NOT_SUPPORTED_not_certified'
        output['modes'][mode] = case
    if dining_quotes and all(q.status == 'estimated' for q in dining_quotes):
        outbound, onward = dining_quotes
        direct = quotes.get('walking')
        if direct and direct.status == 'estimated':
            journeys = [arc('origin', 'origin', 'walking', 0, 0), arc('hotel', 'hotel', 'walking', 0, 0),
                        arc('origin', 'hotel', 'walking', direct.duration_seconds, direct.distance_m),
                        arc('origin', 'restaurant', 'walking', outbound.duration_seconds, outbound.distance_m),
                        arc('restaurant', 'hotel', 'walking', onward.duration_seconds, onward.distance_m)]
            req = base.model_copy(update={'modes': ('walking',), 'goals': base.goals + (
                Goal(goal_id='optional_meal', target_kind='place', target_id='restaurant',
                     kind='dining', required=False, duration_seconds=1200),)})
            ctx = PlanningContext(ctx_facts, OperationalBundle(journeys=tuple(journeys)),
                                  datetime.fromisoformat(fixture['assessed_at']), True)
            result = plan_trip(req, ctx)
            output['optional_dining'] = {'status': result.status,
                'included': any(a.goal_id == 'optional_meal' for p in result.itineraries for a in p.activities),
                'all_selected_transport_from_api_constants_or_same_node_zero': True,
                'opening_and_service_are_synthetic': True}
    return output
