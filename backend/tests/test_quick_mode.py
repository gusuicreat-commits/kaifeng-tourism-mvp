"""Quick interaction fills bounded single-day and multi-day planning gaps."""
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app import language
from app.city.agent import provider, planning_intent, understand
from app.city.agent.models import PlanningIntent, Understanding
from app.city.agent.orchestration import QUICK_PLACE_ID
from app.city.assistant import context, service as assistant_service, tools
from app.city.engine import service as engine_service
from app.city import state
from app.memory import session as memory_session, storage as memory_storage
from app.db import connect
from test_city_assistant import city, planned
from test_city_agent import snapshot
from test_city_engine_api import engine
from test_multi_day_api import multi_engine


def _path(e, suffix=''):
    return f"/api/city/v1/trips/{e['trip']['id']}{suffix}"


@pytest.fixture
def reference_city(client, monkeypatch):
    from app.city import facts
    from app.city.engine import live_travel
    from app.city.engine.amap import Quote
    bundle=Path(__file__).resolve().parents[2] / 'data/mvp/city-facts-reference-2026-10-25.json'
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(json.loads(bundle.read_text())),db,apply=True)
    moment=datetime.fromisoformat('2026-10-24T19:00:00+08:00')
    for module in (memory_storage,memory_session,engine_service,state,context,assistant_service,tools):
        monkeypatch.setattr(module,'now',lambda moment=moment:moment)
    monkeypatch.setattr(language,'available',lambda:True)
    class FakeProvider:
        request_count=0
        interval=0
        def estimate(self, origin, destination, mode, departure, checked):
            self.request_count+=1
            return Quote('estimated',duration_seconds=600,distance_m=700,geometry_available=True,queried_at=checked.isoformat())
        def close(self): pass
    monkeypatch.setenv('AMAP_PLANNING_ENABLED','1')
    monkeypatch.setattr(live_travel,'provider_from_environment',lambda:FakeProvider())
    client.post('/api/v1/sessions')
    trip=client.post('/api/city/v1/trips',json={'request_id':str(uuid4()),'draft':{}}).json()
    added=client.patch(_path({'trip':trip},'/basket'),json={
        'request_id':str(uuid4()),'expected_version':0,'operation':'put',
        'item':{'kind':'place','object_id':'pl_wb_000013','priority':'want','status':'pending','source':'detail'}})
    assert added.status_code==200,added.text
    return {'client':client,'trip':trip}


def _run(e,text,mode=None,raw=None,monkeypatch=None):
    if raw is None:
        raw={'actions':[{'kind':'modify','quote':text}],
             'planning':{'operation':'update','quote':text,'fields':[
                 {'field':'pace','value':'relaxed','quote':text}], 'choices':[]}}
    if monkeypatch:
        monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    current=e['client'].get(_path(e)).json()
    body={'request_id':str(uuid4()),'text':text,'expected_version':current['version'],
          'expected_route_version':current['route_version']}
    if mode is not None:body['interaction_mode']=mode
    task=e['client'].post(_path(e,'/agent/tasks'),json=body).json()
    for _ in range(12):
        if task['status']!='running':break
        task=e['client'].post(_path(e,'/agent/tasks/'+task['task_id']+'/step'),json={
            'request_id':str(uuid4()),'expected_step':task['step']}).json()
    assert task['status']!='running',task
    return task


def saved(e):
    return e['client'].get(_path(e,'/exploration/constraints')).json()['constraints']


def test_quick_defaults_all_single_day_gaps(reference_city,monkeypatch):
    task=_run(reference_city,'帮我轻松安排一下','quick',monkeypatch=monkeypatch)
    assert task['status']=='answered',task
    reply=task['result']['reply']
    tomorrow=(datetime.fromisoformat('2026-10-24T19:00:00+08:00').date()+timedelta(days=1))
    assert reply.startswith(f'没说的我先按明天（{tomorrow.month}月{tomorrow.day}日）')
    assert '9:00—20:00' in reply and '从开封府出发并返回' in reply
    constraints=saved(reference_city)
    assert constraints['business_date']==tomorrow.isoformat()
    assert constraints['starts_at'].endswith('T09:00:00+08:00')
    assert constraints['ends_at'].endswith('T20:00:00+08:00')
    assert constraints['origin_place_id']==constraints['destination_place_id']==QUICK_PLACE_ID
    assert '确认方案' in reply


def test_quick_uses_visitor_date_and_only_lists_missing_fields(reference_city,monkeypatch):
    text='后天轻松安排一下'
    raw={'actions':[{'kind':'modify','quote':text}],
         'planning':{'operation':'update','quote':text,'fields':[
             {'field':'date','value':'后天','quote':'后天'},
             {'field':'pace','value':'relaxed','quote':'轻松'}], 'choices':[]}}
    task=_run(reference_city,text,'quick',raw,monkeypatch)
    assert task['status'] in ('answered','needs_data'),task
    assert '明天' not in task['result']['reply'] and '9:00—20:00' in task['result']['reply']
    assert saved(reference_city)['business_date']=='2026-10-26'


def test_quick_with_date_and_time_lists_only_default_endpoints(reference_city,monkeypatch):
    text='明天九点到六点轻松安排一下'
    raw={'actions':[{'kind':'modify','quote':text}],
         'planning':{'operation':'update','quote':text,'fields':[
             {'field':'date','value':'明天','quote':'明天'},
             {'field':'start_time','value':'09:00','quote':'九点'},
             {'field':'end_time','value':'18:00','quote':'六点'},
             {'field':'pace','value':'relaxed','quote':'轻松'}], 'choices':[]}}
    task=_run(reference_city,text,'quick',raw,monkeypatch)
    assert task['status'] in ('answered','needs_data'),task
    reply=task['result']['reply']
    assert '从开封府出发并返回' in reply and '9:00—20:00' not in reply
    assert '明天（' not in reply
    assert saved(reference_city)['starts_at'].endswith('T09:00:00+08:00')
    assert saved(reference_city)['ends_at'].endswith('T18:00:00+08:00')


def test_quick_multiday_defaults_all_days(reference_city,monkeypatch):
    calls=[];preview=engine_service.preview
    def record(*a,**kw):
        calls.append(a[2].requirements)
        return preview(*a,**kw)
    monkeypatch.setattr(engine_service,'preview',record)
    text='玩三天，轻松安排一下'
    raw={'actions':[{'kind':'modify','quote':text}],
         'planning':{'operation':'update','quote':text,'fields':[
             {'field':'trip_days','value':3,'quote':'三天'},
             {'field':'pace','value':'relaxed','quote':'轻松'}], 'choices':[]}}
    task=_run(reference_city,text,'quick',raw,monkeypatch)
    assert task['status']=='needs_confirmation',task
    reply=task['result']['reply']
    assert reply.startswith('没说的我先按明天（10月25日）')
    assert '每天9:00—20:00' in reply and '从开封府出发并返回' in reply
    constraints=saved(reference_city)
    assert constraints['start_date']=='2026-10-25' and constraints['end_date']=='2026-10-27'
    assert len(constraints['days'])==3
    assert all(d['starts_at'].endswith('T09:00:00+08:00') and d['ends_at'].endswith('T20:00:00+08:00')
               and d['origin_place_id']==d['destination_place_id']==QUICK_PLACE_ID for d in constraints['days'])
    assert len(constraints['overnights'])==2
    assert len(calls)==1 and len(calls[0].days)==3
    assert all(o.place_id==QUICK_PLACE_ID for o in calls[0].overnights)
    assert reference_city['client'].get(_path(reference_city)).json()['route_version']==0


@pytest.mark.parametrize('distorted',[False,True])
def test_quick_multiday_preserves_daily_start_time(reference_city,monkeypatch,distorted):
    text='玩三天，每天十点出发，轻松安排一下'
    raw={'actions':[{'kind':'modify','quote':text}],
         'planning':{'operation':'update','quote':text,'fields':[
             {'field':'trip_days','value':3,'quote':'三天'},
             {'field':'start_time','value':'10:00','quote':'每天十点出发','scope':'every_day'},
             {'field':'pace','value':'relaxed','quote':'轻松'}], 'choices':[]}}
    if distorted:
        raw['planning']['fields'][1]['day_ref']='第二天'
        raw['planning']['fields'].append(copy.deepcopy(raw['planning']['fields'][1]))
        raw['actions']*=2
        raw['extra']='好的，开始安排。'
    task=_run(reference_city,text,'quick',raw,monkeypatch)
    assert task['status']=='needs_confirmation',task
    reply=task['result']['reply']
    assert '每天20:00 结束' in reply and '9:00' not in reply
    assert all(d['starts_at'].endswith('T10:00:00+08:00') for d in saved(reference_city)['days'])
    assert not saved(reference_city)['pending_fields']


def test_quick_multiday_preserves_explicit_second_day_condition(reference_city,monkeypatch):
    text='玩三天，第二天十点出发，轻松安排一下'
    raw={'actions':[{'kind':'modify','quote':text}],
         'planning':{'operation':'update','quote':text,'fields':[
             {'field':'trip_days','value':3,'quote':'三天'},
             {'field':'start_time','value':'10:00','quote':'第二天十点出发','day_ref':'第二天'},
             {'field':'pace','value':'relaxed','quote':'轻松'}], 'choices':[]}}
    task=_run(reference_city,text,'quick',raw,monkeypatch)
    assert task['status']=='needs_confirmation',task
    starts=[d['starts_at'] for d in saved(reference_city)['days']]
    assert starts==['2026-10-25T09:00:00+08:00','2026-10-26T10:00:00+08:00','2026-10-27T09:00:00+08:00']
    assert '每天9:00' not in task['result']['reply']
    assert '第1天9:00 开始' in task['result']['reply'] and '第3天9:00 开始' in task['result']['reply']


@pytest.mark.parametrize('daily',[False,True])
def test_quick_multiday_without_duration_still_asks_for_days(reference_city,monkeypatch,daily):
    text='玩几天，你看着安排'+('，每天十点出发' if daily else '')
    raw={'actions':[{'kind':'modify','quote':text}],
         'planning':{'operation':'update','quote':text,'fields':[
             {'field':'trip_days','value':None,'quote':'玩几天'}]+([
             {'field':'start_time','value':'10:00','quote':'每天十点出发','scope':'every_day'}] if daily else []), 'choices':[]}}
    task=_run(reference_city,text,'quick',raw,monkeypatch)
    assert task['status']=='needs_clarification',task
    assert '这趟行程玩几天，或哪天结束？' in task['result']['reply']
    assert '没说的我先按' not in task['result']['reply']
    assert not saved(reference_city)['start_date'] and not saved(reference_city)['days']


def test_deep_multiday_known_duration_keeps_questions(reference_city,monkeypatch):
    text='玩三天，轻松安排一下'
    raw=_planning_output(text,[{'field':'trip_days','value':3,'quote':'三天'},
                              {'field':'pace','value':'relaxed','quote':'轻松'}])
    task=_run(reference_city,text,'deep',raw,monkeypatch)
    assert task['status']=='needs_clarification',task
    assert task['result']['reply'].startswith('你打算哪天开始这趟行程？')
    assert '没说的我先按' not in task['result']['reply']
    assert not saved(reference_city)['start_date'] and not saved(reference_city)['days']


@pytest.mark.parametrize('text,fields,end',[
    ('住两晚，你看着安排',[{'field':'trip_nights','value':2,'quote':'两晚'}],'2026-10-27'),
    ('10月27日结束，你看着安排',[{'field':'end_date','value':'2026-10-27','quote':'10月27日'}],'2026-10-27'),
    ('10月26日开始玩两天，你看着安排',[
        {'field':'start_date','value':'2026-10-26','quote':'10月26日'},
        {'field':'trip_days','value':2,'quote':'两天'}],'2026-10-27'),
])
def test_quick_multiday_uses_nights_or_dates(reference_city,monkeypatch,text,fields,end):
    task=_run(reference_city,text,'quick',_planning_output(text,fields),monkeypatch)
    assert task['status']=='needs_confirmation',task
    if any(f['field'] in ('start_date','end_date') for f in fields):
        assert not any(f['field']=='date' for f in task['understanding']['planning']['fields'])
        assert {'field':'date','source':'date_grammar'} not in task['interpretation'].get('recovered_fields',[])
    values=saved(reference_city)
    assert values['start_date']==('2026-10-26' if '开始' in text else '2026-10-25')
    assert values['end_date']==end
    assert all(d['starts_at'].endswith('T09:00:00+08:00') and d['ends_at'].endswith('T20:00:00+08:00') for d in values['days'])
    if '开始' in text:assert '明天' not in task['result']['reply']


def test_quick_multiday_keeps_saved_clarification(reference_city,monkeypatch):
    from app.city import exploration_state
    from app.city.engine.models import MultiDayConstraints
    question='预算有冲突，请确认采用哪一笔预算？'
    values=MultiDayConstraints(schema_version='city-constraints/2',trip_days=3,clarifications=[question])
    with connect() as db:exploration_state.save(db,reference_city['trip']['id'],values.model_dump(mode='json'),None,0)
    monkeypatch.setattr(engine_service,'preview',lambda *a,**kw:pytest.fail('unresolved clarification must not preview'))
    text='你看着安排吧'
    task=_run(reference_city,text,'quick',{'planning':{'operation':'continue','quote':text}},monkeypatch)
    assert task['status']=='needs_clarification',task
    assert task['result']['reply'].startswith('没说的我先按明天（10月25日）')
    assert question in task['result']['reply'] and saved(reference_city)['clarifications']==[question]


def test_deep_multiday_without_duration_keeps_questions(reference_city,monkeypatch):
    text='每天十点出发，轻松安排一下'
    raw={'actions':[{'kind':'modify','quote':text}],
         'planning':{'operation':'update','quote':text,'fields':[
             {'field':'start_time','value':'10:00','quote':'每天十点出发','scope':'every_day'},
             {'field':'pace','value':'relaxed','quote':'轻松'}], 'choices':[]}}
    task=_run(reference_city,text,raw=raw,monkeypatch=monkeypatch)
    assert task['status']=='needs_clarification',task
    assert task['result']['reply'].startswith('你打算哪天开始这趟行程？')
    assert '没说的我先按' not in task['result']['reply']


def test_quick_multiday_keeps_budget_conflict_after_defaults(reference_city,monkeypatch):
    text='玩三天，预算是500元还是预算是800元，轻松安排一下'
    raw={'actions':[{'kind':'modify','quote':text}],
         'planning':{'operation':'update','quote':text,'fields':[
             {'field':'trip_days','value':3,'quote':'三天'},
             {'field':'budget_fen','value':50000,'budget_basis':'total','quote':'预算是500元'},
             {'field':'budget_fen','value':80000,'budget_basis':'total','quote':'预算是800元'},
             {'field':'pace','value':'relaxed','quote':'轻松'}], 'choices':[]}}
    task=_run(reference_city,text,'quick',raw,monkeypatch)
    assert task['status']=='needs_clarification',task
    reply=task['result']['reply']
    assert reply.startswith('没说的我先按明天（10月25日）') and '50000' in reply and '80000' in reply
    assert saved(reference_city)['start_date']=='2026-10-25'


@pytest.mark.parametrize('fields,text',[
    ([{'field':'trip_days','value':4,'quote':'四天'}],'玩四天，你看着安排'),
    ([{'field':'trip_days','value':3,'quote':'三天'},
      {'field':'start_time','value':'21:00','quote':'每天晚上九点出发','scope':'every_day'}],
     '玩三天，每天晚上九点出发，你看着安排'),
])
def test_quick_multiday_defaults_still_respect_range_and_time_limits(reference_city,monkeypatch,fields,text):
    monkeypatch.setattr(engine_service,'preview',lambda *a,**kw:pytest.fail('invalid dates or windows must not preview'))
    task=_run(reference_city,text,'quick',_planning_output(text,fields),monkeypatch)
    assert task['status']=='needs_clarification',task
    assert saved(reference_city)['clarifications']


def test_deep_mode_keeps_existing_question(reference_city,monkeypatch):
    task=_run(reference_city,'帮我轻松安排一下',monkeypatch=monkeypatch)
    assert task['status']=='needs_clarification'
    assert task['result']['reply'].startswith('你打算哪天去？')


def test_start_task_interaction_mode_contract(client):
    from app.city.agent.models import StartTask
    body=StartTask(request_id=uuid4(),text='x',expected_version=1,expected_route_version=0)
    assert body.interaction_mode=='deep'
    with pytest.raises(ValueError):
        StartTask(request_id=uuid4(),text='x',expected_version=1,expected_route_version=0,interaction_mode='fast')


def test_start_task_invalid_interaction_mode_returns_422(reference_city):
    current=reference_city['client'].get(_path(reference_city)).json()
    response=reference_city['client'].post(_path(reference_city,'/agent/tasks'),json={
        'request_id':str(uuid4()),'text':'安排一下','expected_version':current['version'],
        'expected_route_version':current['route_version'],'interaction_mode':'fast'})
    assert response.status_code==422


@pytest.mark.parametrize('text',[
    '直接帮我排个行程吧','给我安排一下','排个路线','看着安排吧',
    '请替我们规划一份方案','做个一日游攻略','你看着办吧',
])
def test_continue_accepts_explicit_planning_requests(city,text):
    snap,_=snapshot(city)
    intent=PlanningIntent(operation='continue',quote=text)
    assert planning_intent.grounded(intent,text,snap)==intent


@pytest.mark.parametrize('text',[
    '行程排得太满了','我不想排太多景点','这个方案不行','你安排得太紧了',
])
def test_continue_rejects_non_planning_requests(city,text):
    snap,_=snapshot(city)
    with pytest.raises(ValueError,match='continue needs an explicit planning request'):
        planning_intent.grounded(PlanningIntent(operation='continue',quote=text),text,snap)


@pytest.mark.parametrize('mode',['quick','deep'])
def test_continue_without_fields_uses_interaction_mode(reference_city,monkeypatch,mode):
    text='直接帮我排个行程吧'
    raw={'planning':{'operation':'continue','quote':text,'fields':[],'choices':[]}}
    task=_run(reference_city,text,mode,raw,monkeypatch)
    assert task['mode']=='planning',task
    assert task['understanding']['planning']['operation']=='continue'
    if mode=='quick':
        assert task['status']=='answered',task
        assert '没说的我先按' in task['result']['reply'] and '确认方案' in task['result']['reply']
        assert saved(reference_city)['business_date']=='2026-10-25'
    else:
        assert task['status']=='needs_clarification',task
        assert task['result']['reply'].startswith('你打算哪天去？')


def _preference_output(text):
    return {'actions':[{'kind':'preference','quote':text}], 'constraints':[
        {'field':'pace','value':'relaxed','strength':'soft','quote':text},
        {'field':'objective','value':'lower_cost','strength':'soft','quote':text}]}


@pytest.mark.parametrize('mode',['quick','deep'])
@pytest.mark.parametrize('text',['不想太累，别花太多钱','步调放缓些，花销压低些','轻松一点，尽量省钱'])
def test_unplanned_preferences_recover_planning(reference_city,monkeypatch,mode,text):
    task=_run(reference_city,text,mode,_preference_output(text),monkeypatch)
    assert task['mode']=='planning',task
    assert {'from':'constraints','to':'planning'} in task['interpretation']['recovered_fields']
    intent=task['understanding']
    assert not intent['constraints'] and not intent['actions']
    assert intent['planning']['operation']=='update' and intent['planning']['quote']==text
    assert {f['field']:f['value'] for f in intent['planning']['fields']}=={'pace':'relaxed','objective':'lower_cost'}
    reply=task['result']['reply']
    if mode=='quick':
        assert task['status']=='answered' and '没说的我先按' in reply and '确认方案' in reply,task
    else:
        assert task['status']=='needs_clarification' and reply.startswith('你打算哪天去？'),task
        assert '调整为放松' in reply
    assert reference_city['client'].get(_path(reference_city)).json()['route_version']==0


def _planning_output(text,fields):
    return {'actions':[{'kind':'modify','quote':text}],
            'planning':{'operation':'update','quote':text,'fields':fields,'choices':[]}}


def test_new_single_day_pace_and_mode_are_echoed_in_order_and_saved(reference_city,monkeypatch):
    text='明天九点到六点，节奏轻松点，坐公交'
    fields=[
        {'field':'date','value':'明天','quote':'明天'},
        {'field':'start_time','value':'09:00','quote':'九点'},
        {'field':'end_time','value':'18:00','quote':'六点'},
        {'field':'pace','value':'relaxed','quote':'节奏轻松点'},
        {'field':'mode','value':'transit','quote':'坐公交'},
    ]
    task=_run(reference_city,text,'quick',_planning_output(text,fields),monkeypatch)
    assert task['status'] in ('answered','needs_confirmation','needs_data'),task
    reply=task['result']['reply']
    pace='已按“节奏轻松点”调整为放松。';mode='已按“坐公交”把交通方式改为公交。'
    assert pace in reply and mode in reply and reply.index(pace)<reply.index(mode)
    assert saved(reference_city)['modes']==['transit']


@pytest.mark.parametrize('value,quote,label',[('walking','改成步行','步行'),('transit','坐公交','公交'),('driving','打车','打车 / 自驾')])
def test_mode_preference_note_uses_all_supported_labels(city,value,quote,label):
    intent=Understanding(actions=[{'kind':'modify','quote':quote}],planning=PlanningIntent(operation='update',quote=quote,fields=[
        {'field':'mode','value':value,'quote':quote}]))
    assert planning_intent.preference_notes(intent)==[f'已按“{quote}”把交通方式改为{label}。']


def test_duplicate_mode_notes_are_deduplicated_after_existing_preferences(city):
    intent=Understanding(actions=[{'kind':'modify','quote':'节奏轻松点，坐公交'}],planning=PlanningIntent(operation='update',quote='节奏轻松点，坐公交',fields=[
        {'field':'mode','value':'transit','quote':'坐公交'},
        {'field':'pace','value':'relaxed','quote':'节奏轻松点'},
        {'field':'mode','value':'transit','quote':'坐公交'}]))
    assert planning_intent.preference_notes(intent)==[
        '已按“节奏轻松点”调整为放松。','已按“坐公交”把交通方式改为公交。']


def test_negated_mode_is_dropped_without_transport_note(city,monkeypatch):
    monkeypatch.setattr(language,'available',lambda:True)
    text='不要坐公交'
    task=_run(city,text,raw=_planning_output(text,[
        {'field':'mode','value':'transit','quote':text}]),monkeypatch=monkeypatch)
    assert task['interpretation']['dropped_fields']==[{'field':'mode','check':'preference_not_in_quote'}]
    assert '把交通方式改为' not in task['result']['reply']


def test_confirmed_single_day_mode_change_is_echoed(city,monkeypatch):
    planned(city)
    monkeypatch.setattr(language,'available',lambda:True)
    text='改成公交'
    task=_run(city,text,raw=_planning_output(text,[
        {'field':'mode','value':'transit','quote':text}]),monkeypatch=monkeypatch)
    assert task['status'] in ('needs_confirmation','needs_data'),task
    assert '已按“改成公交”把交通方式改为公交。' in task['result']['reply']


def test_confirmed_multiday_mode_replan_echoes_note(multi_engine,monkeypatch):
    from app import language
    from app.city.agent import provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    from test_multi_day_api import committed,start_today
    e,_,_=committed(multi_engine);moment=start_today(e,monkeypatch)
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    text='今天跟明天都改成打车'
    raw=_planning_output(text,[{'field':'mode','value':'driving','quote':'改成打车','scope':'every_day'}])
    agent.TASKS.clear()
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    task=run({'client':e[0],'trip':e[1]},text)
    assert task['status'] in ('needs_confirmation','needs_data'),task
    assert '已按“改成打车”把交通方式改为打车 / 自驾。' in task['result']['reply']
    plan=task['result']['planning']['result']['itineraries'][0]
    assert all(tuple(day['requirements']['modes'])==('driving',) for day in plan['days'][1:])
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()['route_version']==1
    agent.TASKS.clear()


def test_confirmed_preferences_keep_workflow(city,monkeypatch):
    planned(city)
    monkeypatch.setattr(language,'available',lambda:True)
    text='不想太累，别花太多钱'
    task=_run(city,text,raw=_preference_output(text),monkeypatch=monkeypatch)
    assert task['mode']=='workflow',task
    assert task['understanding']['planning'] is None
    assert len(task['understanding']['constraints'])==2
    assert {'from':'constraints','to':'planning'} not in task['interpretation'].get('recovered_fields',[])


@pytest.mark.parametrize('other',['food','correction','action','empty_actions'])
def test_mixed_preferences_do_not_recover_planning(city,monkeypatch,other):
    text='轻松一点，别花太多钱，想吃灌汤包'
    raw=_preference_output(text)
    if other=='food':raw['constraints'].append({'field':'food','value':'灌汤包','strength':'soft','quote':'想吃灌汤包'})
    elif other=='correction':
        text='紧凑一点，不，轻松一点，别花太多钱'
        raw=_preference_output(text)
        raw['corrections']=[{'before':'紧凑一点','after':'轻松一点','quote':text}]
    elif other=='action':raw['actions'].append({'kind':'query','purpose':'trip','quote':text})
    else:raw['actions']=[]
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    snap,_=snapshot(city)
    intent,meta=understand.parse(text,snap)
    assert intent.planning is None
    assert {'from':'constraints','to':'planning'} not in meta.get('recovered_fields',[])
