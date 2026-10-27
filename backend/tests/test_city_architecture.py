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


@pytest.mark.parametrize('count',[1,2,3])
def test_multi_day_requirements_have_stable_identity_and_trip_scoped_goals(count):
    from app.city.engine.models import MultiDayRequirements, ReplanRequestV2
    from multi_day_helpers import requirements
    req=MultiDayRequirements.model_validate(requirements(count))
    assert req.day_count==count and req.solve_seconds==1 and len(req.goals)==2
    assert [d.day_id for d in req.days]==['day:'+d.business_date.isoformat() for d in req.days]
    assert req.goals[0].required and len(req.goals[0].allowed_dates)==count
    assert req.goals[1].fixed_date==req.end_date
    assert MultiDayRequirements.model_validate_json(req.model_dump_json())==req
    schema=MultiDayRequirements.model_json_schema()
    assert schema['properties']['day_count']['type']=='integer' and 'day_count' not in schema['required']
    body=ReplanRequestV2(request_id=uuid4(),expected_version=1,requirements=req,scope='selected_future_day',day_id=req.days[-1].day_id)
    assert body.day_id==req.days[-1].day_id
    for patch in ({'day_id':None},{'day_id':'day:2030-05-09'}):
        with pytest.raises(ValidationError):ReplanRequestV2.model_validate(body.model_dump()|patch)


@pytest.mark.parametrize('case,message',[
    ('zero','1–3'),('four','1–3'),('gap','连续'),('duplicate','day_id'),('wrong_id','day_id'),
    ('out_of_scope','作用域'),('fixed_outside','固定日期'),('duplicate_goal','目标 ID'),
    ('too_many_goals','30'),('cross_midnight','同一业务日'),('naive','timezone'),
    ('forged_count','day_count'),('absolute_window','没有交集'),('access_scope','票务'),('no_tag','schema_version')])
def test_multi_day_invalid_ranges_scopes_and_daily_windows_are_rejected(case,message):
    import copy
    from app.city.engine.models import MultiDayRequirements
    from multi_day_helpers import requirements
    data=requirements()
    if case=='zero':data['end_date']='2030-04-30'
    elif case=='four':data['end_date']='2030-05-04'
    elif case=='gap':data['days']=data['days'][:1]
    elif case=='duplicate':data['days'][1]=copy.deepcopy(data['days'][0])
    elif case=='wrong_id':data['days'][1]['day_id']='day:2030-05-01'
    elif case=='out_of_scope':data['goals'][0]['allowed_dates']=['2030-05-09']
    elif case=='fixed_outside':data['goals'][1]['fixed_date']='2030-05-01'
    elif case=='duplicate_goal':data['goals'][1]['goal_id']='flexible'
    elif case=='too_many_goals':data['goals']=[data['goals'][0]|{'goal_id':f'g{i}'} for i in range(31)]
    elif case=='cross_midnight':data['days'][0]['ends_at']='2030-05-02T00:00:00+08:00'
    elif case=='naive':data['days'][0]['starts_at']='2030-05-01T09:00:00'
    elif case=='forged_count':data['day_count']=1
    elif case=='absolute_window':data['goals'][0]['window_start']='2030-05-03T12:00:00+08:00'
    elif case=='access_scope':data['access_confirmations']=[{'place_id':'museum','allowed_dates':['2030-05-03']}]
    elif case=='no_tag':data.pop('schema_version')
    with pytest.raises(ValidationError,match=message):MultiDayRequirements.model_validate(data)


def test_partial_multi_day_constraints_do_not_inherit_first_day_fields():
    from app.city.engine.models import MultiDayConstraints, MultiDayRequirements
    from multi_day_helpers import requirements
    req=requirements();days=req['days']
    days[1]={k:v for k,v in days[1].items() if k=='business_date'}
    constraints=MultiDayConstraints.model_validate({'schema_version':'city-constraints/2',
        'start_date':req['start_date'],'end_date':req['end_date'],'days':days,'modes':['driving']})
    assert constraints.days[1].starts_at is None and constraints.days[1].origin_place_id is None
    assert constraints.days[1].modes is None and constraints.modes==('driving',)
    assert constraints.days[1].model_fields_set=={'business_date','day_id'}
    assert MultiDayConstraints.model_validate({'schema_version':'city-constraints/2'}).days==()
    assert MultiDayConstraints.model_validate({'schema_version':'city-constraints/2','start_date':req['start_date'],
        'end_date':req['end_date'],'goals':req['goals']}).days==()
    with pytest.raises(ValidationError):MultiDayRequirements.model_validate(req)


@pytest.mark.parametrize('model_name',['draft','trip'])
def test_multi_day_prefix_is_daily_and_ids_and_sessions_are_global(model_name):
    import copy
    from app.city.state_schema import CityDraftV2
    from app.city.schema import CityTripV2
    from multi_day_helpers import draft
    data=draft();model=CityDraftV2
    if model_name=='trip':
        data.update(schema_version='city-trip/2',trip_id=uuid4(),version=1);model=CityTripV2
    completed={'stop_id':'done','place_id':'museum','kind':'visit','progress':'completed',
        'actual_start':'2030-05-02T09:00:00+08:00','actual_end':'2030-05-02T09:10:00+08:00','progress_source':'user_reported'}
    pending={'stop_id':'pending','place_id':'museum','kind':'visit'}
    data['days'][0]['stops']=[pending]
    data['days'][1]['stops']=[completed,pending|{'stop_id':'next'}]
    assert model.model_validate(data).days[1].stops[0].progress=='completed'
    broken=copy.deepcopy(data);broken['days'][1]['stops'].reverse()
    with pytest.raises(ValidationError,match='前缀'):model.model_validate(broken)
    broken=copy.deepcopy(data);broken['days'][1]['stops'][1]['stop_id']='pending'
    with pytest.raises(ValidationError,match='重复'):model.model_validate(broken)
    broken=copy.deepcopy(data);broken['days'][1]['stops'][0]['actual_start']='2030-05-01T09:00:00+08:00'
    with pytest.raises(ValidationError,match='业务日|前缀'):model.model_validate(broken)
    for d in data['days']:
        d['stops']=[{'stop_id':'show:'+d['business_date'],'place_id':'stage','kind':'show','session_id':'same'}]
    with pytest.raises(ValidationError,match='场次重复'):model.model_validate(data)


def test_multi_day_route_keeps_original_day_and_distinguishes_active_elapsed_and_evidence():
    import copy
    from app.city.engine.models import MultiDayItinerary
    from multi_day_helpers import itinerary
    data=itinerary();plan=MultiDayItinerary.model_validate(data)
    assert plan.active_seconds==7200 and plan.elapsed_seconds==90000
    assert plan.days[0].original_requirements.starts_at.hour==9
    assert MultiDayItinerary.model_validate_json(plan.model_dump_json())==plan
    for patch in ({'active_seconds':90000},{'elapsed_seconds':7200},{'travel_seconds':1},
                  {'walking_distance_m':1},{'evidence_mode':'verified_with_estimates'},{'cost_unknown':False}):
        with pytest.raises(ValidationError):MultiDayItinerary.model_validate(data|patch)
    for patch in ({'overnights':[]},{'overnights':[data['overnights'][0]|{'place_id':None}]},
                  {'overnights':[data['overnights'][0]|{'place_id':'other'}]}):
        with pytest.raises(ValidationError,match='过夜'):MultiDayItinerary.model_validate(data|patch)
    broken=copy.deepcopy(data);broken['days'][0]['requirements']['starts_at']='2030-05-01T08:30:00+08:00'
    with pytest.raises(ValidationError,match='原始每日'):MultiDayItinerary.model_validate(broken)


@pytest.mark.parametrize('reader,tag',[
    ('read_draft','city-draft/999'),('read_requirements','city-engine/999'),
    ('read_itinerary','city-itinerary/999'),('read_result','city-engine-result/999'),('read_constraints','city-constraints/999'),('read_trip','city-trip/999')])
def test_unknown_schema_is_rejected_without_legacy_fallback(reader,tag):
    from fastapi import HTTPException
    from app.city import versions
    with pytest.raises(HTTPException) as exc:getattr(versions,reader)({'schema_version':tag})
    assert exc.value.status_code==422 and exc.value.detail['code']=='SCHEMA_UNSUPPORTED'


def test_daily_stop_limit_is_not_a_trip_wide_24_stop_limit():
    from app.city.state_schema import CityDraftV2
    from app.city.engine.models import MultiDayRequirements
    from multi_day_helpers import draft,requirements
    data=draft()
    for index,d in enumerate(data['days']):
        d['stops']=[{'stop_id':f'd{index}:g{i}','place_id':'museum','kind':'visit'} for i in range(15)]
    assert sum(len(d.stops) for d in CityDraftV2.model_validate(data).days)==30
    data['days'][0]['stops']=[{'stop_id':f'g{i}','place_id':'museum','kind':'visit'} for i in range(25)]
    with pytest.raises(ValidationError,match='24'):CityDraftV2.model_validate(data)
    req=requirements();req['goals']=[req['goals'][0]|{'goal_id':f'g{i}'} for i in range(30)]
    assert len(MultiDayRequirements.model_validate(req).goals)==30


def test_multi_day_route_rejects_duplicate_goal_session_and_multiple_ongoing():
    import copy
    from app.city.engine.models import MultiDayItinerary
    from multi_day_helpers import itinerary
    data=itinerary()
    for d in data['days']:
        day=d['business_date']
        d['itinerary']['activities']=[{'activity_id':'a:'+day,'goal_id':'g:'+day,'place_id':'stage','kind':'show',
            'session_id':'s:'+day,'arrives_at':day+'T09:00:00+08:00','starts_at':day+'T09:00:00+08:00',
            'departs_at':day+'T09:15:00+08:00','duration_seconds':900,'wait_seconds':0,'required':True,
            'adjustable':False,'data_status':'synthetic','evidence_refs':[]}]
    assert len(MultiDayItinerary.model_validate(data).days)==2
    for key in ('goal_id','session_id'):
        broken=copy.deepcopy(data)
        broken['days'][1]['itinerary']['activities'][0][key]=broken['days'][0]['itinerary']['activities'][0][key]
        with pytest.raises(ValidationError,match='重复'):MultiDayItinerary.model_validate(broken)
    for d in data['days']:
        a=d['itinerary']['activities'].pop()
        d['itinerary']['ongoing']={'stop_id':a['goal_id'],'place_id':a['place_id'],'starts_at':a['starts_at'],
            'ends_at':a['departs_at'],'remaining_basis':'user_reported','original_activity':a}
    with pytest.raises(ValidationError,match='最多一个'):MultiDayItinerary.model_validate(data)
    data['days'][1]['itinerary']['ongoing']=None
    assert MultiDayItinerary.model_validate(data).days[0].itinerary.ongoing
    data['days'][0]['itinerary']['ongoing']={'stop_id':'malformed'}
    with pytest.raises(ValidationError):MultiDayItinerary.model_validate(data)
