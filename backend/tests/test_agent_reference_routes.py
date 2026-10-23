"""Reference routing is independent of GPS, model availability and formal itinerary data."""
import json
import time
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect
from app import language
from app.city import location_resolution as lr
from app.city.agent import provider,service,reference_routes


def rid():return str(uuid4())

@pytest.fixture
def reference(client,monkeypatch):
    service.TASKS.clear()
    monkeypatch.setattr(lr,'STORE',lr.ResolutionStore())
    monkeypatch.setenv('AMAP_REFERENCE_ROUTES_ENABLED','1')
    monkeypatch.setenv('AMAP_LOCATION_SEARCH_ENABLED','1')
    monkeypatch.setenv('AMAP_TERMS_REVIEWED','1')
    calls=[];settings={'ambiguous':False,'both':False,'poi_status':'1','route_status':'1'}
    def handle(request):
        calls.append(request)
        if '/place/' in request.url.path:
            query=request.url.params['keywords']
            rows=[{'id':query+':a','name':query,'location':'114.3,34.8','adcode':'410202','citycode':'0371','address':'[合成测试]主站'}]
            if settings['ambiguous'] and (query=='开封站' or settings['both']):rows.append(rows[0]|{'id':query+':b','address':'[合成测试]另一地址','location':'114.31,34.81'})
            else:rows.append(rows[0]|{'id':query+':other','name':query+'附近小店'})
            return httpx.Response(200,json={'status':settings['poi_status'],'infocode':'10000','pois':rows})
        return httpx.Response(200,json={'status':settings['route_status'],'infocode':'10000','route':{'paths':[{'distance':'1234','cost':{'duration':'720'},'steps':[{'instruction':'[合成测试]沿测试路前行'}]}]}})
    def factory():
        return lr.amap.AmapProvider('fake-key',enabled=True,terms_reviewed=True,transport=httpx.MockTransport(handle),api_version='v5',retries=0)
    monkeypatch.setattr(lr,'provider_from_environment',factory)
    client.post('/api/v1/sessions')
    trip=client.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    return {'c':client,'trip':trip,'path':'/api/city/v1/trips/'+trip['id'],'calls':calls,'settings':settings}


def run(e,text):
    t=e['c'].post(e['path']+'/agent/tasks',json={'request_id':rid(),'text':text,'expected_version':1,'expected_route_version':0}).json()
    return drive(e,t)


def drive(e,t):
    for _ in range(5):
        if t['status']!='running':break
        response=e['c'].post(e['path']+'/agent/tasks/'+t['task_id']+'/step',json={'request_id':rid(),'expected_step':t['step']})
        assert response.status_code==200,response.text
        t=response.json()
    return t


def select(e,t,choice,index=0,request_id=None):
    return e['c'].post(e['path']+'/agent/tasks/'+t['task_id']+'/locations/confirm',json={'request_id':request_id or rid(),'expected_step':t['step'],'role':choice['role'],'resolution_id':choice['resolution_id'],'candidate_id':choice['candidates'][index]['candidate_id']})


def test_explicit_endpoints_call_amap_without_gps_model_or_formal_data(reference,monkeypatch):
    e=reference
    monkeypatch.setattr(provider,'generate',lambda *a,**k:pytest.fail('named route must not need a model'))
    t=run(e,'从开封站到清明上河园怎么走')
    assert t['status']=='answered',t
    assert t['mode']=='reference_route' and t['result']['state']=='locations_explicit'
    assert [event['tool'] for event in t['events']]==['understand','search_locations','query_reference_route']
    assert t['metrics']['model_calls']==0 and t['metrics']['tools']==2
    route=t['result']['reference_route']
    assert route['duration_seconds']==720 and route['distance_m']==1234
    assert route['formal_admission']=='needs_data' and route['entrances_verified'] is False
    assert [c.url.path for c in e['calls']]==['/v5/place/text','/v5/place/text','/v5/direction/walking']
    assert 'position' not in str(e['calls'][-1].url)
    with connect() as db:
        for table in ('city_facts','city_engine_operations','city_engine_candidates','city_engine_routes'):
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
        stored=' '.join(r[0] for r in db.execute('SELECT content_redacted FROM conversation_messages'))
        assert '[合成测试]沿测试路前行' not in stored
        assert '1234' not in stored
    assert e['c'].get(e['path']).json()['route_version']==0


def test_ambiguous_location_requires_real_selection_and_keeps_task_snapshot(reference):
    e=reference;e['settings']['ambiguous']=True
    t=run(e,'从开封站到清明上河园怎么走')
    assert t['status']=='awaiting_locations' and t['result']['state']=='ambiguous_locations'
    assert len(e['calls'])==2
    choice=t['result']['location_choices'][0]
    assert len(choice['candidates'])==2
    assert service.TASKS[t['task_id']].snapshot is not None
    result=select(e,t,choice,1)
    assert result.status_code==200,result.text
    final=drive(e,result.json())
    assert final['status']=='answered',final
    assert e['calls'][-1].url.params['origin']=='114.310000,34.810000'
    assert len(e['calls'])==3


def test_two_ambiguous_roles_and_human_wait_do_not_consume_active_budget(reference):
    e=reference;e['settings'].update(ambiguous=True,both=True)
    t=run(e,'从开封站到清明上河园怎么走')
    assert len(t['result']['location_choices'])==2
    pending=service.TASKS[t['task_id']]
    pending.started-=60;pending.selection_wait_started-=60
    t=select(e,t,t['result']['location_choices'][0]).json()
    assert t['status']=='awaiting_locations'
    t=select(e,t,t['result']['location_choices'][0]).json()
    assert drive(e,t)['status']=='answered'


def test_current_location_is_requested_only_when_user_selects_it(reference):
    e=reference;t=run(e,'从我这里到清明上河园怎么走')
    assert t['result']['state']=='current_position_required'
    assert t['result']['code']=='CURRENT_ORIGIN_REQUIRED' and len(e['calls'])==0


def test_missing_origin_is_clarification_not_gps(reference):
    t=run(reference,'清明上河园怎么走')
    assert t['result']['state']=='ambiguous_locations'
    assert t['result']['code']=='ROUTE_ENDPOINTS_REQUIRED'
    assert '当前位置缺失' not in t['result']['reply']


def test_model_unconfigured_reports_zero_attempts_and_no_fake_http_error(reference):
    t=run(reference,'这个和我的定位又没关系')
    assert t['status']=='unavailable' and t['result']['state']=='model_unavailable'
    assert t['result']['code']=='MODEL_NOT_CONFIGURED'
    assert t['result']['model_status']['http_attempts']==0
    assert t['result']['model_status']['schedule_state']=='not_entered'
    assert t['metrics']['model_calls']==0 and t['metrics']['usage']==[]


def test_model_http_429_keeps_real_error_classification(reference,monkeypatch):
    monkeypatch.setattr(language,'available',lambda:True)
    def fail(*a,**k):raise HTTPException(503,detail={'code':'MODEL_HTTP_ERROR','upstream_status':429,'http_attempts':1,'source':'live_model','limit_classification':'rate_limit'})
    monkeypatch.setattr(provider,'generate',fail)
    t=run(reference,'这个和我的定位又没关系')
    assert t['result']['state']=='model_unavailable'
    assert t['result']['model_status']['upstream_status']==429
    assert t['metrics']['model_calls']==1


def test_capabilities_report_independent_gates(reference):
    result=reference['c'].get(reference['path']+'/agent/capabilities').json()
    assert result['poi_search_enabled'] and result['reference_routes_enabled']
    assert result['model']['available'] is False
    assert result['formal_amap_traffic_provider']=='not_connected'


def test_disabled_reference_is_not_misreported_as_gps(reference,monkeypatch):
    monkeypatch.setenv('AMAP_REFERENCE_ROUTES_ENABLED','0')
    result=run(reference,'从开封站到清明上河园怎么走')['result']
    assert result['code']=='REFERENCE_ROUTES_DISABLED'
    assert result['state']=='locations_explicit' and not reference['calls']


def test_selection_cannot_cross_session_or_task_or_use_expired_candidates(reference):
    e=reference;e['settings']['ambiguous']=True
    t=run(e,'从开封站到清明上河园怎么走');choice=t['result']['location_choices'][0]
    with TestClient(app) as other:
        other.post('/api/v1/sessions')
        response=other.post(e['path']+'/agent/tasks/'+t['task_id']+'/locations/confirm',json={'request_id':rid(),'expected_step':t['step'],'role':choice['role'],'resolution_id':choice['resolution_id'],'candidate_id':choice['candidates'][0]['candidate_id']})
        assert response.status_code==404
    assert select(e,t,choice|{'resolution_id':rid()}).status_code==404
    lr.STORE._items[choice['resolution_id']].created_at=time.monotonic()-1000
    assert select(e,t,choice).status_code==404
    assert len(e['calls'])==2


def test_cancel_during_location_choice_prevents_route_request(reference):
    e=reference;e['settings']['ambiguous']=True
    t=run(e,'从开封站到清明上河园怎么走');choice=t['result']['location_choices'][0]
    response=e['c'].post(e['path']+'/agent/tasks/'+t['task_id']+'/cancel')
    assert response.json()['status']=='cancelled'
    assert select(e,t,choice).status_code==409
    assert len(e['calls'])==2


def test_model_route_extraction_is_grounded_and_uses_same_tools(reference,monkeypatch):
    e=reference;text='能否帮我查一下开封站至清明上河园的走法'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','quote':text}],'route_query':{'quote':text,'origin':'开封站','destination':'清明上河园'}},{'source':'mock_model','http_attempts':1}))
    result=run(e,text)
    assert result['mode']=='reference_route' and result['status']=='answered',result
    assert result['metrics']['model_calls']==1
