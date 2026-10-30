"""Trip intentions and detail context, exercised only in isolated pytest databases."""
import copy,json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect
from test_city_engine_api import engine


def rid():return str(uuid4())
def base(t):return '/api/city/v1/trips/'+t['id']
def collect(c,t,kind,identity,priority='want',version=None,**kw):
    if version is None:version=c.get(base(t)+'/basket').json()['version']
    return c.patch(base(t)+'/basket',json={'request_id':kw.pop('request_id',rid()),'expected_version':version,'operation':kw.pop('operation','put'),
         'item':{'kind':kind,'object_id':identity,'priority':priority,**kw}})
def constraints(d):return {k:d['requirements'][k] for k in ('business_date','starts_at','ends_at','origin_place_id','destination_place_id')}
def plan(e,**overrides):
    c,t,d=e;b=c.get(base(t)+'/basket').json()
    return c.post(base(t)+'/exploration/preview',json={'request_id':rid(),'expected_version':t['version'],'basket_version':b['version'],'constraints':constraints(d)|overrides})


def test_basket_no_formal_write_dedup_priority_and_idempotency(client):
    c=client;c.post('/api/v1/sessions');t=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    request=rid();first=collect(c,t,'scenic','qingming',request_id=request,version=0)
    assert first.status_code==200,first.text
    b=first.json();assert len(b['items'])==1 and b['items'][0]['status']=='pending' and b['items'][0]['fact_id'] is None
    assert collect(c,t,'scenic','qingming',request_id=request,version=0).json()==b
    assert collect(c,t,'scenic','qingming').json()['version']==b['version']
    changed=collect(c,t,'scenic','qingming','must',note='拍照',status='standby').json()
    assert changed['items'][0]['priority']=='must' and changed['items'][0]['status']=='standby'
    formal=c.get(base(t)).json();assert formal['draft']['stops']==[] and formal['version']==1 and formal['route_version']==0
    assert collect(c,t,'scenic','qingming',version=0).status_code==409
    assert collect(c,t,'scenic','made-up').status_code==404


def test_basket_trip_owner_and_deletion(client):
    c=client;c.post('/api/v1/sessions');t=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    collect(c,t,'food','yangroukangmo')
    second=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json();assert c.get(base(second)+'/basket').json()['items']==[]
    with TestClient(app) as other:
        other.post('/api/v1/sessions');assert other.get(base(t)+'/basket').status_code==404
        assert collect(other,t,'scenic','qingming',version=0).status_code==404
    exported=c.get('/api/v1/memory/export').json()['city_trips']
    assert any(x['basket']['items'] for x in exported)
    c.post('/api/v1/privacy/delete-request',json={'request_id':rid(),'confirmed':True})
    with connect() as db:assert db.execute('SELECT count(*) FROM city_basket_items').fetchone()[0]==0


def test_food_does_not_choose_fake_vendor_and_unknown_is_not_infeasible(engine):
    c,t,d=engine
    collect(c,t,'food','yangroukangmo','must')
    r=plan(engine);assert r.status_code==200,r.text
    assert r.json()['status']=='unknown' and r.json()['planning'] is None
    assert c.get(base(t)).json()['draft']['stops']==[]
    assert '供应' not in str(c.get(base(t)+'/basket').json()['items'][0].get('fact_id'))


def test_missing_personal_choice_only_prompts_for_missing_fields(client):
    c=client;c.post('/api/v1/sessions');t=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    collect(c,t,'food','chaoliangfen')
    r=c.post(base(t)+'/exploration/preview',json={'request_id':rid(),'expected_version':1,'basket_version':1,'constraints':{}})
    assert r.status_code==200,r.text
    assert r.json()['status']=='unknown' and len(r.json()['questions'])==5


def test_verified_route_preview_confirm_and_basket_stale_gate(engine):
    c,t,d=engine
    collect(c,t,'place','museum','must')
    p=plan(engine);assert p.status_code==200,p.text;p=p.json()
    assert p['status']=='feasible' and p['planning']['candidate_id']
    assert c.get(base(t)).json()['route_version']==0
    assert len(p['planning']['result']['itineraries'])>=1
    for i in p['planning']['result']['itineraries']:assert any(a['place_id']=='museum' and a['required'] for a in i['activities'])
    # Any later note/priority change invalidates the preview, even through the old engine endpoint.
    collect(c,t,'place','museum','must',note='新的备注')
    stale=c.post(base(t)+'/engine/confirm',json={'request_id':rid(),'expected_version':1,'candidate_id':p['planning']['candidate_id']})
    assert stale.status_code==409 and stale.json()['detail']['code']=='BASKET_CHANGED'
    p=plan(engine).json();request=rid();body={'request_id':request,'expected_version':1,'candidate_id':p['planning']['candidate_id']}
    committed=c.post(base(t)+'/engine/confirm',json=body);assert committed.status_code==200,committed.text
    assert c.post(base(t)+'/engine/confirm',json=body).json()==committed.json()
    assert c.get(base(t)).json()['route_version']==1
    assert c.get(base(t)+'/basket').json()['items'][0]['status']=='arranged'


def test_child_stage_keeps_parent_poi_basket_visit_and_route_coordinates(engine):
    from app.city import facts
    c,t,d=engine
    assert collect(c,t,'poi','museum','must').status_code==200
    before=c.get(base(t)+'/basket').json()['items']
    first=plan(engine).json();assert first['status']=='feasible',first
    with connect() as db:
        parent=facts.records(db)['place','museum']
        stage=parent.model_copy(update={'fact_id':'synthetic-child-stage','name':'合成院内演出点',
            'place_type':'stage','parent_place_id':parent.fact_id,'longitude':None,'latitude':None,'coordinate_system':'unknown'})
        facts.import_bundle(facts.FactBundle(records=[stage]),db,apply=True)
        assert facts.records(db)['place','museum']==parent
    assert c.get(base(t)+'/basket').json()['items']==before
    result=plan(engine).json();assert result['status']=='feasible',result
    routes=result['planning']['result']['itineraries']
    assert routes==first['planning']['result']['itineraries']
    confirmed=c.post(base(t)+'/engine/confirm',json={'request_id':rid(),'expected_version':t['version'],
        'candidate_id':result['planning']['candidate_id']})
    assert confirmed.status_code==200,confirmed.text
    current=c.get(base(t)+'/engine/current').json()
    assert current['itinerary']['activities'][0]['place_id']==parent.fact_id
    public=c.get('/api/city/v1/facts?kind=place').json()
    place=next(p for p in public['items'] if p['record']['fact_id']==parent.fact_id)
    assert place['record']['place_type']=='poi'
    assert (place['record']['longitude'],place['record']['latitude'])==(parent.longitude,parent.latitude)


def test_actual_conflict_has_structured_choices_and_never_relaxes_must(engine):
    c,t,d=engine
    collect(c,t,'place','museum','must');collect(c,t,'show','s1','must')
    # Opening and session data are complete, but the combined time horizon cannot fit.
    p=plan(engine,starts_at='2030-05-01T10:45:00+08:00',ends_at='2030-05-01T11:45:00+08:00').json()
    assert p['status']=='infeasible',p
    assert c.get(base(t)+'/basket').json()['items'][0]['priority']=='must'
    assert c.get(base(t)).json()['route_version']==0
    assert all(o['requires_consent'] for o in p['options'])
    assert any(o['id']=='relax:place:museum' for o in p['options'])
    # This explicit choice changes structured priority, not chat history or the formal route.
    collect(c,t,'place','museum','want')
    agreed=plan(engine,starts_at='2030-05-01T10:45:00+08:00',ends_at='2030-05-01T11:45:00+08:00').json()
    assert agreed['status']=='feasible',agreed
    assert all(any(x['session_id']=='s1' and x['required'] for x in route['activities']) for route in agreed['planning']['result']['itineraries'])


def test_detail_context_updates_without_browser_events_becoming_chat(client):
    c=client;c.post('/api/v1/sessions');t=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    for identity,name in [('kaifengfu','开封府'),('qingming','清明上河园')]:
        task=c.post(base(t)+'/agent/tasks',json={'request_id':rid(),'text':'这里适合游玩多久？','expected_version':1,'expected_route_version':0,'page_focus':{'kind':'scenic','object_id':identity}})
        assert task.status_code==200,task.text
        task=task.json()
        for _ in range(4):
            if task['status']!='running':break
            task=c.post(base(t)+'/agent/tasks/'+task['task_id']+'/step',json={'request_id':rid(),'expected_step':task['step']}).json()
        assert name in task['result']['reply'] and '待添加' in task['result']['reply']
    messages=c.get('/api/v1/memory/export').json()['messages'];assert len(messages)==4
    assert all(m['text']!='浏览开封府' for m in messages)
    assert c.get(base(t)).json()['version']==1


def test_agent_reads_basket_and_stops_on_basket_change(client):
    c=client;c.post('/api/v1/sessions');t=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    collect(c,t,'food','chaoliangfen','must')
    context=c.get(base(t)+'/assistant/context').json();assert context['basket']['items'][0]['priority']=='must'
    task=c.post(base(t)+'/agent/tasks',json={'request_id':rid(),'text':'这里适合游玩多久？','expected_version':1,'expected_route_version':0,'page_focus':{'kind':'scenic','object_id':'qingming'}}).json()
    collect(c,t,'scenic','qingming')
    stopped=c.post(base(t)+'/agent/tasks/'+task['task_id']+'/step',json={'request_id':rid(),'expected_step':0}).json()
    assert stopped['status']=='stale' and c.get(base(t)).json()['version']==1


def test_explicit_business_binding_not_name_guess(engine):
    c,t,d=engine
    assert collect(c,t,'food','chaoliangfen').json()['items'][0]['fact_id'] is None
    with connect() as db:db.execute('INSERT INTO city_object_bindings VALUES (?,?,?,?)',('food','chaoliangfen','food','food'))
    result=c.get(base(t)+'/basket').json();assert result['items'][0]['fact_id']=='food'
    assert plan(engine).json()['status']=='feasible'


def answer_body(c,t,result,**reply):
    return {'request_id':rid(),'expected_version':t['version'],
            'basket_version':c.get(base(t)+'/basket').json()['version'],
            'planning_version':result['planning_version'],**reply}


def test_changed_constraints_and_cancel_invalidate_old_candidates_on_server(engine):
    c,t,d=engine;collect(c,t,'place','museum','must')
    old=plan(engine).json()
    latest=plan(engine,ends_at='2030-05-01T19:00:00+08:00').json()
    assert latest['planning_version']>old['planning_version']
    stale=c.post(base(t)+'/engine/confirm',json={'request_id':rid(),'expected_version':1,'candidate_id':old['planning']['candidate_id']})
    assert stale.status_code==409 and stale.json()['detail']['code']=='PLANNING_CHANGED'
    saved=c.get(base(t)+'/exploration/constraints').json()
    assert saved['result']==latest and saved['constraints']['ends_at']==latest['constraints']['ends_at']
    cancelled=c.post(base(t)+'/exploration/cancel',json=answer_body(c,t,latest))
    assert cancelled.status_code==200 and cancelled.json()['result'] is None
    stale=c.post(base(t)+'/engine/confirm',json={'request_id':rid(),'expected_version':1,'candidate_id':latest['planning']['candidate_id']})
    assert stale.status_code==409 and c.get(base(t)).json()['route_version']==0


def test_negotiation_option_and_free_text_persist_atomic_decisions_and_recompute(engine):
    c,t,d=engine;collect(c,t,'place','museum','must');collect(c,t,'show','s1','must')
    timing={'starts_at':'2030-05-01T10:45:00+08:00','ends_at':'2030-05-01T11:45:00+08:00'}
    conflict=plan(engine,**timing).json();assert conflict['status']=='infeasible'
    body=answer_body(c,t,conflict,option_id='relax:place:museum')
    agreed=c.post(base(t)+'/exploration/answer',json=body)
    assert agreed.status_code==200,agreed.text
    assert c.post(base(t)+'/exploration/answer',json=body).json()==agreed.json()
    assert agreed.json()['basket']['items'][0]['priority']=='want'
    assert agreed.json()['rounds']==1 and agreed.json()['result'] is None
    solved=plan(engine,**timing).json();assert solved['status']=='feasible'
    assert all(any(a['session_id']=='s1' and a['required'] for a in route['activities']) for route in solved['planning']['result']['itineraries'])
    # Full names are stable business references; unsupported or ambiguous text never partially changes state.
    before=c.get(base(t)+'/basket').json()
    invalid=c.post(base(t)+'/exploration/answer',json=answer_body(c,t,solved,text='[合成测试]museum必去，随便编个饭店'))
    assert invalid.status_code==422 and c.get(base(t)+'/basket').json()==before
    reply=c.post(base(t)+'/exploration/answer',json=answer_body(c,t,solved,text='[合成测试]museum必去，轻松一点，晚上七点回酒店'))
    assert reply.status_code==200,reply.text
    assert reply.json()['constraints']['pace']=='relaxed'
    assert reply.json()['constraints']['ends_at'].startswith('2030-05-01T19:00:00')
    assert reply.json()['basket']['items'][0]['priority']=='must'
    assert c.get(base(t)).json()['version']==1
    exported=c.get('/api/v1/memory/export').json()['city_trips']
    assert exported[0]['exploration']['version']==reply.json()['version']
    c.post('/api/v1/privacy/delete-request',json={'request_id':rid(),'confirmed':True})
    with connect() as db:assert db.execute('SELECT count(*) FROM city_exploration_states').fetchone()[0]==0


def test_negotiation_rejects_cross_trip_stale_and_invalid_options(engine):
    c,t,d=engine;collect(c,t,'place','museum','must');p=plan(engine).json()
    assert c.post(base(t)+'/exploration/answer',json=answer_body(c,t,p,option_id='relax:place:invented')).status_code==409
    collect(c,t,'place','museum','must',note='已修改')
    old=answer_body(c,t,p,text='轻松一点');old['basket_version']=p['basket_version']
    assert c.post(base(t)+'/exploration/answer',json=old).status_code==409
    with TestClient(app) as other:
        other.post('/api/v1/sessions')
        assert other.get(base(t)+'/exploration/constraints').status_code==404
        assert other.post(base(t)+'/exploration/cancel',json=old).status_code==404


def test_reported_locations_persist_without_solver_or_formal_route(engine,monkeypatch):
    from app.city.exploration import engine as planning_engine
    c,t,d=engine
    before=c.get(base(t)).json()
    def forbidden(*a,**kw):raise AssertionError('unmatched names must never enter the solver')
    monkeypatch.setattr(planning_engine,'preview',forbidden)
    r=plan(engine,origin_place_id=None,destination_place_id=None,origin_text='  我住的酒店  ',destination_text='开封站')
    assert r.status_code==200,r.text
    result=r.json()
    assert result['issues'][0]['code']=='ENDPOINT_MATCH_PENDING' and result['planning'] is None
    saved=c.get(base(t)+'/exploration/constraints').json()
    assert saved['constraints']['origin_text']=='我住的酒店'
    assert saved['constraints']['destination_text']=='开封站'
    assert saved['constraints']['origin_place_id'] is None
    assert c.get(base(t)).json()==before
    stale=c.post(base(t)+'/exploration/preview',json={'request_id':rid(),'expected_version':1,'basket_version':0,
        'planning_version':0,'constraints':saved['constraints']})
    assert stale.status_code==409
    c.post('/api/v1/privacy/delete-request',json={'request_id':rid(),'confirmed':True})
    assert c.get(base(t)+'/exploration/constraints').status_code==401
    c.post('/api/v1/sessions')
    assert c.get(base(t)+'/exploration/constraints').status_code==404


def test_reported_location_validation_and_known_place_path(engine):
    # Selected IDs remain authoritative, and text-only fields never leak to strict engine requirements.
    r=plan(engine,origin_place_id=None,origin_text='a'*121)
    assert r.status_code==422
    r=plan(engine,origin_text='different label',destination_text='different label')
    assert r.status_code==200,r.text
    assert r.json()['constraints']['origin_text'] is None
    assert r.json()['constraints']['destination_text'] is None
    assert r.json()['issues'][0]['code']=='BASKET_EMPTY'


@pytest.mark.parametrize('party_size',[None,2])
@pytest.mark.parametrize('gate_fields',[('origin_place_id','destination_place_id'),('origin_place_id',),('destination_place_id',)])
def test_outside_meeting_endpoint_keeps_city_goals_without_reentry(engine,monkeypatch,party_size,gate_fields):
    from app.city import facts
    from app.city.agent import orchestration
    from app.city.engine import live_travel
    from app.city.engine.amap import Quote
    from datetime import date
    c,t,d=engine
    with connect() as db:
        catalog=facts.records(db);park=catalog['place','museum'];outside=catalog['place','origin']
        rule=catalog['open_rule','open:museum']
        common=outside.model_dump(exclude={'kind','fact_id','name','place_type','longitude','latitude','coordinate_system'})
        gate=facts.Place(**(common|{'kind':'place','fact_id':'synthetic:gate','name':'合成园内入口',
            'place_type':'entrance','parent_place_id':park.fact_id,'longitude':outside.longitude,
            'latitude':outside.latitude,'coordinate_system':outside.coordinate_system}))
        entrance=facts.Entrance(**{k:v for k,v in common.items() if k in facts.Entrance.model_fields},
            kind='entrance',fact_id='synthetic:entrance',place_id=park.fact_id,entrance_place_id=gate.fact_id,direction='both')
        source=catalog['source','fixture:source'].model_copy(update={'fact_id':live_travel.SOURCE_ID})
        facts.import_bundle(facts.FactBundle(records=[source,gate,entrance,
            park.model_copy(update={'version':park.version+1,'place_type':'scenic'}),
            rule.model_copy(update={'version':rule.version+1,'planning_status':'reference','planning_note':'synthetic reference rule for reentry regression','reentry':'unknown'})]),db,apply=True)
    class FakeProvider:
        request_count=0
        interval=0
        def estimate(self,origin,destination,mode,departure,checked):
            self.request_count+=1
            return Quote('estimated',duration_seconds=600,distance_m=700,geometry_available=True,queried_at=checked.isoformat())
        def close(self):pass
    monkeypatch.setenv('AMAP_PLANNING_ENABLED','1')
    monkeypatch.setattr(live_travel,'provider_from_environment',lambda:FakeProvider())
    for pid in ('museum','restaurant','garden'):assert collect(c,t,'place',pid).status_code==200
    extra={} if party_size is None else {'party_size':party_size}
    endpoints={field:gate.fact_id if field in gate_fields else outside.fact_id for field in ('origin_place_id','destination_place_id')}
    inside=plan(engine,**endpoints,**extra).json()
    assert inside['status']=='feasible',inside
    from app.city.exploration import ENDPOINT_REPLACEMENT_WARNING
    assert inside['planning']['result']['warnings'].count(ENDPOINT_REPLACEMENT_WARNING)==1
    saved=c.get(base(t)+'/exploration/constraints').json()
    assert saved['result']['planning']['result']['warnings'].count(ENDPOINT_REPLACEMENT_WARNING)==1
    for field,identity in endpoints.items():
        assert inside['constraints'][field]==identity
        assert saved['constraints'][field]==identity
        assert saved['result']['constraints'][field]==identity
    with connect() as db:
        candidate=db.execute('SELECT requirements_json FROM city_engine_candidates WHERE id=?',
                             (inside['planning']['candidate_id'],)).fetchone()
        assert all(json.loads(candidate[0])[field]==outside.fact_id for field in ('origin_place_id','destination_place_id'))
    # Same missing-field/default workflow as quick chat, using an arbitrary synthetic meeting point.
    monkeypatch.setattr(orchestration,'QUICK_PLACE_ID',outside.fact_id)
    initial=c.post(base(t)+'/exploration/preview',json={'request_id':rid(),'expected_version':t['version'],
        'basket_version':3,'constraints':constraints(d)|{'origin_place_id':None,'destination_place_id':None}|extra}).json()
    quick,_,_=orchestration._quick_defaults(initial,['origin_place_id','destination_place_id'],date(2030,4,30))
    direct=plan(engine,origin_place_id=outside.fact_id,destination_place_id=outside.fact_id,**extra).json()
    chat=plan(engine,**quick).json()
    for result in (inside,direct,chat):
        assert result['status']=='feasible',result
        assert {a['place_id'] for a in result['planning']['result']['itineraries'][0]['activities']}=={'museum','restaurant','garden'}
    def schedule(result):
        return [[(a['place_id'],a['starts_at'],a['departs_at']) for a in i['activities']]
                for i in result['planning']['result']['itineraries']]
    assert schedule(inside)==schedule(direct)==schedule(chat)
    assert ENDPOINT_REPLACEMENT_WARNING not in direct['planning']['result']['warnings']
    assert c.get(base(t)).json()['route_version']==0


@pytest.mark.parametrize('gate_changes,outside_changes,duplicate,expected',[
    ({},{},False,'origin'),
    ({'place_type':'poi'},{},False,'synthetic:gate'),
    ({'parent_place_id':None},{},False,'synthetic:gate'),
    ({'longitude':None,'latitude':None},{},False,'synthetic:gate'),
    ({'longitude':181},{},False,'synthetic:gate'),
    ({'latitude':float('nan')},{},False,'synthetic:gate'),
    ({'coordinate_system':'unknown'},{'coordinate_system':'unknown'},False,'synthetic:gate'),
    ({},{'parent_place_id':'museum'},False,'synthetic:gate'),
    ({},{'place_type':'rest'},False,'synthetic:gate'),
    ({},{'longitude':114.301001},False,'synthetic:gate'),
    ({},{'latitude':34.801001},False,'synthetic:gate'),
    ({},{'coordinate_system':'WGS84'},False,'synthetic:gate'),
    ({},{},True,'synthetic:gate'),
])
def test_planning_endpoint_requires_unique_outside_transport_at_exact_coordinates(engine,gate_changes,outside_changes,duplicate,expected):
    from app.city import facts
    from app.city.exploration import planning_endpoint
    with connect() as db:items=facts.records(db)
    outside=items['place','origin']
    gate=outside.model_copy(update={'fact_id':'synthetic:gate','place_type':'entrance','parent_place_id':'museum',
                                  'longitude':114.3,'latitude':34.8,'coordinate_system':'GCJ02'}|gate_changes)
    outside=outside.model_copy(update={'longitude':114.3,'latitude':34.8,'coordinate_system':'GCJ02'}|outside_changes)
    items={('place',gate.fact_id):gate,('place',outside.fact_id):outside}
    if duplicate:items['place','synthetic:duplicate']=outside.model_copy(update={'fact_id':'synthetic:duplicate'})
    assert planning_endpoint(items,gate.fact_id)==expected
    assert planning_endpoint(items,outside.fact_id)==outside.fact_id
    assert planning_endpoint(items,'missing')=='missing'
