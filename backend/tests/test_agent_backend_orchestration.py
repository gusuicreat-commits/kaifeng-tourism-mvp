"""Natural language -> existing backend workflow -> preview -> explicit confirmation."""
import json
import pytest
from app import language
from app.city import exploration
from app.city.agent import provider,service
from app.db import connect
from test_city_assistant import city,get,path,rid
from test_city_agent import run,start,step
from test_planning_understanding import model,field


def prepare(city,monkeypatch):
    c=city['client']
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum','priority':'must'}}).status_code==200
    message='2030-05-01上午9点到晚上8点，从[合成测试]origin出发，返回[合成测试]hotel'
    fields=[field('date','2030-05-01','2030-05-01'),field('start_time','09:00','上午9点'),field('end_time','20:00','晚上8点'),
            field('origin','[合成测试]origin','[合成测试]origin'),field('destination','[合成测试]hotel','[合成测试]hotel')]
    model(monkeypatch,message,fields)
    return message


def test_agent_alone_saves_constraints_previews_and_only_explicit_confirm_submits(city,monkeypatch):
    message=prepare(city,monkeypatch);before=get(city)
    task=run(city,message)
    assert task['status']=='answered' and task['mode']=='planning',task
    receipt=task['result']['planning_workflow'];r=receipt['result']
    assert receipt['execution']=='completed_backend' and r['status']=='feasible'
    assert 'planning_action' not in task['result']
    saved=city['client'].get(path(city,'/exploration/constraints')).json()
    assert saved['version']==r['planning_version']==1 and saved['result']==r
    assert get(city)==before
    assert task['events'][-1]['tool']=='planning_workflow' and task['metrics']['tools']==1
    plan=r['planning']
    response=city['client'].post(path(city,'/engine/confirm'),json={'request_id':rid(),
        'expected_version':plan['base_version'],'candidate_id':plan['candidate_id'],'alternative_index':0})
    assert response.status_code==200,response.text
    assert get(city)['route_version']==before['route_version']+1


def test_duplicate_step_does_not_run_planner_twice(city,monkeypatch):
    message=prepare(city,monkeypatch)
    task=step(city,start(city,message).json()).json()
    body={'request_id':rid(),'expected_step':task['step']}
    endpoint=path(city,'/agent/tasks/'+task['task_id']+'/step')
    first=city['client'].post(endpoint,json=body).json()
    second=city['client'].post(endpoint,json=body).json()
    assert first['result']==second['result']
    assert city['client'].get(path(city,'/exploration/constraints')).json()['version']==1


@pytest.mark.parametrize('interruption',['cancel','services'])
def test_late_engine_candidate_is_consumed_and_workspace_not_published(city,monkeypatch,interruption):
    from app.city import travel_services
    message=prepare(city,monkeypatch);original=exploration.engine.preview;candidates=[]
    def interrupted(*a,**k):
        result=original(*a,**k);candidates.append(result['candidate_id'])
        task=next(t for t in service.TASKS.values() if t.trip_id==city['trip']['id'])
        if interruption=='cancel':
            assert city['client'].post(path(city,'/agent/tasks/'+task.id+'/cancel')).status_code==200
        else:
            with connect() as db:
                w=travel_services.read(db,task.trip_id);w.update(version=1,requirements={'category':'hotels','nights':3})
                travel_services.write(db,task.trip_id,w)
        return result
    monkeypatch.setattr(exploration.engine,'preview',interrupted)
    task=run(city,message)
    assert task['status']==('cancelled' if interruption=='cancel' else 'stale'),task
    assert city['client'].get(path(city,'/exploration/constraints')).json()['version']==0
    assert candidates and candidates[0]
    with connect() as db:assert db.execute('SELECT consumed FROM city_engine_candidates WHERE id=?',(candidates[0],)).fetchone()[0]==1
    response=city['client'].post(path(city,'/engine/confirm'),json={'request_id':rid(),'expected_version':1,'candidate_id':candidates[0]})
    assert response.status_code==409 and get(city)['route_version']==0


@pytest.mark.parametrize('message,value,accepted',[
    ('我明天想去开封玩','2030-05-02',True),
    ('我明天想去开封玩','2030-05-03',False),
    ('不要把日期改成明天','2030-05-02',False),
])
def test_live_shaped_redundant_index_and_normalized_date_still_need_evidence(city,monkeypatch,message,value,accepted):
    model(monkeypatch,message,[field('date',value,'明天')],
        actions=[{'kind':'query','quote':message,'purpose':'trip'}],conversation={'reply':'请补充游玩时间。'})
    task=run(city,message)
    saved=city['client'].get(path(city,'/exploration/constraints')).json()
    assert saved['version']==int(accepted)
    if accepted:
        assert saved['constraints']['business_date']==value
        assert task['result']['planning_workflow']['execution']=='completed_backend'
    assert get(city)['route_version']==0
