"""Evaluation admission control must bound actual one-attempt provider calls."""
import sys
from pathlib import Path
import pytest
from fastapi import HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from agent_live_diagnostics import ModelCallBudget


def test_request_cap_is_checked_before_provider_and_counts_failures():
    calls=[]
    def model(*a,**kw):calls.append(1);return {'tool':'finish'},{'source':'mock_model'}
    b=ModelCallBudget(model,cap=2,interval=0)
    for _ in range(2):b('json',{'eligible_actions':[]})
    with pytest.raises(RuntimeError):b('json',{})
    assert len(calls)==len(b.calls)==2
    assert [r['sequence'] for r in b.records()]==[1,2]
    assert all(r['source']=='mock_model' for r in b.records())


@pytest.mark.parametrize('status',[401,429])
def test_provider_failure_opens_circuit_and_no_second_attempt(status):
    calls=[]
    def model(*a,**kw):
        calls.append(1);raise HTTPException(503,detail={'code':'MODEL_HTTP_ERROR','upstream_status':status,
            'error_type':'rate_limit_reached_error' if status==429 else 'invalid_authentication_error',
            'retry_after_seconds':60,'http_attempts':1,'automatic_retries':0})
    b=ModelCallBudget(model,cap=12,interval=0)
    with pytest.raises(HTTPException):b('json',{})
    with pytest.raises(RuntimeError):b('json',{})
    assert len(calls)==1 and b.blocked['upstream_status']==status
    assert b.records()[0]['failure']['retry_after_seconds']==60


def test_serial_spacing_is_separate_from_retries():
    clock=[0.0];waits=[]
    def wait(seconds):waits.append(seconds);clock[0]+=seconds
    b=ModelCallBudget(lambda *a,**kw:({},{}),interval=2,clock=lambda:clock[0],sleep=wait)
    b('json',{});b('json',{})
    assert waits==[2] and b.records()[1]['interval_since_previous_start_ms']==2000


def test_local_wait_is_not_a_provider_attempt_or_failed_online_run():
    from app.model_schedule import ModelWaiting
    answers=iter([False,True])
    def model(*a,**kw):
        if not next(answers):raise ModelWaiting({'code':'MODEL_WAITING','http_attempts':0,'retry_after_seconds':25})
        return {},{'source':'mock_model','http_attempts':1}
    budget=ModelCallBudget(model,cap=1,interval=0)
    with pytest.raises(ModelWaiting):budget('JSON',{})
    assert not budget.calls and budget.http_attempt_count==0 and budget.blocked is None
    budget('JSON',{})
    assert len(budget.calls)==budget.http_attempt_count==1
