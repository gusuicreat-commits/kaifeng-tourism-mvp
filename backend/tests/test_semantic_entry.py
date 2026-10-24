"""Regression for the exact reported utterance: the model must see it first.

Temporary city fixture, fake semantic model and fake hotel API; no live data.
"""
import pytest

from app import language
from app.city import hotels, travel_services
from app.city.agent import provider
from app.db import connect
from test_city_agent import run
from test_city_assistant import city, get, walking


@pytest.mark.parametrize('text', [
    '你能查阅相关酒店资料吗',
    '你能帮我找酒店吗',
    '你能安排行程吗',
    '怎么修改日期？',
])
def test_capability_question_reaches_model_and_never_writes(city, monkeypatch, text):
    walking(city)
    before = get(city)
    calls = []
    monkeypatch.setattr(language, 'available', lambda: True)

    def semantic(system, payload, **kwargs):
        calls.append(payload['text'])
        return {'conversation': {'reply': '可以先帮你查找资料，请告诉我具体想了解什么。'}}, {'source': 'mock_model'}

    monkeypatch.setattr(provider, 'generate', semantic)
    monkeypatch.setattr(hotels, 'search', lambda *_: pytest.fail('capability question must not search'))
    result = run(city, text)
    assert calls == [text]
    assert result['mode'] == 'conversation'
    assert result['status'] == 'answered'
    assert result['metrics']['model_calls'] == 1
    assert result['metrics']['tools'] == 0
    assert get(city) == before
    with connect() as db:
        assert travel_services.read(db, city['trip']['id'])['version'] == 0


# Every former pre-model business branch must see the whole utterance once.
BYPASS_TEXTS = [
    '今天天气怎么样？',
    '可以，按天气调整行程',
    '从开封站到清明上河园怎么走',
    '从开封站出发和返回',
    '下一站去哪？',
    '算了',
    '午饭吃完了',
    '我在博物馆',
    '今天天气怎么样？另外明天入住标间',
]


@pytest.mark.parametrize('text', BYPASS_TEXTS)
def test_r1_former_bypasses_use_whole_message_semantics(city, monkeypatch, text):
    walking(city)
    before = get(city)
    calls = []
    monkeypatch.setattr(language, 'available', lambda: True)
    def semantic(system, payload, **kwargs):
        calls.append(payload['text'])
        return {'conversation': {'reply': '我会先核对你这轮的完整意思。'}}, {'source': 'mock_model'}
    monkeypatch.setattr(provider, 'generate', semantic)
    result = run(city, text)
    assert calls == [text]
    assert result['mode'] == 'conversation' and result['status'] == 'answered'
    assert result['metrics']['model_calls'] == 1 and result['metrics']['tools'] == 0
    assert get(city) == before


@pytest.mark.parametrize('text', BYPASS_TEXTS)
@pytest.mark.parametrize('failure', ['transport', 'invalid_output'])
def test_r1_failed_semantics_never_executes_keyword_partial(city, monkeypatch, text, failure):
    from fastapi import HTTPException
    walking(city)
    before = get(city)
    monkeypatch.setattr(language, 'available', lambda: True)
    def semantic(*args, **kwargs):
        if failure == 'transport':
            raise HTTPException(503, detail='isolated semantic transport failure')
        return {'actions': [{'kind': 'unregistered', 'quote': text}]}, {'source': 'mock_model'}
    monkeypatch.setattr(provider, 'generate', semantic)
    result = run(city, text)
    assert result['status'] == 'needs_clarification'
    assert result['metrics']['model_calls'] == 1 and result['metrics']['tools'] == 0
    assert get(city) == before
    with connect() as db:
        assert travel_services.read(db, city['trip']['id'])['version'] == 0


def test_r1_local_extractor_capacity_does_not_bypass_model(city, monkeypatch):
    text = '，'.join(['下一站去哪'] * 9)
    monkeypatch.setattr(language, 'available', lambda: True)
    calls = []
    def semantic(system, payload, **kwargs):
        calls.append(payload['text'])
        return {'conversation': {'reply': '请告诉我你最想了解哪一项。'}}, {'source': 'mock_model'}
    monkeypatch.setattr(provider, 'generate', semantic)
    assert run(city, text)['mode'] == 'conversation'
    assert calls == [text]


def test_r1_explicit_disable_keeps_offline_path_even_with_configured_model(city, monkeypatch):
    from app.city.agent import understand
    from test_city_agent import snapshot
    snap, _ = snapshot(city)
    monkeypatch.setattr(language, 'available', lambda: True)
    monkeypatch.setattr(provider, 'generate', lambda *args, **kwargs: pytest.fail('explicit offline path called a model'))
    intent, meta = understand.parse('从开封站出发和返回', snap, allow_model=False)
    assert meta['model_calls'] == 0
    assert {f.field: f.value for f in intent.planning.fields} == {'origin': '开封站', 'destination': '开封站'}


def test_r1_typed_cancel_endpoint_needs_no_semantic_call(city, monkeypatch):
    from test_city_agent import start
    from test_city_assistant import path
    monkeypatch.setattr(language, 'available', lambda: True)
    monkeypatch.setattr(provider, 'generate', lambda *args, **kwargs: pytest.fail('typed cancel called a model'))
    task = start(city, '今天天气怎么样？').json()
    response = city['client'].post(path(city, '/agent/tasks/' + task['task_id'] + '/cancel'))
    assert response.status_code == 200 and response.json()['status'] == 'cancelled'
    assert response.json()['metrics']['model_calls'] == 0
