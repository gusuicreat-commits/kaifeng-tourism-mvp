"""Online semantic data reaches existing executors without a second intent parser."""
import pytest
from app import language
from app.city.agent import understand, provider, service
from app.city.agent.models import Understanding
from app.city import travel_services
from test_city_assistant import city, planned, walking, get
from test_city_agent import run


def online(monkeypatch, raw):
    monkeypatch.setattr(language,'available',lambda:True)
    # The pre-model safety extractor is independent of the executor under test.
    monkeypatch.setattr(understand,'rules',lambda *a:Understanding())
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(raw,{'source':'mock_model'}))
    def forbidden(*a,**k):raise AssertionError('legacy text parser reached after semantic understanding')
    monkeypatch.setattr(service.interpret,'rules',forbidden)
    monkeypatch.setattr(travel_services,'parse',forbidden)


@pytest.mark.parametrize('message,purpose,key',[
    ('还有哪些安排没处理呢','trip','remaining'),
    ('接下来该往何处去','next','next_stop'),
    ('那场表演的开场时刻呢','shows','facts'),
])
def test_typed_query_executes_without_legacy_intent_parser(city,monkeypatch,message,purpose,key):
    planned(city);before=get(city)
    online(monkeypatch,{'actions':[{'kind':'query','quote':message,'purpose':purpose}]})
    task=run(city,message)
    assert key in task['result'],task
    assert get(city)==before


def test_missing_query_purpose_clarifies_without_reparsing(city,monkeypatch):
    planned(city)
    online(monkeypatch,{'actions':[{'kind':'query','quote':'下一站去哪？'}]})
    task=run(city,'下一站去哪？')
    assert task['status']=='needs_clarification' and 'next_stop' not in task['result']


def test_action_only_hotel_does_not_recover_fields_from_original_text(city,monkeypatch):
    message='查酒店，明天入住，住两晚'
    online(monkeypatch,{'actions':[{'kind':'search_services','quote':message}]})
    task=run(city,message)
    req=task['result']['services']['requirements']
    assert 'check_in' not in req and 'nights' not in req
    assert not task['result']['services']['result']['items']


def test_typed_state_keeps_original_preview_confirmation(city,monkeypatch):
    walking(city);before=get(city)
    message='跳过测试餐厅'
    # Use a factual current pending stop name, not a model-supplied ID.
    from test_city_agent import snapshot
    snap,_=snapshot(city)
    from app.city.assistant.context import label
    name=label(snap.planning.facts,next(s.place_id for s in snap.draft.stops if s.progress=='pending'))
    message='跳过'+name
    online(monkeypatch,{'actions':[{'kind':'progress','quote':message}],
        'state_request':{'operation':'skip','quote':message,'target_name':name}})
    task=run(city,message)
    assert task['status']=='needs_confirmation' and task['result']['confirmation']['target_kind']=='state',task
    assert get(city)==before


def test_missing_state_target_is_not_arbitrarily_next_stop(city,monkeypatch):
    walking(city);before=get(city);message='跳过这站'
    online(monkeypatch,{'actions':[{'kind':'progress','quote':message}],
        'state_request':{'operation':'skip','quote':message}})
    task=run(city,message)
    assert task['status']=='needs_clarification' and not task['result'].get('confirmation')
    assert get(city)==before
