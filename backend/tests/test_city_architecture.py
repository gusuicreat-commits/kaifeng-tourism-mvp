"""Synthetic contract tests; fixtures are NOT Kaifeng facts or field evidence."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.city.schema import CityChangeRequest, CityTrip, Evidence, PositionAnchor, Stop, TravelEstimate
from app.city.timing import ConnectionInput, ShowTiming, assess_connection
from app.city.travel import SurveyedWalkingProvider, UnavailableTravelTimeProvider, safe_estimate
from app.city.workflow import confirmation_required, prepare_remaining


def at(clock):
    return datetime.fromisoformat('2026-10-21T' + clock + ':00+08:00')


def evidence(**patch):
    return Evidence.model_validate(dict(evidence_id='test-evidence', revision='1', kind='verified',
                                        source='SYNTHETIC TEST ONLY', checked_at=at('08:00'),
                                        valid_from=at('08:00'), valid_until=at('23:00')) | patch)


def travel(**patch):
    return TravelEstimate.model_validate(dict(origin_place_id='a', destination_place_id='b', mode='walking',
                                              lower_seconds=600, upper_seconds=600, evidence=evidence(),
                                              path_status='verified', path_ref='test-corridor',
                                              coordinate_system='WGS84') | patch)


def connection(**patch):
    return ConnectionInput.model_validate(dict(previous_ends_at=at('14:00'), assessed_at=at('09:00'),
                                               travel=travel(), boundary_seconds=0, boundary_evidence=evidence(),
                                               show=ShowTiming(place_id='b', starts_at=at('14:20'), ends_at=at('14:40'),
                                                               evidence=evidence())) | patch)


def trip(**patch):
    return CityTrip.model_validate(dict(trip_id=uuid4(), version=3, route_version=1, business_date='2026-10-21',
                                        starts_at=at('09:00'), ends_at=at('21:00'), origin_place_id='gate',
                                        destination_place_id='hotel',
                                        stops=[Stop(stop_id='meal', place_id='restaurant', kind='dining', duration_seconds=1800),
                                               Stop(stop_id='show-2', place_id='stage', kind='show', session_id='evening', locked=True)]) | patch)


def test_twenty_minute_gap_is_five_minute_slack_not_a_queue_prediction():
    result = assess_connection(connection())
    assert result.status == 'slack'
    assert result.slack_seconds == 300
    assert result.latest_arrival == at('14:15')
    assert result.planned_wait_seconds == 600
    assert result.navigation_certified is False
    assert 'queue' not in str(result.model_dump()).lower()


def test_conservative_eta_and_earlier_admission_cutoff_take_priority():
    data = connection(travel=travel(upper_seconds=900), show=ShowTiming(
        place_id='b', starts_at=at('14:20'), ends_at=at('14:40'), evidence=evidence(),
        admission_deadline=at('14:12'), admission_evidence=evidence()))
    result = assess_connection(data)
    assert result.status == 'conflict' and result.slack_seconds == -180
    assert result.latest_arrival == at('14:12')


def test_unknown_show_end_does_not_erase_a_known_conflict():
    show = ShowTiming(place_id='b', starts_at=at('14:20'), evidence=evidence())
    result = assess_connection(connection(show=show, travel=travel(upper_seconds=1000)))
    assert result.status == 'conflict'
    assert 'SHOW_END_UNKNOWN' in result.issues
    assert 'ARRIVAL_DEADLINE_CONFLICT' in result.issues


@pytest.mark.parametrize('patch,issue', [
    ({'show': ShowTiming(place_id='b', starts_at=at('14:20'), evidence=evidence())}, 'SHOW_END_UNKNOWN'),
    ({'travel': travel(lower_seconds=None, upper_seconds=None)}, 'TRAVEL_TIME_OR_PATH_UNKNOWN'),
    ({'travel': travel(path_status='demo', coordinate_system='local_demo')}, 'TRAVEL_TIME_OR_PATH_UNKNOWN'),
    ({'travel': travel(evidence=evidence(kind='reported'))}, 'TRAVEL_TIME_OR_PATH_UNKNOWN'),
    ({'boundary_seconds': None}, 'BOUNDARY_RULE_OR_DURATION_UNKNOWN'),
    ({'boundary_evidence': evidence(kind='estimate')}, 'BOUNDARY_RULE_OR_DURATION_UNKNOWN'),
    ({'show': ShowTiming(place_id='b', starts_at=at('14:20'), ends_at=at('14:40'),
                        evidence=evidence(valid_until=at('14:00')))}, 'SHOW_FACT_UNVERIFIED_OR_EXPIRED'),
])
def test_missing_or_unqualified_facts_never_pass(patch, issue):
    result = assess_connection(connection(**patch))
    assert result.status == 'pending' and issue in result.issues


def test_zero_boundary_requires_an_explicit_rule_and_unverified_cutoff_is_pending():
    result = assess_connection(connection(boundary_evidence=None))
    assert result.status == 'pending' and result.conservative_arrival is None
    result = assess_connection(connection(show=ShowTiming(place_id='b', starts_at=at('14:20'), ends_at=at('14:40'),
                                                        evidence=evidence(), admission_deadline=at('14:00'))))
    assert result.status == 'pending'
    assert result.latest_arrival == at('14:15')  # Unverified cutoff is not used as a fact.


def test_missing_eta_is_not_zero_and_does_not_hide_an_already_missed_deadline():
    result = assess_connection(connection(previous_ends_at=at('14:19'),
                                         travel=travel(lower_seconds=None, upper_seconds=None)))
    assert result.status == 'conflict' and result.conservative_arrival is None


def test_provider_direction_mode_freshness_and_failures():
    provider = SurveyedWalkingProvider((travel(),))
    assert safe_estimate(provider, 'a', 'b', 'walking', at('14:00')).upper_seconds == 600
    for origin, dest, mode, depart in [('b', 'a', 'walking', at('14:00')),
                                      ('a', 'b', 'driving', at('14:00')),
                                      ('a', 'b', 'walking', at('23:10'))]:
        assert safe_estimate(provider, origin, dest, mode, depart).upper_seconds is None
    assert safe_estimate(UnavailableTravelTimeProvider(), 'a', 'b', 'walking', at('14:00')).upper_seconds is None

    class Broken:
        def estimate(self, *args):
            raise RuntimeError('sensitive provider details')

    result = safe_estimate(Broken(), 'a', 'b', 'walking', at('14:00'))
    assert result.upper_seconds is None and 'sensitive' not in result.model_dump_json()

    class WrongEndpoint:
        def estimate(self, *args):
            return travel(destination_place_id='wrong-stage')

    assert safe_estimate(WrongEndpoint(), 'a', 'b', 'walking', at('14:00')).upper_seconds is None


def test_evidence_missing_expiry_does_not_become_permanently_valid():
    assert not evidence(valid_until=None).usable_at(at('14:00'))
    with pytest.raises(ValidationError):
        evidence(kind='verified', source=None)


def test_same_place_two_sessions_are_distinct_but_same_session_is_not_duplicated():
    first = Stop(stop_id='show-1', place_id='stage', kind='show', session_id='afternoon')
    second = Stop(stop_id='show-2', place_id='stage', kind='show', session_id='evening')
    assert len(trip(stops=[first, second]).stops) == 2
    with pytest.raises(ValidationError):
        trip(stops=[first, Stop(stop_id='other-id', place_id='stage', kind='show', session_id='afternoon')])


def test_remaining_work_uses_current_anchor_and_keeps_history_hard_goals_and_return():
    completed = Stop(stop_id='show-1', place_id='stage', kind='show', session_id='afternoon', progress='completed',
                     actual_start=at('13:00'), actual_end=at('13:30'), locked=True)
    current = trip(stops=[completed, *trip().stops])
    anchor = PositionAnchor(place_id='exit', confirmed_at=at('14:00'))
    work = prepare_remaining(current, anchor, {'exit', 'gate'}, at('14:05'))
    assert work.origin.place_id == 'exit' and work.destination_place_id == 'hotel'
    assert work.completed_prefix == (completed,) and work.locked_stop_ids == ('show-2',)
    assert [s.stop_id for s in work.remaining_stops] == ['meal', 'show-2']
    assert work.remaining_seconds == 24900
    assert current.origin_place_id == 'gate' and current.version == 3  # No commit occurred.


@pytest.mark.parametrize('place,confirmed', [('unknown', '14:00'), ('exit', '13:00'), ('exit', '14:10')])
def test_unknown_stale_or_future_position_requires_manual_confirmation(place, confirmed):
    with pytest.raises(ValueError):
        prepare_remaining(trip(), PositionAnchor(place_id=place, confirmed_at=at(confirmed)), {'exit'}, at('14:05'))


def test_completed_prefix_and_locked_goal_cannot_be_silently_rewritten():
    completed = Stop(stop_id='old', place_id='stage', kind='show', session_id='old-session', progress='completed',
                     actual_start=at('13:00'), actual_end=at('13:30'))
    with pytest.raises(ValidationError):
        trip(stops=[trip().stops[0], completed])
    with pytest.raises(ValidationError):
        Stop(stop_id='locked', place_id='stage', kind='show', session_id='test', progress='skipped', locked=True)
    current = trip()
    def request(stop, channel='control', version=3):
        return CityChangeRequest(request_id=uuid4(), trip_id=current.trip_id, expected_version=version,
                                 channel=channel, change={'operation': 'remove_stop', 'stop_id': stop})
    assert confirmation_required(current, request('show-2'))
    assert not confirmation_required(current, request('meal'))
    assert confirmation_required(current, request('meal', 'natural_language'))
    with pytest.raises(ValueError):
        confirmation_required(current, request('meal', version=2))


def test_untrusted_fields_dish_as_stop_and_coordinate_injection_are_rejected():
    current = trip()
    with pytest.raises(ValidationError):
        CityTrip.model_validate(current.model_dump() | {'owner_user_id': 'another-user'})
    with pytest.raises(ValidationError):
        Stop(stop_id='dish', place_id='dish-only', kind='dish')
    with pytest.raises(ValidationError):
        PositionAnchor(place_id='exit', confirmed_at=at('14:00'), latitude=34.8)
    with pytest.raises(ValidationError):
        connection(travel=travel(destination_place_id='other-stage'))


def test_new_contract_does_not_reinterpret_legacy_trip_and_capabilities_are_honest(client):
    response = client.get('/api/city/v1/capabilities')
    assert response.status_code == 200
    assert response.json()['city_planning_available'] is False
    client.post('/api/v1/sessions')
    legacy = client.post('/api/v1/trips', json={'request_id': str(uuid4())})
    assert legacy.status_code == 201 and 'interview' in legacy.json() and 'city_id' not in legacy.json()
    city = client.post('/api/city/v1/trips', json={'request_id': str(uuid4())})
    assert city.status_code == 201 and city.json()['schema_version'] == 'city-state/1'
    assert city.json()['readiness']['planning_available'] is False
