"""Full system/schema/payload accounting and safe failure before providers."""
import copy,json
import pytest
from fastapi import HTTPException
from app import language
from app.city.agent import conversation,understand,provider
from test_city_assistant import city, planned, get
from test_city_agent import snapshot,run


def test_large_projection_keeps_current_turn_and_hard_conditions(city):
    planned(city);snap,_=snapshot(city)
    snap.services.update(version=5,requirements={'category':'hotels','nights':2,'room_type':'标间',
        'stay_budget_fen':50000,'attributes':{'window':{'strength':'hard','value':True}}})
    snap.envelope=snap.envelope.model_copy(update={'recent_messages':[{'role':'assistant','text':'旧消息'*1000} for _ in range(8)]})
    original=copy.deepcopy(snap.envelope.model_dump())
    system='系统规则'*1100
    payload=conversation.model_payload('明天入住',snap,system)
    assert payload['text']=='明天入住'
    assert payload['context']['travel_service_workspace']['requirements']['attributes']['window']=={'value':True,'strength':'hard'}
    assert payload['context']['requirements']['locked_stop_ids']==original['requirements']['locked_stop_ids']
    assert len(system)+len(json.dumps(payload,ensure_ascii=False))<=12000
    assert payload['context']['input_budget']['characters']==len(system)+len(json.dumps(payload,ensure_ascii=False))
    assert 'oldest_messages' in payload['context']['input_budget']['trimmed']
    assert snap.envelope.model_dump()==original


def test_irreducible_context_exposes_sanitized_reason_without_dropping_requirements(city):
    snap,_=snapshot(city)
    snap.services['requirements']={'category':'hotels','room_type':'保留条件'*3000}
    with pytest.raises(HTTPException) as error:conversation.model_payload('当前输入',snap,'规则')
    detail=error.value.detail
    assert detail['code']=='AGENT_CONTEXT_BUDGET' and detail['http_attempts']==0
    assert '保留条件' not in json.dumps(detail,ensure_ascii=False)
    assert len(snap.services['requirements']['room_type'])==12000


def test_task_budget_failure_is_not_business_or_semantic_failure(city,monkeypatch):
    before=get(city)
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:pytest.fail('budget must stop before model'))
    real=conversation.model_payload
    monkeypatch.setattr(conversation,'model_payload',lambda text,snap,system:real(text,snap,system+'规则'*7000))
    task=run(city,'你好')
    assert task['status']=='needs_clarification' and task['result']['code']=='AGENT_CONTEXT_BUDGET'
    assert task['metrics']['model_calls']==0 and task['metrics']['tools']==0
    assert task['events'][0]['status']=='blocked_before_request'
    assert get(city)==before


@pytest.mark.parametrize('repeats,over_budget',[(100,False),(285,True)])
def test_real_schema_and_maximum_turn_count_in_the_same_budget(city,monkeypatch,repeats,over_budget):
    planned(city)
    monkeypatch.setattr(language,'available',lambda:True)
    calls=[]
    def generate(system,payload,**kwargs):
        calls.append(len(system)+len(json.dumps(payload,ensure_ascii=False)))
        assert calls[-1]<=12000
        return {'conversation':{'reply':'请告诉我你希望了解的具体内容。'}},{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',generate)
    task=run(city,'想聊聊旅行。'*repeats+'你好')
    if over_budget:
        assert not calls and task['result']['code']=='AGENT_CONTEXT_BUDGET'
        budget=task['result']['context_budget']
        assert budget['required_characters']>budget['limit_characters']==12000
        assert task['metrics']['model_calls']==0
        return
    assert calls and task['status']=='answered',json.dumps(task,ensure_ascii=False)
    assert task['events'][0]['context_budget']['characters']==calls[0]
