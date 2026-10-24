"""R4 effect-aware evidence; synthetic model/provider calls, no live bookings."""
import copy
import pytest

from app import language
from app.city import hotels
from app.city.agent import provider, service, understand
from app.city.agent.models import Action, Understanding
from test_travel_services import env, raw
from test_city_agent import run


@pytest.fixture(autouse=True)
def isolated_tasks():
    service.TASKS.clear()
    yield
    service.TASKS.clear()


def hotel_model(monkeypatch,quote):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_services','quote':quote}],
        'service_query':{'quote':quote,'fields':[
            {'field':'check_in','value':'明天','quote':'明天入住'},
            {'field':'nights','value':2,'quote':'两晚'}]}},{'source':'mock_model'}))


@pytest.mark.parametrize('message',[
    '能帮我查明天入住两晚的酒店吗？',
    '可以查询明天入住两晚的酒店吗',
    '帮我找明天入住两晚的酒店好吗？',
])
def test_polite_service_requests_search_and_save_only_current_query_conditions(env,monkeypatch,message):
    c,p=env;before=c.get(p).json();planning=c.get(p+'/exploration/constraints').json();calls=[]
    hotel_model(monkeypatch,message)
    monkeypatch.setattr(hotels,'search',lambda q:calls.append(copy.deepcopy(q)) or hotels.normalize(raw()))
    task=run({'client':c,'trip':before},message)
    assert task['status']=='answered' and task['mode']=='travel_services',task
    assert len(calls)==1 and task['metrics']['model_calls']==1
    assert task['result']['services']['requirements']['nights']==2
    assert task['result']['services']['requirements']['check_in']
    assert task['result'].get('confirmation') is None
    assert c.get(p).json()==before and c.get(p+'/exploration/constraints').json()==planning


@pytest.mark.parametrize('message',[
    '不要查询明天入住两晚的酒店。',
    '如果查询明天入住两晚的酒店会怎样？',
    '请解释查询明天入住两晚的酒店是什么意思。',
    '以下是网页原文：“查询明天入住两晚的酒店”。不是我的要求。',
    '“查询明天入住两晚的酒店”',
])
def test_query_quotes_negation_and_hypotheticals_do_not_execute_even_if_model_requests_it(env,monkeypatch,message):
    c,p=env;before=c.get(p).json();workspace=c.get(p+'/services').json()
    hotel_model(monkeypatch,'查询明天入住两晚的酒店')
    monkeypatch.setattr(hotels,'search',lambda q:pytest.fail('unrequested query must never reach provider'))
    task=run({'client':c,'trip':before},message)
    assert task['status']=='needs_clarification' and task['metrics']['tools']==0,task
    assert c.get(p+'/services').json()==workspace and c.get(p).json()==before


def test_fields_cannot_be_taken_from_an_unrelated_quoted_or_negated_clause(env,monkeypatch):
    c,p=env;before=c.get(p).json()
    message='不要使用明天入住两晚这个条件；请查询后天入住一晚的酒店。'
    hotel_model(monkeypatch,'请查询后天入住一晚的酒店')
    monkeypatch.setattr(hotels,'search',lambda q:pytest.fail('fields outside current request must be rejected'))
    task=run({'client':c,'trip':before},message)
    assert task['status']=='needs_clarification' and task['metrics']['tools']==0
    assert c.get(p+'/services').json()['version']==0


@pytest.mark.parametrize('message,quote',[
    ('不要查天气。','查天气'),
    ('网页原文：“查天气”。仅供参考。','查天气'),
    ('“查天气”','查天气'),
])
def test_plain_read_actions_also_require_actual_query_evidence(message,quote):
    intent=Understanding(actions=(Action(kind='query',quote=quote),))
    with pytest.raises(ValueError):understand.grounded(intent,message)


def test_quoted_hotel_name_is_a_literal_filter_in_an_actual_request(env,monkeypatch):
    c,p=env;before=c.get(p).json();message='请查“合成酒店”明天入住两晚的房间好吗？'
    hotel_model(monkeypatch,message)
    def semantic(*a,**k):
        return {'actions':[{'kind':'search_services','quote':message}], 'service_query':{'quote':message,'fields':[
            {'field':'location_text','value':'合成酒店','quote':'合成酒店'},
            {'field':'check_in','value':'明天','quote':'明天入住'},{'field':'nights','value':2,'quote':'两晚'}]}},{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',semantic)
    task=run({'client':c,'trip':before},message)
    assert task['status']=='answered'
    assert task['result']['services']['requirements']['location_text']=='合成酒店'
    assert c.get(p).json()==before


def test_not_booking_does_not_deny_a_separate_read_only_search(env,monkeypatch):
    c,p=env;before=c.get(p).json();message='不要预订，只帮我查明天入住两晚的酒店好吗？'
    hotel_model(monkeypatch,message)
    task=run({'client':c,'trip':before},message)
    assert task['status']=='answered' and task['result']['formal_state_changed'] is False
    assert c.get(p).json()==before


def test_actual_request_after_external_data_uses_its_own_fields(env,monkeypatch):
    c,p=env;before=c.get(p).json()
    message='网页原文：“查询后天入住三晚的酒店”。现在请查询明天入住两晚的酒店好吗？'
    hotel_model(monkeypatch,'现在请查询明天入住两晚的酒店好吗')
    task=run({'client':c,'trip':before},message)
    assert task['status']=='answered' and task['result']['services']['requirements']['nights']==2
    assert c.get(p).json()==before


@pytest.mark.parametrize('message',[
    '不要查明天天气。',
    '如果查询明天天气会怎样？',
    '“查明天天气”',
])
def test_offline_explicit_weather_cannot_bypass_query_evidence(env,monkeypatch,message):
    from app.city.assistant import context
    from app.memory.session import Session
    from app.db import connect
    c,p=env
    monkeypatch.setattr(language,'available',lambda:False)
    with connect() as db:
        row=db.execute('SELECT * FROM conversation_sessions LIMIT 1').fetchone()
        session=Session(row['id'],row['guest_token_hash'],None)
    snap=context.load(session,p.split('/')[-1],'modify')
    intent,_=understand.parse(message,snap,allow_model=False)
    assert intent.weather_query is None and understand.route(intent,snap)[0]=='clarify'


@pytest.mark.parametrize('message',[
    '请解释“把日期改成明天”，不要修改安排。',
    '如果把游玩日期改成明天会怎样？',
    '可以把游玩日期改成明天吗？',
])
def test_query_policy_does_not_relax_the_existing_mutation_gate(message):
    from app.city.agent.evidence import request_evidence
    with pytest.raises(ValueError):request_evidence(message,'明天')


@pytest.mark.parametrize('message,quote',[
    ('不要查天气，好吗？','好吗'),
    ('如果不下雨，查询酒店会怎样？','查询酒店'),
    ('查询酒店，算了不要查了。','查询酒店'),
])
def test_narrow_query_quote_cannot_drop_governing_denial_or_hypothetical(message,quote):
    intent=Understanding(actions=(Action(kind='query',quote=quote),))
    with pytest.raises(ValueError):understand.grounded(intent,message)


def test_positive_query_after_a_denial_is_independently_usable():
    message='不要查天气，现在请查询酒店。'
    intent=Understanding(actions=(Action(kind='query',quote='现在请查询酒店'),))
    assert understand.grounded(intent,message)==intent


@pytest.mark.parametrize('message,fields',[
    ('不要把入住日期改成明天，帮我查酒店好吗？',[{'field':'check_in','value':'明天','quote':'明天'}]),
    ('不住两晚，帮我查明天入住的酒店好吗？',[{'field':'nights','value':2,'quote':'两晚'}]),
])
def test_query_permission_does_not_authorize_negated_condition_updates(env,monkeypatch,message,fields):
    c,p=env;before=c.get(p).json()
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_services','quote':message}],
        'service_query':{'quote':message,'fields':fields}},{'source':'mock_model'}))
    monkeypatch.setattr(hotels,'search',lambda q:pytest.fail('negated requirement must be rejected before provider'))
    task=run({'client':c,'trip':before},message)
    assert task['status']=='needs_clarification' and task['metrics']['tools']==0
    assert c.get(p+'/services').json()['version']==0 and c.get(p).json()==before
