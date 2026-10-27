"""D4 in-trip changes use synthetic facts and a semantic provider double."""
from datetime import datetime,timedelta
import copy
import pytest
from app import language
from app.city.agent import provider,understand
from app.db import connect
from test_city_assistant import city,planned,walking,get,path,modify,confirm
from test_city_agent import run,empty_tasks


def model(monkeypatch,text,operation,minutes=None,target=None,next_stop=False):
    monkeypatch.setattr(language,'available',lambda:True)
    raw={'state_request':{'operation':operation,'quote':text,'minutes':minutes,'target_name':target,'next_stop':next_stop}}
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))


def active_current(city):
    result=planned(city);activity=result['itinerary']['activities'][0]
    city['clock'][0]=datetime.fromisoformat(activity['starts_at'])+timedelta(minutes=5)
    modify(city,{'operation':'transition','status':'ACTIVE'})
    modify(city,{'operation':'set_anchor','anchor':{'place_id':activity['place_id'],'confirmed_at':city['clock'][0].isoformat()}})
    return activity


@pytest.mark.parametrize('operation,text,minutes',[
    ('delay','排队耗掉了一个小时，重新排后面的安排',60),
    ('delay','路上走了二十五分钟才到，比预期晚了',25),
    ('delay','前头耽搁了半小时，后续按现在重排',30),
    ('rest','我想歇半小时',30),
    ('rest','腿酸了，留一刻钟坐坐',15),
    ('rest','先在这儿休息20分钟再继续',20),
])
def test_elapsed_delay_and_rest_preview_confirm_preserves_history(city,monkeypatch,operation,text,minutes):
    walking(city);before=get(city);saved=city['client'].get(path(city,'/engine/current')).json()
    model(monkeypatch,text,operation,minutes)
    task=run(city,text)
    assert task['status']=='needs_confirmation',task
    result=task['result'];plan=result['planning']['result']['itineraries'][0]
    assert result['confirmation']['target_kind']=='route' and get(city)==before
    assert city['client'].get(path(city,'/engine/current')).json()==saved
    assert plan['completed_prefix'][0]==before['draft']['stops'][0]
    assert next(a for a in plan['activities'] if a['goal_id']=='show')['session_id']=='s2'
    if operation=='rest':
        rest=next(a for a in plan['activities'] if a['kind']=='rest')
        assert rest['duration_seconds']==minutes*60 and rest['place_id']=='museum'
        assert plan['activities'][0]==rest and '未指定或核实座椅' in result['reply']
    else:assert plan['legs'][0]['departs_at']=='2030-05-01T09:42:00+08:00'
    assert '预计' in result['reply'] and '结束地点' in result['reply']
    response=confirm(city,result);assert response.status_code==200,response.text
    assert get(city)['route_version']==2 and get(city)['draft']['stops'][0]==before['draft']['stops'][0]


@pytest.mark.parametrize('text,minutes',[
    ('这儿还没逛够，再加二十分钟',20),
    ('我想在当前站多留半小时',30),
    ('把此处停留延长一刻钟',15),
])
def test_extend_current_station_uses_original_remainder(city,monkeypatch,text,minutes):
    activity=active_current(city);before=get(city)
    model(monkeypatch,text,'extend',minutes)
    task=run(city,text);assert task['status']=='needs_confirmation',task
    plan=task['result']['planning']['result']['itineraries'][0]
    ongoing=plan['ongoing'];expected=datetime.fromisoformat(activity['departs_at'])+timedelta(minutes=minutes)
    assert datetime.fromisoformat(ongoing['ends_at'])==expected and ongoing['stop_id']==activity['goal_id']
    assert activity['goal_id'] not in {a['goal_id'] for a in plan['activities']}
    assert activity['goal_id'] not in task['result']['changes']['removed']
    assert get(city)==before
    response=confirm(city,task['result']);assert response.status_code==200,response.text
    assert get(city)['route_version']==2 and get(city)['draft']['stops'][0]['progress']=='pending'


@pytest.mark.parametrize('text,target',[
    ('下一站不去了，后面重新排',None),
    ('[合成测试]restaurant这站省掉吧','[合成测试]restaurant'),
    ('请取消去[合成测试]restaurant的安排','[合成测试]restaurant'),
])
def test_skip_and_replan_is_one_route_confirmation(city,monkeypatch,text,target):
    walking(city);before=get(city);model(monkeypatch,text,'skip_replan',target=target,next_stop=target is None)
    task=run(city,text);assert task['status']=='needs_confirmation',task
    result=task['result'];plan=result['planning']['result']['itineraries'][0]
    assert 'lunch' not in {a['goal_id'] for a in plan['activities']} and 'lunch' in result['changes']['removed']
    assert get(city)==before
    response=confirm(city,result);assert response.status_code==200,response.text
    after=get(city);assert after['route_version']==2
    assert next(s for s in after['draft']['stops'] if s['stop_id']=='lunch')['progress']=='skipped'
    assert after['draft']['stops'][0]==before['draft']['stops'][0]


def test_skip_locked_future_station_explicitly_warns_and_waits(city,monkeypatch):
    walking(city);before=get(city);text='[合成测试]stage2这站放弃，剩下的重排'
    model(monkeypatch,text,'skip_replan',target='[合成测试]stage2')
    task=run(city,text);assert task['status']=='needs_confirmation',task
    assert '必去项目' in task['result']['reply'] and '确认后才生效' in task['result']['reply']
    assert get(city)==before
    response=confirm(city,task['result']);assert response.status_code==200,response.text
    assert get(city)['route_version']==2
    assert next(s for s in get(city)['draft']['stops'] if s['stop_id']=='show')['progress']=='skipped'


@pytest.mark.parametrize('operation,text,minutes,target',[
    ('rest','累了，歇一会',None,None),
    ('extend','这里还想再玩玩',None,None),
    ('delay','堵车耽误了',None,None),
    ('delay','刚才排队没排多久',None,None),
    ('delay','前面只用十五分钟',60,None),
    ('rest','歇半小时',20,None),
    ('skip_replan','取消去不存在的古城',None,'不存在的古城'),
    ('skip_replan','跳过这站',None,None),
])
def test_unverifiable_changes_ask_without_candidate(city,monkeypatch,operation,text,minutes,target):
    walking(city);before=get(city);model(monkeypatch,text,operation,minutes,target)
    task=run(city,text)
    assert task['status']=='needs_clarification' and not task['result'].get('confirmation'),task
    assert get(city)==before
    with connect() as db:assert db.execute('SELECT count(*) FROM city_engine_candidates WHERE consumed=0').fetchone()[0]==0


def test_delay_cannot_silently_keep_an_already_started_show(city,monkeypatch):
    walking(city);city['clock'][0]=datetime.fromisoformat('2030-05-01T15:05:00+08:00')
    modify(city,{'operation':'set_anchor','anchor':{'place_id':'museum','confirmed_at':city['clock'][0].isoformat()}})
    before=get(city);text='前面耽搁了一个小时，按现在重排'
    model(monkeypatch,text,'delay',60);task=run(city,text)
    assert not task['result'].get('confirmation') and '已赶不上' in task['result']['reply'],task
    assert '查询下一场' in task['result']['reply'] and '明确放弃' in task['result']['reply']
    assert get(city)==before


def test_rest_after_ongoing_station_is_inserted_after_preserved_remainder(city,monkeypatch):
    activity=active_current(city);before=get(city);text='这里结束后歇半小时'
    model(monkeypatch,text,'rest',30);task=run(city,text)
    assert task['status']=='needs_confirmation',task
    plan=task['result']['planning']['result']['itineraries'][0];rest=plan['activities'][0]
    assert plan['ongoing']['stop_id']==activity['goal_id']
    assert rest['kind']=='rest' and rest['starts_at']==activity['departs_at']
    assert activity['goal_id'] not in task['result']['changes']['removed']
    assert get(city)==before


@pytest.mark.parametrize('text,minutes',[
    ('半个小时',30),('一个小时',60),('一个半小时',90),('两个半小时',150),
    ('三小时半',210),('1.5小时',90),('一小时三十分钟',90),('2小时又15分钟',135),
    ('两刻钟',30),('十五分钟',15),('18分',18),('一百二十分钟',120),
])
def test_duration_grammar_checks_values_without_sentence_matching(text,minutes):
    assert understand.duration_values(text)==(minutes,)


@pytest.mark.parametrize('text',['一点半','没排多久','-30分钟','负十五分钟'])
def test_no_clock_or_negative_duration_becomes_positive_duration(text):
    assert understand.duration_values(text)==()


@pytest.mark.parametrize('text',['接下来那个地方我不想去了','后头第一站直接略过吧'])
def test_relative_next_reference_works_while_current_station_is_preserved(city,monkeypatch,text):
    activity=active_current(city);before=get(city)
    model(monkeypatch,text,'skip_replan',next_stop=True);task=run(city,text)
    assert task['status']=='needs_confirmation',task
    plan=task['result']['planning']['result']['itineraries'][0]
    assert plan['ongoing']['stop_id']==activity['goal_id'] and 'lunch' in task['result']['changes']['removed']
    assert get(city)==before


@pytest.mark.parametrize('operation',['rest','delay','extend'])
def test_changes_do_not_run_before_departure(city,monkeypatch,operation):
    planned(city);before=get(city);text='请调整半小时'
    model(monkeypatch,text,operation,30);task=run(city,text)
    assert task['status']=='needs_clarification' and '先开始' in task['result']['reply'],task
    assert get(city)==before


@pytest.mark.parametrize('skipped',['unknown','visit','show'])
def test_engine_omission_cannot_skip_history_unknown_or_retained_goal(city,skipped):
    from test_city_agent import snapshot
    from app.city.assistant.tools import requirements
    walking(city);before=get(city);snap,_=snapshot(city)
    req=requirements(snap)
    response=city['client'].post(path(city,'/engine/replan-preview'),json={
        'request_id':__import__('uuid').uuid4().hex,'expected_version':before['version'],
        'requirements':req.model_dump(mode='json'),'skipped_goal_ids':[skipped]})
    assert response.status_code==422,response.text
    assert get(city)==before


def test_negated_elapsed_report_is_conversation_without_replan(city,monkeypatch):
    walking(city);before=get(city);text='刚才排队没排多久'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'conversation':{'reply':'好的，需要调整时告诉我。'}},{'source':'mock_model'}))
    task=run(city,text);assert task['status']=='answered' and task['metrics']['tools']==0
    assert get(city)==before


def raw_model(monkeypatch,raw):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))


@pytest.mark.parametrize('raw_of',[
    # Live model outputs (2026-10-27): a cancel action, or a progress skip, carrying the catalog name.
    lambda text:{'actions':[{'kind':'cancel','quote':text,'target_name':'[合成测试]restaurant'}]},
    lambda text:{'state_request':{'operation':'skip_replan','quote':text,'target_name':'[合成测试]restaurant'}},
])
def test_a_short_name_for_one_remaining_stop_becomes_the_skip_preview(city,monkeypatch,raw_of):
    walking(city);before=get(city);text='restaurant就不去了'
    raw_model(monkeypatch,raw_of(text));task=run(city,text)
    assert task['status']=='needs_confirmation',task
    assert 'lunch' in task['result']['changes']['removed'] and get(city)==before


def test_next_stop_wins_over_a_copied_name_absent_from_the_words(city,monkeypatch):
    walking(city);before=get(city);text='后面那家不吃了，直接去下一个'
    raw_model(monkeypatch,{'state_request':{'operation':'skip','quote':text,'target_name':'[合成测试]restaurant','next_stop':True}})
    task=run(city,text);assert task['status']=='needs_confirmation',task
    assert 'lunch' in task['result']['changes']['removed'] and get(city)==before


def test_a_shared_fragment_naming_several_stops_is_not_evidence(city,monkeypatch):
    walking(city);before=get(city);text='[合成测试]那站就不去了'
    raw_model(monkeypatch,{'actions':[{'kind':'cancel','quote':text,'target_name':'[合成测试]restaurant'}]})
    task=run(city,text)
    assert task['status']!='needs_confirmation' and get(city)==before,task
