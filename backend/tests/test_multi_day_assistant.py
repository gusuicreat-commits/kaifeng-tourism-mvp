"""Multi-day assistant projections and D4 adapters use isolated synthetic snapshots."""
import json
from datetime import date,datetime,timedelta
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.db import connect
from app.city.assistant import context,tools
from app.city.agent import toolbelt,conversation,service as agent
from app.city.agent.models import StateIntent
from app.city.engine import service as engine_service
from app.city.engine.models import MultiDayRequirements,DayItinerary,Requirements,Itinerary
from app.city.engine.multi_day import assemble
from app.city.state_schema import CityDraftV2
from app.memory.session import Session
from test_city_assistant import city,walking,get,path
from test_agent_in_trip_changes import active_current
from test_multi_day_api import multi_engine,committed,start_today
from test_city_engine_api import engine,confirm as engine_confirm


def multi(city,ongoing=False):
    active_current(city) if ongoing else walking(city)
    original=city['data']['requirements'];today=date(2030,5,1)
    dates=[today+timedelta(days=i) for i in (-1,0,1)]
    days=[{'business_date':d,'starts_at':datetime.combine(d,datetime.min.time(),original_time.tzinfo).replace(hour=9),
           'ends_at':datetime.combine(d,datetime.min.time(),original_time.tzinfo).replace(hour=20),
           'origin_place_id':'hotel' if i==2 else 'origin','destination_place_id':'origin' if i==0 else 'hotel'}
          for i,d in enumerate(dates) for original_time in [datetime.fromisoformat(original['starts_at'])]]
    nights=[{'overnight_id':'night:'+d.isoformat(),'night_date':d,'from_day_id':'day:'+d.isoformat(),
        'to_day_id':'day:'+dates[i+1].isoformat(),'place_id':days[i]['destination_place_id'],
        'source_kind':'user_place','place_evidence':'verified','stay_evidence':'unknown'} for i,d in enumerate(dates[:-1])]
    goals=[g|{'allowed_dates':(today,)} for g in original['goals']]
    goals.append({'goal_id':'future','target_kind':'place','target_id':'restaurant','allowed_dates':(dates[-1],)})
    full=MultiDayRequirements.model_validate({'schema_version':'city-engine/2','start_date':dates[0],
        'end_date':dates[-1],'days':days,'goals':goals,'overnights':nights})
    old=city['client'].get(path(city,'/engine/current')).json()['itinerary']
    day_plans=[]
    for i,d in enumerate(full.days):
        req=Requirements.model_validate(original if i==1 else {**days[i],'goals':[]})
        plan=Itinerary.model_validate(old) if i==1 else Itinerary(activities=(),legs=(),returns_at=d.starts_at,
            total_seconds=0,travel_seconds=0,walking_distance_m=0,known_cost_upper_fen=0,cost_unknown=False,score={},evidence_mode='verified_with_estimates')
        day_plans.append(DayItinerary(business_date=d.business_date,original_requirements=d,requirements=req,itinerary=plan,
            status='closed' if i==0 else 'pending'))
    plan=assemble(full,day_plans,'balanced')
    draft=get(city)['draft']
    full_draft=CityDraftV2.model_validate({'schema_version':'city-draft/2','start_date':dates[0],'end_date':dates[-1],
        'days':[days[0]|{'stops':[],'status':'closed'},draft,days[2]|{'stops':[]}],'overnights':nights})
    with connect() as db:
        db.execute('UPDATE city_trip_states SET draft_json=? WHERE id=?',(full_draft.model_dump_json(),city['trip']['id']))
        db.execute('UPDATE city_engine_routes SET requirements_json=?,itinerary_json=? WHERE trip_id=?',
                   (full.model_dump_json(),plan.model_dump_json(),city['trip']['id']))
        row=db.execute('SELECT * FROM conversation_sessions LIMIT 1').fetchone()
        session=Session(row['id'],row['guest_token_hash'],None)
    return context.load(session,city['trip']['id'],'modify'),session


def test_context_selects_server_today_and_keeps_trip_scope(city):
    snapshot,_=multi(city)
    assert snapshot.day_id=='day:2030-05-01'
    assert len(snapshot.multi_itinerary.days)==3 and len(snapshot.multi_draft.days)==3
    assert snapshot.envelope.progress['next']['stop_id']=='lunch'
    assert snapshot.envelope.progress['next']['day_id']==snapshot.day_id
    scopes=snapshot.envelope.requirements['days']
    assert scopes[0]['frozen'] and scopes[2]['locked']
    assert not scopes[1]['frozen'] and not scopes[1]['locked']
    payload=conversation.model_payload('今天怎么走',snapshot,'system')
    assert payload['context']['trip_scope']['day_id']==snapshot.day_id
    assert payload['context']['trip_scope']['start_date']=='2030-04-30'


@pytest.mark.parametrize('operation,minutes,ongoing', [('delay',30,False),('rest',20,False),('extend',15,True),('skip_replan',None,False)])
def test_d4_adapters_keep_historical_and_future_goals_and_whole_candidate(city,monkeypatch,operation,minutes,ongoing):
    snapshot,session=multi(city,ongoing);before=get(city);calls=[]
    def preview(s,trip_id,body,replanning=False):
        calls.append(body)
        plan=snapshot.multi_itinerary.model_dump(mode='json')
        changes={'days':[{'day_id':d.day_id,'business_date':d.business_date.isoformat(),'removed':[],'added':[],
            'start_changes':[],'order_changes':[]} for d in snapshot.multi_itinerary.days]}
        return {'status':'preview','candidate_id':str(uuid4()),'base_version':snapshot.row['version'],
            'result':{'itineraries':[plan]},'changes':changes,'alternative_changes':[changes]}
    monkeypatch.setattr(engine_service,'preview',preview)
    value=StateIntent(operation=operation,quote='今天调整',minutes=minutes,next_stop=operation=='skip_replan')
    result=toolbelt.preview_adjustment(session,city['trip']['id'],snapshot,value,uuid4())
    body=calls[0]
    assert body.scope=='current_day' and body.day_id==snapshot.day_id
    assert body.requirements.days==snapshot.multi_requirements.days
    assert body.requirements.overnights==snapshot.multi_requirements.overnights
    originals={g.goal_id:g for g in snapshot.multi_requirements.goals}
    goals={g.goal_id:g for g in body.requirements.goals}
    assert goals['future']==originals['future']
    if not ongoing:assert goals['visit']==originals['visit']
    if operation=='rest':
        rest=next(g for g in goals.values() if g.kind=='rest')
        assert rest.allowed_dates==(snapshot.draft.business_date,) and rest.fixed_date==snapshot.draft.business_date
    if operation=='skip_replan':assert 'lunch' not in goals and body.skipped_goal_ids==('lunch',)
    output=agent._preview_result(result,snapshot)
    assert output['status']=='needs_confirmation' and '今天剩余' in output['reply']
    assert len(output['planning']['result']['itineraries'][0]['days'])==3
    assert get(city)==before


@pytest.mark.parametrize('quote', ['明天休息20分钟','2030-05-02休息20分钟','第三天休息20分钟'])
def test_selected_future_day_adjustment_uses_future_day_without_current_anchor(city,monkeypatch,quote):
    snapshot,session=multi(city)
    calls=[]
    def preview(s,trip_id,body,replanning=False):
        calls.append(body)
        plan=snapshot.multi_itinerary.model_dump(mode='json')
        changes={'days':[{'day_id':d.day_id,'business_date':d.business_date.isoformat(),'removed':[],'added':[],
            'start_changes':[],'order_changes':[]} for d in snapshot.multi_itinerary.days]}
        return {'status':'preview','candidate_id':str(uuid4()),'base_version':snapshot.row['version'],
            'result':{'itineraries':[plan]},'changes':changes,'alternative_changes':[changes]}
    monkeypatch.setattr(engine_service,'preview',preview)
    result=toolbelt.preview_adjustment(session,city['trip']['id'],snapshot,
        StateIntent(operation='rest',quote=quote,minutes=20,scope='selected_future_day',scope_quote=quote),uuid4())
    assert calls and calls[0].scope=='selected_future_day' and calls[0].day_id=='day:2030-05-02'
    assert any(g.kind=='rest' and g.fixed_date.isoformat()=='2030-05-02' for g in calls[0].requirements.goals)
    assert result['status']=='preview'


def test_future_scope_without_a_date_is_clarified(city):
    snapshot,session=multi(city)
    with pytest.raises(HTTPException) as exc:
        toolbelt.preview_adjustment(session,city['trip']['id'],snapshot,
            StateIntent(operation='rest',quote='休息20分钟',minutes=20,scope='selected_future_day',scope_quote='休息20分钟'),uuid4())
    assert exc.value.status_code==422 and exc.value.detail['code']=='FUTURE_DAY_REQUIRED'


def test_multi_day_span_stays_unavailable(city):
    snapshot,session=multi(city)
    with pytest.raises(HTTPException) as exc:
        toolbelt.preview_adjustment(session,city['trip']['id'],snapshot,
            StateIntent(operation='rest',quote='接下来的两天都加20分钟休息',minutes=20),uuid4())
    assert exc.value.status_code==503 and exc.value.detail['code']=='MULTI_DAY_SCOPE_UNAVAILABLE'


def test_unsupported_old_assistant_writes_reject_instead_of_saving_today(city):
    snapshot,session=multi(city);before=get(city)
    with pytest.raises(HTTPException) as exc:tools.require_single_day(snapshot)
    assert exc.value.status_code==503 and exc.value.detail['status']=='unavailable'
    from app.city.assistant.models import Intent
    for operation in ('preview','preference','restore','position'):
        with pytest.raises(HTTPException) as exc:
            tools.preview(session,city['trip']['id'],snapshot,Intent(operation=operation),uuid4())
        assert exc.value.status_code==503
    assert get(city)==before


def test_outside_today_never_projects_tomorrow_as_next_and_discards_whole_candidate(city):
    snapshot,session=multi(city)
    city['clock'][0]=datetime.fromisoformat('2030-05-02T09:00:00+08:00')
    tomorrow=context.load(session,city['trip']['id'],'next')
    assert tomorrow.day_id=='day:2030-05-02' and tomorrow.envelope.progress['next'] is None
    city['clock'][0]=datetime.fromisoformat('2030-04-29T09:00:00+08:00')
    before_trip=context.load(session,city['trip']['id'],'next')
    assert before_trip.day_id=='day:2030-04-30' and before_trip.envelope.progress['next'] is None
    city['clock'][0]=datetime.fromisoformat('2030-05-01T09:40:00+08:00')
    from app.city.engine.models import SolveResultV2
    from app.city.assistant.models import DiscardAssistant
    from app.memory.storage import stamp
    candidate=str(uuid4());before=get(city)
    with connect() as db:
        db.execute('INSERT INTO city_engine_candidates VALUES (?,?,?,?,?,?,?,?,0)',
            (candidate,city['trip']['id'],snapshot.row['version'],snapshot.planning.fingerprint(),
             snapshot.multi_requirements.model_dump_json(),SolveResultV2(schema_version='city-engine-result/2',status='feasible',
             itineraries=(snapshot.multi_itinerary,),computed_at=city['clock'][0]).model_dump_json(),
             '{}',stamp(city['clock'][0]+timedelta(minutes=15))))
    response=tools.discard(session,city['trip']['id'],DiscardAssistant(request_id=uuid4(),
        expected_version=snapshot.row['version'],target_kind='route',candidate_id=candidate))
    assert response['status']=='discarded' and not response['formal_state_changed']
    assert get(city)==before
    with connect() as db:assert db.execute('SELECT consumed FROM city_engine_candidates WHERE id=?',(candidate,)).fetchone()[0]==1


@pytest.mark.parametrize('operation,minutes',[('delay',30),('rest',20),('extend',15),('skip_replan',None)])
def test_d4_whole_preview_engine_confirm_keeps_other_days(multi_engine,monkeypatch,operation,minutes):
    e,req,old=committed(multi_engine)
    activity=old['itinerary']['days'][1]['itinerary']['activities'][0]
    moment=datetime.fromisoformat(activity['starts_at'])+timedelta(minutes=1) if operation=='extend' else datetime.fromisoformat('2030-05-02T09:05:00+08:00')
    start_today(e,monkeypatch,moment.isoformat(),'museum' if operation=='extend' else 'hotel')
    monkeypatch.setattr(context,'now',lambda:moment)
    with connect() as db:
        row=db.execute('SELECT * FROM conversation_sessions LIMIT 1').fetchone()
        session=Session(row['id'],row['guest_token_hash'],None)
    snapshot=context.load(session,e[1]['id'],'modify')
    before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()
    value=StateIntent(operation=operation,quote='今天调整',minutes=minutes,next_stop=operation=='skip_replan')
    result=toolbelt.preview_adjustment(session,e[1]['id'],snapshot,value,uuid4())
    assert result['status']=='preview',result
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before
    plan=result['result']['itineraries'][0]
    assert len(plan['days'])==3
    assert plan['days'][0]==old['itinerary']['days'][0] and plan['days'][2]==old['itinerary']['days'][2]
    today=plan['days'][1]['itinerary']
    if operation=='rest':assert next(a for a in today['activities'] if a['kind']=='rest')['duration_seconds']==1200
    if operation=='extend':assert today['ongoing']['stop_id']==activity['goal_id']
    if operation=='skip_replan':assert activity['goal_id'] not in {a['goal_id'] for a in today['activities']}
    output=agent._preview_result(result,snapshot)
    assert output['status']=='needs_confirmation' and '今天剩余' in output['reply']
    response=engine_confirm(e,result)
    assert response.status_code==200,response.text
    assert response.json()['route_version']==2
    after=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()['draft']
    assert after['days'][0]==before['draft']['days'][0] and after['days'][2]==before['draft']['days'][2]


def test_scope_checks_the_whole_request_even_when_model_quote_omits_date(city,monkeypatch):
    snapshot,session=multi(city)
    monkeypatch.setattr(engine_service,'preview',lambda *a,**kw:pytest.fail('must not replan today for another day'))
    with pytest.raises(HTTPException) as exc:
        toolbelt.preview_adjustment(session,city['trip']['id'],snapshot,
            StateIntent(operation='rest',quote='休息20分钟',minutes=20),uuid4(),text='明天休息20分钟')
    assert exc.value.status_code==503 and exc.value.detail['code']=='MULTI_DAY_SCOPE_UNAVAILABLE'


def test_unconnected_weather_replan_rejects_before_any_provider_call(city,monkeypatch):
    from app.city import weather
    snapshot,session=multi(city)
    monkeypatch.setattr(weather,'answer',lambda *a,**kw:pytest.fail('unsupported multi-day write must not query provider'))
    with pytest.raises(HTTPException) as exc:weather.adjustment_preview(session,city['trip']['id'],snapshot)
    assert exc.value.status_code==503 and exc.value.detail['status']=='unavailable'
