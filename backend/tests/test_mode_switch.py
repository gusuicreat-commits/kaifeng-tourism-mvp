"""Mode changes use grounded synthetic model fields and existing planning workflows."""
import pytest
from app import language
from test_city_assistant import city
from test_city_agent import snapshot,empty_tasks
from test_city_engine_api import engine
from test_quick_mode import reference_city,_run,saved
from test_preference_dialogue import prepared,run,workflow,workspace
from test_planning_understanding import model,field


@pytest.fixture(autouse=True)
def available(monkeypatch):
    monkeypatch.setattr(language,'available',lambda:True)


def switch(e,monkeypatch,text,mode,requested,**extra):
    return _run(e,text,mode,{'interaction_mode':requested,'mode_quote':text,**extra},monkeypatch)


def test_quick_switch_fills_missing_conditions_and_previews(reference_city,monkeypatch):
    task=switch(reference_city,monkeypatch,'省去沟通环节，请替我决定安排','deep','quick')
    assert task['result']['interaction_mode']=='quick'
    assert task['result']['reply'].startswith('已切换为快速安排：不再追问，缺的条件按默认补上。')
    assert workflow(task)['status']=='feasible',task
    assert saved(reference_city)['starts_at'].endswith('T09:00:00+08:00')
    assert not saved(reference_city).get('preferences_pending')


def test_pending_preferences_quick_switch_previews(city,monkeypatch):
    prepared(city);assert run(city,monkeypatch)['status']=='needs_clarification'
    assert saved(city)['preferences_pending']
    task=switch(city,monkeypatch,'不必征求我的偏好了，你决定吧','deep','quick')
    assert task['result']['interaction_mode']=='quick'
    assert workflow(task)['status']=='feasible',task
    assert not saved(city)['preferences_pending']
    assert '开始排之前' not in task['result']['reply']
    assert '没有听出具体偏好' not in task['result']['reply']


def test_deep_switch_asks_preferences_and_following_turn_previews(city,monkeypatch):
    prepared(city)
    task=switch(city,monkeypatch,'先聊透我的想法再制定行程','quick','deep')
    assert task['result']['interaction_mode']=='deep'
    assert task['result']['reply'].startswith('已切换为深度互动：我会先问清楚再排。')
    assert '能吃辣吗' in task['result']['reply'] and saved(city)['preferences_pending']
    follow=run(city,monkeypatch,'都行',[field('spice','any','都行')],mode='deep')
    assert workflow(follow)['status']=='feasible'
    assert follow['result']['interaction_mode']=='deep'


def test_deep_switch_with_gaps_asks_missing_condition(reference_city,monkeypatch):
    task=switch(reference_city,monkeypatch,'先和我仔细商量','quick','deep')
    assert task['result']['interaction_mode']=='deep'
    assert '哪天' in task['result']['reply']
    assert not saved(reference_city).get('starts_at')


def test_bad_quote_drops_only_mode_and_keeps_other_conditions(city,monkeypatch):
    prepared(city)
    task=_run(city,'安排轻松点','deep',{'interaction_mode':'quick','mode_quote':'凭空编造的要求',
        'planning':{'operation':'update','quote':'安排轻松点','fields':[field('pace','relaxed','轻松点')]}},monkeypatch)
    assert task['result']['interaction_mode']=='deep'
    assert saved(city)['pace']=='relaxed'
    assert '能吃辣吗' in task['result']['reply']
    assert '互动模式要求未能核对' in task['result']['reply']
    assert any(d['path']=='interaction_mode' for d in task['interpretation']['dropped'])


@pytest.mark.parametrize('mode',['quick','deep'])
def test_absent_mode_uses_request_mode(city,monkeypatch,mode):
    prepared(city)
    task=run(city,monkeypatch,mode=mode)
    assert task['result']['interaction_mode']==mode
    assert '已切换为' not in task['result']['reply']
    assert (task['status']=='needs_clarification')==(mode=='deep')


def test_switch_and_time_change_are_applied_together(city,monkeypatch):
    prepared(city)
    text='先详细商量，上午十点再出发'
    task=switch(city,monkeypatch,text,'quick','deep',planning={
        'operation':'update','quote':text,'fields':[field('start_time','10:00','上午十点')]})
    assert saved(city)['starts_at'].endswith('T10:00:00+08:00')
    assert task['result']['interaction_mode']=='deep' and '能吃辣吗' in task['result']['reply']


@pytest.mark.parametrize('location',['planning','conversation','actions'])
def test_misplaced_mode_and_nonstandard_enum_are_recovered(city,monkeypatch,location):
    prepared(city)
    text='请直接决定，省去提问'
    mode={'interaction_mode':' QUICK ','mode_quote':text}
    raw={location:({'operation':'continue','quote':text,**mode} if location=='planning' else
        {'reply':'收到',**mode} if location=='conversation' else [{'kind':'modify','quote':text,**mode}])}
    task=_run(city,text,'deep',raw,monkeypatch)
    assert workflow(task)['status']=='feasible',task
    assert task['result']['interaction_mode']=='quick'
    assert task['interpretation']['relocated']


@pytest.mark.parametrize('mode,quote',[('turbo','安排轻松点'),(['quick'],'安排轻松点'),('quick',None)])
def test_malformed_mode_only_drops_that_item(city,monkeypatch,mode,quote):
    prepared(city)
    task=_run(city,'安排轻松点','deep',{'interaction_mode':mode,'mode_quote':quote,
        'planning':{'operation':'update','quote':'安排轻松点','fields':[field('pace','relaxed','轻松点')]}},monkeypatch)
    assert task['result']['interaction_mode']=='deep' and saved(city)['pace']=='relaxed'
    assert '互动模式要求未能核对' in task['result']['reply']


def test_quoted_mode_does_not_authorize_switch(city,monkeypatch):
    prepared(city)
    task=_run(city,'他说“直接替我安排”是什么意思','deep',{'interaction_mode':'quick','mode_quote':'直接替我安排',
        'conversation':{'reply':'这是希望减少提问的意思。'}},monkeypatch)
    assert task['result']['interaction_mode']=='deep'
    assert '已切换为' not in task['result']['reply']


def test_interaction_mode_changed_flag(city,monkeypatch):
    prepared(city)
    stayed=run(city,monkeypatch)
    assert stayed['result']['interaction_mode_changed'] is False
    switched=switch(city,monkeypatch,'省去沟通环节，请替我决定安排','deep','quick')
    assert switched['result']['interaction_mode_changed'] is True
