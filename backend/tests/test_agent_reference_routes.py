"""Reference routing is independent of GPS, model availability and formal itinerary data."""
import json
import time
from uuid import uuid4
from types import SimpleNamespace
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect
from app import language
from app.city import location_resolution as lr
from app.city import facts
from app.city.agent import provider,service,reference_routes
from app.city.agent import entrance_navigation as navigation


def rid():return str(uuid4())

@pytest.fixture
def reference(client,monkeypatch):
    service.TASKS.clear()
    navigation.CACHE.clear()
    monkeypatch.setattr(lr,'STORE',lr.ResolutionStore())
    monkeypatch.setenv('AMAP_REFERENCE_ROUTES_ENABLED','1')
    monkeypatch.setenv('AMAP_LOCATION_SEARCH_ENABLED','1')
    monkeypatch.setenv('AMAP_TERMS_REVIEWED','1')
    calls=[];settings={'ambiguous':False,'both':False,'poi_status':'1','route_status':'1','pois':{}}
    def handle(request):
        calls.append(request)
        if '/place/' in request.url.path:
            query=request.url.params['keywords']
            rows=[{'id':query+':a','name':query,'location':'114.3,34.8','adcode':'410202','citycode':'0371','address':'[合成测试]主站'}]
            if query in settings['pois']:
                return httpx.Response(200,json={'status':settings['poi_status'],'infocode':'10000','pois':settings['pois'][query]})
            rows[0]['location']=settings.get('origin_point','114.3,34.8') if query=='开封站' else '114.3,34.8'
            rows[0]['children']=settings.get('children',[]) if query=='合成园' else []
            if settings['ambiguous'] and (query=='开封站' or settings['both']):rows.append(rows[0]|{'id':query+':b','address':'[合成测试]另一地址','location':'114.31,34.81'})
            else:rows.append(rows[0]|{'id':query+':other','name':query+'附近小店'})
            return httpx.Response(200,json={'status':settings['poi_status'],'infocode':'10000','pois':rows})
        return httpx.Response(200,json={'status':settings['route_status'],'infocode':'10000','route':{'taxi_cost':settings.get('taxi_cost'),('transits' if '/transit/' in request.url.path else 'paths'):[{'distance':'1234','cost':{'duration':'720','tolls':settings.get('tolls')},'steps':[{'instruction':'[合成测试]沿测试路前行'}]}]}})
    def factory():
        instance=lr.amap.AmapProvider('fake-key',enabled=True,terms_reviewed=True,transport=httpx.MockTransport(handle),api_version='v5',retries=0)
        instance.interval=0
        return instance
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


def add_scenic(e, gates=('A门','B门','C门'), catalog=True):
    records=[{'kind':'place','fact_id':'synthetic:scenic','name':'合成园','place_type':'scenic','aliases':['合成景区']},
             {'kind':'place','fact_id':'synthetic:old','name':'旧合成园','place_type':'scenic','superseded_by':'synthetic:scenic'}]
    for index,name in enumerate(gates):
        place={'kind':'place','fact_id':f'synthetic:gate{index}','name':name,'place_type':'entrance'}
        if catalog and index==2:place.update(longitude=114.31,latitude=34.8,coordinate_system='GCJ02')
        records.extend([place,{'kind':'entrance','fact_id':f'synthetic:entry{index}','place_id':'synthetic:scenic',
                               'entrance_place_id':place['fact_id'],'direction':'both'}])
    if gates:
        records.append({'kind':'entrance','fact_id':'synthetic:duplicate','place_id':'synthetic:scenic',
                        'entrance_place_id':'synthetic:gate0','direction':'entry'})
        records.extend([{'kind':'place','fact_id':'synthetic:exit','name':'出口','place_type':'entrance'},
                        {'kind':'entrance','fact_id':'synthetic:exit-edge','place_id':'synthetic:scenic',
                         'entrance_place_id':'synthetic:exit','direction':'exit'}])
    with connect() as db:facts.import_bundle(facts.FactBundle.model_validate({'records':records}),db,apply=True)
    e['settings']['children']=[
        {'name':'合成园A门停车场','location':'114.3001,34.8'},
        {'name':'合成园B门酒店','location':'114.3001,34.8'},
        {'name':'别的园B门售票处','location':'114.3001,34.8'},
        {'name':'合成园A门售票处','location':'114.295,34.8'},
        {'name':'合成园B门游客中心','location':'114.305,34.8'},
        {'name':'合成园B门售票处','location':'114.306,34.8'}]


def poi(name, point='114.3,34.8', children=None):
    return {'id':name,'name':name,'location':point,'adcode':'410202','citycode':'0371','children':children or []}


def test_nearest_entrance_changes_with_origin_and_uses_cache(reference):
    e=reference;add_scenic(e);e['settings']['origin_point']='114.3049,34.8'
    result=run(e,'从开封站到合成园怎么走')['result'];route=result['reference_route']
    assert route['destination_entrance']=={'name':'B门','basis':'amap_poi_proxy','poi_name':'合成园B门游客中心','straight_m':9,'others':['A门','C门']}
    assert route['destination_point']==[114.305,34.8] and route['destination']=='合成园·B门'
    assert '合成园有3个入口资料，离起点最近的是B门' in result['reply']
    assert '其他入口：A门、C门。' in result['reply']
    assert e['calls'][-1].url.params['destination']=='114.305000,34.800000'
    assert len(e['calls'])==4 and e['calls'][-2].url.params['keywords']=='合成园'
    assert e['calls'][-2].url.path=='/v5/place/text'
    e['settings']['origin_point']='114.2951,34.8';before=len(e['calls'])
    result=run(e,'从开封站到合成园怎么走')['result']
    assert result['reference_route']['destination_entrance']['name']=='A门'
    assert len(e['calls'])-before==3
    assert result['reference_route']['entrances_verified'] is False


def test_catalog_entrance_does_not_need_proxy_for_its_position(reference):
    e=reference;add_scenic(e);e['settings']['origin_point']='114.3101,34.8'
    result=run(e,'从开封站到合成园怎么走')['result'];gate=result['reference_route']['destination_entrance']
    assert gate['name']=='C门' and gate['basis']=='catalog' and gate['poi_name'] is None
    assert '按资料库位置' in result['reply']
    assert result['reference_route']['destination_point']==[114.31,34.8]


@pytest.mark.parametrize('children',[
    [],
    [{'name':'合成园A门停车场','location':'114.3,34.8'},{'name':'合成园A门酒店','location':'114.3,34.8'}],
    [{'name':'合成园A门售票处','location':'115.3,34.8'}],
    [None,{'name':['走样'],'location':'114.3,34.8'},{'name':'合成园A门','location':'NaN,34.8'},
     {'name':'合成园A门','location':'114.3'},{'name':'合成园A门','location':'114.3,100'}],
])
def test_unlocated_entrances_keep_original_destination(reference,children):
    e=reference;add_scenic(e,('A门',),catalog=False)
    e['settings'].update(children=children);e['settings']['pois']['合成园门']=[]
    result=run(e,'从开封站到合成园怎么走')['result'];route=result['reference_route']
    assert '有入口资料（A门），但都还没有位置' in result['reply']
    assert route['destination_entrance'] is None and route['destination_point']==[114.3,34.8]
    assert e['calls'][-1].url.params['destination']=='114.300000,34.800000'
    assert len(e['calls'])==5
    before=len(e['calls']);assert run(e,'从开封站到合成园怎么走')['status']=='answered'
    assert len(e['calls'])-before==3


def test_second_search_matches_alias_and_normalized_gate_core(reference):
    e=reference;add_scenic(e,('南大门','西大门（原北门）'),catalog=False)
    e['settings']['children']=[]
    e['settings']['pois']['合成园门']=[poi('合成景区南门游客中心','114.301,34.8'),poi('合成园西大门售票处','114.299,34.8')]
    result=run(e,'从开封站到合成园怎么走')['result']
    assert result['reference_route']['destination_entrance']['name'] in ('南大门','西大门（原北门）')
    assert len(e['calls'])==5
    assert e['calls'][-2].url.params['keywords']=='合成园门'


def test_scenic_without_entrances_and_non_scenic_behavior(reference):
    e=reference;add_scenic(e,())
    result=run(e,'从开封站到合成园怎么走')['result']
    assert '暂无合成园的入口资料，终点按高德地点位置。' in result['reply']
    assert result['reference_route']['destination_entrance'] is None and len(e['calls'])==3
    before=len(e['calls']);result=run(e,'从开封站到普通地点怎么走')['result']
    assert result['reply']=='开封站 → 普通地点，已查得步行参考路线。以下为高德当前估计，出入口尚未核验，不是已确认行程。'
    assert len(e['calls'])-before==3 and result['reference_route']['destination_entrance'] is None


@pytest.mark.parametrize('bad_result',[None,SimpleNamespace(pois=None)])
def test_entrance_provider_exception_or_bad_response_does_not_fail_route(reference,monkeypatch,bad_result):
    e=reference;add_scenic(e,('A门',),catalog=False)
    factory=lr.provider_from_environment;searches=[];instances=[]
    def wrapped():
        instance=factory();instances.append(instance)
        if len(instances)<3:return instance
        def search(query,**kwargs):
            searches.append(query)
            if bad_result is None:raise TimeoutError('synthetic entrance timeout')
            return bad_result
        instance.search_pois=search
        return instance
    monkeypatch.setattr(lr,'provider_from_environment',wrapped)
    result=run(e,'从开封站到合成园怎么走')
    assert result['status']=='answered' and '但都还没有位置' in result['result']['reply']
    assert searches==['合成园','合成园门']


def test_scenic_resolution_entrance_filter_nearest_and_core_rules(reference):
    e=reference;add_scenic(e)
    with connect() as db:catalog=facts.records(db)
    scenic=navigation.scenic_for('旧合成园',catalog)
    assert scenic.fact_id=='synthetic:scenic'
    assert navigation.scenic_for('合成景区',catalog)==scenic
    assert navigation.scenic_for('合成',catalog) is None
    items=navigation.entrances(scenic,catalog)
    assert [i['name'] for i in items]==['A门','B门','C门']
    assert navigation.matches(scenic,'南大门','合成园南门游客中心')
    assert navigation.matches(scenic,'西大门（原北门）','合成园西大门售票处')
    assert not navigation.matches(scenic,'南门','合成园南门酒店')
    assert not navigation.matches(scenic,'南门','别园南门游客中心')
    chosen,meters=navigation.nearest(SimpleNamespace(longitude=114.31,latitude=34.8),items)
    assert chosen['name']=='C门' and meters==0
    assert navigation.nearest(SimpleNamespace(longitude=114.31,latitude=34.8),[])==(None,None)


def test_entrance_cache_expires_and_non_gcj_catalog_coordinates_are_not_used(reference,monkeypatch):
    e=reference;add_scenic(e,('A门',),catalog=False)
    with connect() as db:catalog=facts.records(db)
    scenic=navigation.scenic_for('合成园',catalog)
    place=catalog[('place','synthetic:gate0')]
    catalog[('place',place.fact_id)]=place.model_copy(update={'longitude':114.3,'latitude':34.8,'coordinate_system':'WGS84'})
    items=navigation.entrances(scenic,catalog)
    assert items[0]['longitude'] is None
    clock=[0.0];monkeypatch.setattr(navigation.time,'monotonic',lambda:clock[0])
    around=SimpleNamespace(longitude=114.3,latitude=34.8)
    fake=SimpleNamespace(search_pois=lambda *a,**k:SimpleNamespace(pois=()))
    navigation.locate_entrances(scenic,items,fake,around)
    clock[0]=navigation.TTL-1
    fake.search_pois=lambda *a,**k:pytest.fail('cached entrance lookup')
    navigation.locate_entrances(scenic,items,fake,around)
    clock[0]=navigation.TTL
    calls=[];fake.search_pois=lambda *a,**k:(calls.append(a[0]) or SimpleNamespace(pois=()))
    navigation.locate_entrances(scenic,items,fake,around)
    assert calls==['合成园','合成园门']


def test_entrance_queries_respect_provider_pacing_before_estimate(reference,monkeypatch):
    e=reference;add_scenic(e,('A门','B门'),catalog=False)
    e['settings']['children']=[];e['settings']['pois']['合成园门']=[]
    factory=lr.provider_from_environment;ticks=[0.0];waits=[]
    def wrapped():
        instance=factory();instance.interval=0.5;instance.clock=lambda:ticks[0]
        def advance(seconds):
            waits.append(seconds);ticks[0]+=seconds
        instance.sleeper=advance
        return instance
    monkeypatch.setattr(lr,'provider_from_environment',wrapped)
    result=run(e,'从开封站到合成园怎么走')
    assert result['status']=='answered' and len(e['calls'])==5 and waits==[0.5,0.5]


def test_catalog_only_entrance_and_selected_poi_name_fallback(reference):
    e=reference;add_scenic(e,('A门','B门','C门'))
    with connect() as db:catalog=facts.records(db)
    scenic=navigation.scenic_for('合成园',catalog)
    records=[]
    for index in (0,1):
        place=catalog[('place',f'synthetic:gate{index}')]
        records.append(place.model_copy(update={'version':2,'longitude':114.29+index*0.005,'latitude':34.8,'coordinate_system':'GCJ02'}))
    with connect() as db:facts.import_bundle(facts.FactBundle(records=tuple(records)),db,apply=True)
    e['settings']['pois']['园区全称查询']=[poi('合成园')]
    task=run(e,'从开封站到园区全称查询怎么走')
    result=drive(e,select(e,task,task['result']['location_choices'][0]).json())['result']
    assert result['reference_route']['destination_entrance']['basis']=='catalog'
    assert len(e['calls'])==3


def test_closed_ticket_office_is_not_an_entrance_proxy():
    from app.city.agent.entrance_navigation import matches
    scenic=SimpleNamespace(name='龙亭景区',aliases=())
    assert not matches(scenic,'南门','龙亭景区南门售票处(暂停营业)')
    assert matches(scenic,'北门','龙亭景区北门售票处')


@pytest.mark.parametrize('mode,text,expected',[
    ('transit','从开封站去清明上河园怎么走','walking'),('driving','从开封站到大相国寺怎么走','walking'),
    ('transit','从开封站坐公交去清明上河园怎么走','transit'),('driving','从开封府开车去万岁山怎么走','driving'),
    ('driving','从开封府打车去万岁山怎么走','driving'),('driving','从开封站坐出租去龙亭怎么走','driving'),
])
def test_unrequested_mode_falls_back_to_walking_without_losing_the_route(reference,monkeypatch,mode,text,expected):
    # The live model sometimes picks transit for a "站" origin; only that value is unsupported.
    monkeypatch.setattr(language,'available',lambda:True)
    origin,destination=text[1:].split('去' if '去' in text else '到',1)
    for word in ('坐公交','开车','打车','坐出租'):origin=origin.replace(word,'')
    destination=destination.removesuffix('怎么走')
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','quote':text}],'route_query':{
        'quote':text,'origin':origin,'destination':destination,'mode':mode}},{'source':'mock_model','http_attempts':1}))
    t=run(reference,text)
    assert t['mode']=='reference_route' and '格式无法核对' not in json.dumps(t,ensure_ascii=False)
    # The route request goes out with the visitor's own mode, or walking when none was said.
    assert {'walking':'/walking','transit':'/transit','driving':'/driving'}[expected] in reference['calls'][-1].url.path
    if expected=='walking':assert t['status']=='answered' and t['result']['reference_route']['mode']=='walking'


@pytest.mark.parametrize('tolls', ['0','2.5',None])
def test_driving_reference_reports_vehicle_estimates(reference,monkeypatch,tolls):
    e=reference;e['settings'].update(taxi_cost='15',tolls=tolls)
    text='从开封站开车去普通地点怎么走'
    route_model(monkeypatch,text,'普通地点','driving')
    result=run(e,text)['result']
    assert '高德估计打车约15元（按车计）。' in result['reply']
    route=result['reference_route']
    assert route['taxi_cost_fen']==1500 and route['parking']==[]
    if tolls=='2.5':assert '过路费约2.5元。' in result['reply'] and route['tolls_fen']==250
    else:assert '过路费' not in result['reply']
    assert len(e['calls'])==3


@pytest.mark.parametrize('mode,words', [('walking','步行'),('transit','坐公交'),('driving','开车')])
def test_parking_reuses_entrance_pool_and_orders_from_actual_gate(reference,monkeypatch,mode,words):
    e=reference;add_scenic(e);e['settings'].update(origin_point='114.3049,34.8',taxi_cost='15',tolls='0')
    e['settings']['pois']['合成园']=[poi('合成园',children=[
        *e['settings']['children'],
        {'name':'合成景区停车场甲','location':'114.3051,34.8'},
        {'name':'合成园停车场乙','location':'114.3052,34.8'},
        {'name':'合成园停车场坏坐标','location':'NaN,34.8'},
        {'name':'合成园停车场坏名称','location':'114.3'},
        {'name':'合成园停车酒店','location':'114.305,34.8'},
        {'name':'合成园停车场甲(暂停营业)','location':'114.305,34.8'},
        {'name':'合成园酒店','location':'114.305,34.8'},
        {'name':'合成园停车场(内部专用)','location':'114.305,34.8'},{'name':'合成园内部停车场','location':'114.305,34.8'},
        {'name':'别园停车场','location':'114.305,34.8'}]),
        poi('合成园停车场丙','114.3053,34.8'),poi('合成园游客中心','114.305,34.8')]
    text=f'从开封站{words}去合成园怎么走'
    route_model(monkeypatch,text,'合成园',mode)
    result=run(e,text)['result'];route=result['reference_route']
    assert route['mode']==mode and route['destination_point']==[114.305,34.8]
    if mode=='driving':
        assert route['parking']==[{'name':'合成景区停车场甲','straight_m':9},{'name':'合成园停车场乙','straight_m':18}]
        assert '附近停车场（高德资料，收费与车位未核实）：合成景区停车场甲（约9米）、合成园停车场乙（约18米）。' in result['reply']
        assert route['taxi_cost_fen']==1500
    else:
        assert route['parking']==[] and route['taxi_cost_fen'] is None and route['tolls_fen'] is None
        assert '停车场' not in result['reply'] and '打车' not in result['reply']
    assert len(e['calls'])==4
    before=len(e['calls'])
    cached=run(e,text)['result']['reference_route']
    assert cached['parking']==route['parking'] and len(e['calls'])-before==3


def test_parking_in_top_level_poi(reference,monkeypatch):
    e=reference;add_scenic(e,('A门',),catalog=False)
    e['settings']['pois']['合成园']=[poi('合成园'),poi('合成园A门售票处','114.301,34.8'),poi('合成园停车场','114.3011,34.8')]
    text='从开封站开车去合成园怎么走'
    route_model(monkeypatch,text,'合成园','driving')
    route=run(e,text)['result']['reference_route']
    assert route['parking']==[{'name':'合成园停车场','straight_m':9}] and len(e['calls'])==4


def test_parking_does_not_trigger_search_without_entrances(reference,monkeypatch):
    e=reference;add_scenic(e,())
    before=len(e['calls'])
    text='从开封站开车去合成园怎么走'
    route_model(monkeypatch,text,'合成园','driving')
    route=run(e,text)['result']['reference_route']
    assert route['parking']==[] and len(e['calls'])-before==3



def route_model(monkeypatch,text,destination,mode):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'query','quote':text}],
        'route_query':{'quote':text,'origin':'开封站','destination':destination,'mode':mode}},
        {'source':'mock_model','http_attempts':1}))
