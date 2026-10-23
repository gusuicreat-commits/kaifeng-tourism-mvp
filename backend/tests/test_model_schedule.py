"""Fake clocks and MockTransport only; no paid requests or production state."""
import json
import os
import threading
from datetime import datetime,timedelta,timezone
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from app import language,model_schedule
from app.city.agent import provider,service
from test_city_assistant import city,walking,get,modify,rid,path,confirm
from test_city_agent import start,step


class Clock:
    def __init__(self):self.value=0
    def __call__(self):return self.value
    def wall(self):return datetime(2026,10,22,tzinfo=timezone.utc)+timedelta(seconds=self.value)
    def advance(self,seconds=25):self.value+=seconds


@pytest.fixture
def gate(monkeypatch):
    clock=Clock();g=model_schedule.ModelScheduler(clock=clock,wall=clock.wall)
    monkeypatch.setattr(model_schedule,'_scheduler',g)
    return g,clock


def test_three_rpm_has_no_burst_and_never_sleeps(gate):
    g,c=gate
    for t in (0,25,50,75):
        c.value=t
        with g.permit(10):pass
        with pytest.raises(model_schedule.ModelWaiting) as e:
            with g.permit(10):pytest.fail('burst admitted')
        assert e.value.detail['retry_after_seconds']==25
        assert e.value.detail['http_attempts']==0
    assert g.attempts==4 and g.describe()['organization_global'] is False


def test_concurrent_request_cannot_get_second_permit(gate):
    g,c=gate;ready=threading.Event();release=threading.Event()
    def hold():
        with g.permit(5):ready.set();assert release.wait(5)
    thread=threading.Thread(target=hold);thread.start();assert ready.wait(5)
    c.advance(100)
    with pytest.raises(model_schedule.ModelWaiting) as e:
        with g.permit(5):pytest.fail('concurrent call')
    assert e.value.detail['reason']=='concurrency'
    release.set();thread.join(5);assert not thread.is_alive()
    assert not g.inflight and g.attempts==1


@pytest.mark.parametrize('kind',['rate_limit_reached_error','engine_overloaded_error','unknown_error'])
def test_429_cooldown_honors_retry_after_and_never_retries(gate,kind):
    g,c=gate
    with g.permit(10) as p:g.observe(p,httpx.Response(429,headers={'retry-after':'90'},json={'error':{'type':kind}}))
    c.advance(25)
    with pytest.raises(model_schedule.ModelWaiting) as e:
        with g.permit(10):pytest.fail('cooldown bypass')
    assert e.value.detail['retry_after_seconds']==65 and g.attempts==1
    c.advance(65)
    with g.permit(10):pass
    assert g.attempts==2


@pytest.mark.parametrize(('status','kind'),[(401,None),(403,None),(429,'exceeded_current_quota_error')])
def test_auth_or_quota_stops_until_operator_reinitializes(gate,status,kind):
    g,c=gate
    with g.permit(10) as p:g.observe(p,httpx.Response(status,json={'error':{'type':kind}}))
    c.advance(1000)
    with pytest.raises(HTTPException) as e:
        with g.permit(10):pytest.fail('permission failure retried')
    assert e.value.detail['code']=='MODEL_SCHEDULE_STOPPED' and g.attempts==1


def test_explicit_shared_request_cap_counts_admitted_attempts_only():
    c=Clock();g=model_schedule.ModelScheduler(clock=c,wall=c.wall,max_attempts=1)
    with g.permit(1):pass
    c.advance(25)
    with pytest.raises(HTTPException) as e:
        with g.permit(1):pass
    assert e.value.detail['code']=='MODEL_REQUEST_BUDGET' and g.attempts==1


def test_token_reservation_minute_and_daily_limits():
    c=Clock();g=model_schedule.ModelScheduler(clock=c,wall=c.wall,tpm=100,tpd=150)
    with g.permit(80):pass
    c.advance(25)
    with pytest.raises(model_schedule.ModelWaiting) as e:
        with g.permit(30):pass
    assert e.value.detail['reason']=='tpm_window'
    c.advance(36)
    with g.permit(70):pass
    c.advance(61)
    with pytest.raises(model_schedule.ModelWaiting) as e:
        with g.permit(1):pass
    assert e.value.detail['reason']=='local_utc_daily_tokens'
    with pytest.raises(HTTPException) as e:
        with g.permit(101):pass
    assert e.value.detail['code']=='MODEL_TOKEN_BUDGET'


def test_missing_usage_retains_reservation_and_headers_can_lower_limits(gate):
    g,c=gate
    with g.permit(80) as p:
        g.observe(p,httpx.Response(200,headers={'x-ratelimit-limit-requests':'2','x-ratelimit-limit-tokens':'100'},json={}))
    assert g.day_tokens==80 and g.tpm==100 and g.interval>=30
    assert g.describe()['unknown_provider_token_limits']


def test_retry_after_http_date_and_invalid_header(gate):
    from email.utils import format_datetime
    g,c=gate
    assert model_schedule.retry_seconds(format_datetime(c.wall()+timedelta(seconds=65)),c.wall())==65
    assert model_schedule.retry_seconds('nonsense',c.wall())==0


def test_language_and_agent_share_exact_http_gate(gate,monkeypatch):
    g,c=gate;calls=[];real=httpx.Client
    monkeypatch.setenv('TOURISM_LLM_API_KEY','test-only-key')
    monkeypatch.setenv('TOURISM_LLM_BASE_URL','https://provider.invalid/v1')
    monkeypatch.setenv('TOURISM_LLM_MODEL','kimi-k2.6')
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(**kw,transport=httpx.MockTransport(handle)))
    assert language._call('JSON',{})=={}
    with pytest.raises(model_schedule.ModelWaiting):provider.generate('JSON',{})
    c.advance()
    assert provider.generate('JSON',{})[0]=={}
    with pytest.raises(model_schedule.ModelWaiting):language._call('JSON',{})
    assert len(calls)==g.attempts==2


def test_acceptance_requires_explicit_isolated_runtime(monkeypatch,tmp_path):
    monkeypatch.setenv('TOURISM_AGENT_MODE','live_acceptance')
    with pytest.raises(HTTPException):model_schedule.acceptance_seconds()
    monkeypatch.setenv('TOURISM_ENABLE_LIVE_ACCEPTANCE','1');monkeypatch.setenv('TOURISM_ENV','test')
    monkeypatch.setenv('TOURISM_ACCEPTANCE_DB_ROOT',str(tmp_path));monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'test.db'))
    assert model_schedule.acceptance_seconds()==240
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path.parent/'outside.db'))
    with pytest.raises(HTTPException):model_schedule.acceptance_seconds()
    monkeypatch.setenv('TOURISM_AGENT_MODE','product');assert model_schedule.acceptance_seconds()==45


def install_model(monkeypatch):
    calls=[];real=httpx.Client;original=provider.generate
    monkeypatch.setenv('TOURISM_LLM_API_KEY','test-only-key');monkeypatch.setenv('TOURISM_LLM_MODEL','kimi-k2.6')
    monkeypatch.setenv('TOURISM_LLM_BASE_URL','https://provider.invalid/v1')
    def handle(request):
        payload=json.loads(json.loads(request.content)['messages'][1]['content']);calls.append(payload)
        if 'eligible_actions' not in payload:result={'actions':[{'kind':'search_dining','quote':'找个吃饭的地方'}]}
        else:
            result=next(a for tool in ('query_shows','search_dining','verify_travel','preview_plan') for a in payload['eligible_actions'] if a['tool']==tool)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}],
                                       'usage':{'prompt_tokens':20,'completion_tokens':5,'total_tokens':25}})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(**kw,transport=httpx.MockTransport(handle)))
    def tagged(*a,**kw):
        result,meta=original(*a,**kw);return result,{**meta,'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',tagged)
    return calls


def enable_acceptance(monkeypatch):
    monkeypatch.setenv('TOURISM_AGENT_MODE','live_acceptance');monkeypatch.setenv('TOURISM_ENV','test')
    monkeypatch.setenv('TOURISM_ENABLE_LIVE_ACCEPTANCE','1')
    monkeypatch.setenv('TOURISM_ACCEPTANCE_DB_ROOT',str(Path(os.environ['DATABASE_PATH']).parent))


def waiting_task(city,gate,monkeypatch,accept=True):
    walking(city);calls=install_model(monkeypatch)
    if accept:enable_acceptance(monkeypatch)
    t=start(city,'找个吃饭的地方，演出必须保留。').json();t=step(city,t).json()
    assert t['metrics']['model_calls']==1
    req={'request_id':rid(),'expected_step':t['step']}
    t=city['client'].post(path(city,'/agent/tasks/'+t['task_id']+'/step'),json=req).json()
    return t,req,calls


def test_product_returns_busy_promptly_without_changing_45_seconds(city,gate,monkeypatch):
    t,req,calls=waiting_task(city,gate,monkeypatch,False)
    assert t['status']=='model_busy' and t['limits']['max_seconds']==45
    assert t['metrics']['tools']==0 and len(calls)==1 and t['metrics']['elapsed_ms']<2000


def test_wait_does_not_consume_steps_tools_or_requests_and_replay_never_sends(city,gate,monkeypatch):
    g,c=gate;t,req,calls=waiting_task(city,gate,monkeypatch);before=get(city)
    assert t['status']=='running' and t['phase']=='waiting_model' and t['step']==1
    assert datetime.fromisoformat(t['waiting']['next_allowed_at']).tzinfo is not None
    assert not t['busy'] and not t['formal_state_changed']
    assert t['limits']['max_seconds']==240 and t['metrics']['tools']==0 and t['metrics']['model_calls']==1
    url=path(city,'/agent/tasks/'+t['task_id']+'/step')
    c.advance()
    assert city['client'].post(url,json=req).json()['step']==1 and len(calls)==1
    for _ in range(12):
        if t['status']!='running':break
        t=step(city,t).json()
        if t.get('waiting'):c.advance()
    assert t['status']=='needs_confirmation' and len(calls)==5 and t['metrics']['tools']==4
    assert t['events'][1]['tool']=='query_shows' and get(city)==before
    assert all(d['source']=='mock_model' for d in t['decisions'])
    assert confirm(city,t['result']).status_code==200


def test_cancel_wait_prevents_further_provider_calls(city,gate,monkeypatch):
    g,c=gate;t,req,calls=waiting_task(city,gate,monkeypatch);before=get(city)
    cancelled=city['client'].post(path(city,'/agent/tasks/'+t['task_id']+'/cancel')).json()
    c.advance(100)
    assert step(city,cancelled).json()['status']=='cancelled' and len(calls)==1
    assert get(city)==before


def test_wait_resume_rechecks_trip_version_before_sending(city,gate,monkeypatch):
    g,c=gate;t,req,calls=waiting_task(city,gate,monkeypatch)
    modify(city,{'operation':'set_anchor','anchor':{'place_id':'museum','confirmed_at':city['clock'][0].isoformat()}})
    c.advance()
    assert step(city,t).json()['status']=='stale' and len(calls)==1


def test_expired_task_never_sends_after_wait(city,gate,monkeypatch):
    g,c=gate;t,req,calls=waiting_task(city,gate,monkeypatch);before=get(city)
    service.TASKS[t['task_id']].started-=241
    assert city['client'].get(path(city,'/agent/tasks/'+t['task_id'])).json()['status']=='budget_exhausted'
    c.advance(100)
    assert step(city,t).json()['status']=='budget_exhausted' and len(calls)==1 and get(city)==before


def test_history_off_clears_waiting_tasks(city,gate,monkeypatch):
    t,req,calls=waiting_task(city,gate,monkeypatch)
    assert city['client'].put('/api/v1/memory/history',json={'enabled':False}).status_code==200
    assert t['task_id'] not in service.TASKS
    assert city['client'].get(path(city,'/agent/tasks/'+t['task_id'])).status_code==404


def test_wait_resume_rechecks_fact_fingerprint(city,gate,monkeypatch):
    from app.db import connect
    from app.city import facts
    g,c=gate;t,req,calls=waiting_task(city,gate,monkeypatch)
    with connect() as db:
        item=facts.records(db)['show_session','s2'].model_dump(mode='json')|{'version':2,'business_status':'cancelled'}
        facts.import_bundle(facts.FactBundle(records=[item]),db,apply=True)
    c.advance()
    assert step(city,t).json()['status']=='stale' and len(calls)==1


def test_long_provider_cooldown_cannot_extend_acceptance_budget(city,gate,monkeypatch):
    g,c=gate;t,req,calls=waiting_task(city,gate,monkeypatch)
    g.cool_until=1000
    t=step(city,t).json()
    assert t['status']=='budget_exhausted' and len(calls)==1
    assert t['metrics']['tools']==0


def test_429_numeric_daily_limit_is_exposed_as_a_stop(gate):
    g,c=gate
    with g.permit(5) as p:g.observe(p,httpx.Response(429,json={'error':{
        'type':'rate_limit_reached_error','message':'request reached organization max TPD: 100'}}))
    assert g.describe()['stopped']=='provider_daily_token_limit'


def test_visitor_cannot_request_test_profile_or_budget_via_http(city):
    walking(city);before=get(city)
    response=city['client'].post(path(city,'/agent/tasks'),json={'request_id':rid(),'text':'找个吃饭的地方。',
        'expected_version':before['version'],'expected_route_version':before['route_version'],
        'execution_mode':'live_acceptance','budget':{'max_seconds':240}})
    assert response.status_code==422 and get(city)==before


def test_pre_request_budget_rejection_is_recorded_as_not_sent(city,gate,monkeypatch):
    walking(city);install_model(monkeypatch)
    gate[0].max_attempts=0
    t=start(city,'找个吃饭的地方。').json();t=step(city,t).json()
    assert t['status']=='unavailable' and t['metrics']['model_calls']==0
    assert t['events'][0]['status']=='blocked_before_request'
    assert gate[0].attempts==0
