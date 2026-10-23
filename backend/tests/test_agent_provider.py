import json
import httpx
import pytest
from fastapi import HTTPException
from app.city.agent import provider


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('TOURISM_LLM_BASE_URL','https://provider.invalid/v1')
    monkeypatch.setenv('TOURISM_LLM_API_KEY','test-only-not-a-real-key')
    monkeypatch.setenv('TOURISM_LLM_MODEL','kimi-k2.6')


def test_json_action_protocol_collects_usage_and_limits(configured,monkeypatch):
    real=httpx.Client
    def response(request):
        body=json.loads(request.content)
        assert body['max_tokens']==200 and body['thinking']=={'type':'disabled'}
        assert 'temperature' not in body and 'tools' not in body
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"tool":"query_shows"}'}}],
                                       'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110}})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(**kw,transport=httpx.MockTransport(response)))
    result,meta=provider.generate('Return JSON',{'text':'synthetic'},max_tokens=200)
    assert result['tool']=='query_shows' and meta['usage']['total_tokens']==110 and meta['cost_cny'] is None


@pytest.mark.parametrize('case',['401','429','timeout','length','native_tool','bad_json'])
def test_provider_failure_is_sanitized_and_never_automatically_retried(configured,monkeypatch,case):
    real=httpx.Client;calls=[]
    def response(request):
        calls.append(1)
        if case in ('401','429'):return httpx.Response(int(case),json={'secret':'test-only-not-a-real-key'})
        if case=='timeout':raise httpx.ReadTimeout('private URL and secret must not escape')
        return httpx.Response(200,json={'choices':[{'finish_reason':'length' if case=='length' else 'stop',
            'message':{'content':'bad' if case=='bad_json' else '{}','tool_calls':[{}] if case=='native_tool' else []}}]})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(**kw,transport=httpx.MockTransport(response)))
    with pytest.raises(HTTPException) as error:provider.generate('JSON',{})
    assert len(calls)==1 and 'test-only-not-a-real-key' not in str(error.value.detail)


@pytest.mark.parametrize(('kind','category'),[
    ('engine_overloaded_error','overloaded'),('rate_limit_reached_error','rate_limit'),
    ('exceeded_current_quota_error','quota'),('new_vendor_error','unknown')])
def test_429_diagnostics_classify_without_retry_or_secrets(configured,monkeypatch,kind,category):
    real=httpx.Client;calls=[]
    def response(request):
        calls.append(1)
        return httpx.Response(429,headers={'Retry-After':'7','X-Request-Id':'req-synthetic-1'},json={
            'error':{'type':kind,'message':'Limit reached; Bearer test-only-not-a-real-key https://private.invalid/path?token=abc'}})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(**kw,transport=httpx.MockTransport(response)))
    with pytest.raises(HTTPException) as e:provider.generate('JSON',{})
    d=e.value.detail
    assert d['upstream_status']==429 and d['error_type']==kind and d['limit_classification']==category
    assert d['retry_after_seconds']==7 and d['request_id']=='req-synthetic-1'
    assert d['automatic_retries']==0 and d['http_attempts']==len(calls)==1
    assert d['endpoint_host']=='provider.invalid' and d['model']=='kimi-k2.6'
    assert 'test-only-not-a-real-key' not in json.dumps(d) and 'private.invalid' not in json.dumps(d)


def test_retry_after_accepts_http_date_and_rejects_invalid():
    from datetime import datetime,timedelta,timezone
    from email.utils import format_datetime
    raw=format_datetime(datetime.now(timezone.utc)+timedelta(seconds=30),usegmt=True)
    assert 28<=provider.retry_after(raw)[1]<=31
    assert provider.retry_after('invalid')==('invalid',None)
    assert provider.retry_after(None)==(None,None)


def test_invalid_output_keeps_known_usage_and_request_id(configured,monkeypatch):
    real=httpx.Client
    def response(request):
        return httpx.Response(200,headers={'request-id':'synthetic-output-1'},json={
            'usage':{'prompt_tokens':5,'completion_tokens':3,'total_tokens':8},
            'choices':[{'finish_reason':'length','message':{'content':'{'}}]})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(**kw,transport=httpx.MockTransport(response)))
    with pytest.raises(HTTPException) as e:provider.generate('JSON',{})
    assert e.value.detail['usage']['total_tokens']==8
    assert e.value.detail['request_id']=='synthetic-output-1'


def test_account_identifiers_are_removed_but_numeric_limits_and_request_ids_remain(configured,monkeypatch):
    real=httpx.Client
    def response(request):
        return httpx.Response(429,headers={'X-Request-Id':'req-'+('a'*48)},json={'error':{
            'type':'rate_limit_reached_error','message':'Your account somebody@example.invalid<ak-account123> request reached organization max RPM: 3'}})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(**kw,transport=httpx.MockTransport(response)))
    with pytest.raises(HTTPException) as e:provider.generate('JSON',{})
    d=e.value.detail
    assert 'somebody' not in d['error_summary'] and 'ak-account123' not in d['error_summary']
    assert d['limit_metric']=='RPM' and d['limit_value']==3
    assert d['request_id']=='req-'+('a'*48)
