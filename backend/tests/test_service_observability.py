"""No inference of HTTP success from an adapter/tool step."""
import json
from types import SimpleNamespace
import pytest
from app.city import hotels
from app.city.agent import provider
from app import language
from test_travel_services import env,query,raw
from test_city_agent import run


def test_missing_fields_is_a_step_with_zero_provider_requests(env):
    result=query(env,'找酒店')['result'];d=result['query_diagnostics']
    assert d=={'workflow_steps':1,'adapter_executions':0,'http_attempts':0,'cache_hits':0,
        'provider_status':'needs_clarification','normalized_candidate_count':0,'execution_basis':'missing_user_fields'}


@pytest.mark.parametrize('status,expected_task', [('empty','needs_data'),('timeout','unavailable'),('rate_limited','unavailable')])
def test_failure_and_candidate_count_match_agent_event(env,monkeypatch,status,expected_task):
    c,p=env;message='查明天入住两晚酒店'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_services','quote':message}],
        'service_query':{'quote':message,'fields':[{'field':'check_in','value':'明天','quote':'明天入住'},
            {'field':'nights','value':2,'quote':'两晚'}]}},{'source':'mock_model','http_attempts':0}))
    monkeypatch.setattr(hotels,'search',lambda q:{'status':status,'items':[],
        'transport':{'http_attempts':0 if status=='rate_limited' else None,'basis':'synthetic_adapter'}})
    task=run({'client':c,'trip':c.get(p).json()},message)
    d=task['result']['services']['result']['query_diagnostics']
    assert task['status']==expected_task and d['provider_status']==status
    assert task['events'][-1]['query_diagnostics']==d
    assert task['metrics']['service_queries']==[d] and task['metrics']['model_http_attempts']==0
    assert d['adapter_executions']==1 and d['normalized_candidate_count']==0


def test_cli_cache_does_not_claim_unobserved_http(tmp_path,monkeypatch):
    cli=tmp_path/'flyai.cjs';cli.write_text('// synthetic')
    monkeypatch.setenv('FLYAI_ENABLED','true');monkeypatch.setenv('FLYAI_API_KEY','synthetic-private-key')
    monkeypatch.setenv('FLYAI_CLI_PATH',str(cli));monkeypatch.setattr(hotels.shutil,'which',lambda _: '/test/node')
    hotels.CACHE.clear();monkeypatch.setattr(hotels,'LAST',0)
    calls=[]
    monkeypatch.setattr(hotels.subprocess,'run',lambda *a,**k:calls.append(1) or SimpleNamespace(returncode=0,stdout=json.dumps(raw())))
    q=hotels.Search(check_in='2030-05-01',check_out='2030-05-02')
    first=hotels.search(q);second=hotels.search(q)
    assert len(calls)==1
    assert first['transport']['http_attempts'] is None and first['transport']['cli_invocations']==1
    assert second['transport']=={'cli_invocations':0,'http_attempts':0,'basis':'cache'}
    assert second['cache_hit'] and second['items']==first['items']
    assert 'synthetic-private-key' not in json.dumps(first)+json.dumps(second)
    hotels.CACHE.clear()
