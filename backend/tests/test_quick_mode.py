"""Quick interaction fills only bounded single-day planning gaps."""
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app import language
from app.city.agent import provider
from app.city.agent.orchestration import QUICK_PLACE_ID
from app.city.assistant import context, service as assistant_service, tools
from app.city.engine import service as engine_service
from app.city import state
from app.memory import session as memory_session, storage as memory_storage
from app.db import connect


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


def test_quick_multiday_keeps_daily_questions(reference_city,monkeypatch):
    text='玩两天，轻松安排一下'
    raw={'actions':[{'kind':'modify','quote':text}],
         'planning':{'operation':'update','quote':text,'fields':[
             {'field':'trip_days','value':2,'quote':'两天'},
             {'field':'pace','value':'relaxed','quote':'轻松'}], 'choices':[]}}
    task=_run(reference_city,text,'quick',raw,monkeypatch)
    assert task['status']=='needs_clarification',task
    assert '没说的我先按' not in task['result']['reply']


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
