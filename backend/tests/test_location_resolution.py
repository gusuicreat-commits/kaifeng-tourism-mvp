"""POI resolution stays ephemeral; HTTP tests only use pytest's temporary database."""
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect
from app.city import location_resolution as lr
from app.city.engine.amap import AmapProvider

SEARCH='/api/city/v1/locations/search'

def fake_provider(monkeypatch, rows=None):
    rows = rows if rows is not None else [
        {'id':'station','name':'开封站','location':'114.3,34.8','adcode':'410202','citycode':'0371','address':'[合成测试]车站路'},
        {'id':'north','name':'开封北站','location':'114.31,34.81','adcode':'410211','citycode':'0371'},
        {'id':'other-city','name':'外地同名','location':'114.3,34.8','adcode':'110100'},
    ]
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'status':'1','infocode':'10000','pois':rows})
    def factory():
        return AmapProvider('fake-secret',enabled=True,terms_reviewed=True,api_version='v5',retries=0,
                            transport=httpx.MockTransport(handle))
    monkeypatch.setattr(lr,'provider_from_environment',factory)
    monkeypatch.setattr(lr,'STORE',lr.ResolutionStore())
    return calls

def test_search_requires_session_and_does_not_choose_first_candidate(client,monkeypatch):
    calls=fake_provider(monkeypatch)
    assert client.post(SEARCH,json={'query':'开封站'}).status_code==401
    client.post('/api/v1/sessions')
    response=client.post(SEARCH,json={'query':'  开封站  '})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['status']=='candidates' and len(result['candidates'])==2
    assert result['requires_user_confirmation'] and result['rejected_count']==1
    assert result['query']=='开封站'
    assert calls[0].url.path=='/v5/place/text'
    assert calls[0].url.params['region']=='410200'
    assert 'fake-secret' not in response.text
    selected=client.post('/api/city/v1/locations/'+result['resolution_id']+'/confirm',json={'candidate_id':result['candidates'][1]['candidate_id']})
    assert selected.json()['selected']['name']=='开封北站'
    assert selected.json()['formal_fact_status']=='provider_candidate_only'
    assert selected.json()['status']=='needs_data'
    with connect() as db:
        for table in ('city_facts','city_engine_operations','city_engine_routes'):
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0


def test_confirmation_checks_session_expiry_and_candidate(client,monkeypatch):
    fake_provider(monkeypatch)
    client.post('/api/v1/sessions')
    result=client.post(SEARCH,json={'query':'开封站'}).json()
    path='/api/city/v1/locations/'+result['resolution_id']+'/confirm'
    choice={'candidate_id':result['candidates'][0]['candidate_id']}
    with TestClient(app) as other:
        other.post('/api/v1/sessions')
        assert other.post(path,json=choice).status_code==404
    assert client.post(path,json={'candidate_id':'invalid'}).status_code==404
    lr.STORE._items[result['resolution_id']].created_at=time.monotonic()-1000
    assert client.post(path,json=choice).status_code==404


def test_child_poi_is_not_an_entrance_and_raw_payload_is_not_exposed(client,monkeypatch):
    fake_provider(monkeypatch,[{'id':'garden','name':'清明上河园','location':'114.3,34.8','adcode':'410202',
        'children':[{'id':'gate','name':'[合成测试]南门','location':'114.3,34.8','key':'do-not-echo','raw':{'anything':'private'}}],
        'business':{'raw':'do-not-echo'}}])
    client.post('/api/v1/sessions')
    result=client.post(SEARCH,json={'query':'清明上河园'}).json()
    item=result['candidates'][0]
    assert item['entrance_status']=='needs_entrance_verification'
    assert item['children']==[{'id':'gate','name':'[合成测试]南门','location':'114.3,34.8'}]
    assert 'business' not in item


def test_disabled_and_bad_query_stay_needs_data(client,monkeypatch):
    monkeypatch.setenv('AMAP_LOCATION_SEARCH_ENABLED','0')
    client.post('/api/v1/sessions')
    assert client.post(SEARCH,json={'query':'开封站'}).json()['provider_status']=='disabled'
    for query in (' ', '长'*81):
        assert client.post(SEARCH,json={'query':query}).status_code==422


def test_store_bound_prevents_unbounded_candidate_retention():
    store=lr.ResolutionStore(max_items=1)
    first,_=store.put('s','first',())
    second,_=store.put('s','second',())
    assert list(store._items)==[second] and first!=second

@pytest.mark.parametrize('action',['history_off','delete'])
def test_privacy_clear_purges_candidate_memory(client,monkeypatch,action):
    from uuid import uuid4
    fake_provider(monkeypatch)
    client.post('/api/v1/sessions')
    result=client.post(SEARCH,json={'query':'开封站'}).json()
    assert result['resolution_id'] in lr.STORE._items
    if action=='history_off':
        assert client.put('/api/v1/memory/history',json={'enabled':False}).status_code==200
    else:
        assert client.post('/api/v1/privacy/delete-request',json={'request_id':str(uuid4()),'confirmed':True}).status_code==200
    assert not lr.STORE._items

@pytest.mark.parametrize('binding_state',['verified','missing','unverified','rights_unknown','source_expired'])
def test_only_curated_usable_binding_returns_formal_id(client,monkeypatch,binding_state):
    import json
    from pathlib import Path
    from app.city import facts
    fake_provider(monkeypatch)
    fixture=json.loads((Path(__file__).parent/'fixtures/engine_synthetic.json').read_text())
    source=fixture['facts']['records'][0]|{'is_demo':False}
    place=fixture['facts']['records'][1]|{'is_demo':False}
    if binding_state=='unverified':place['validation_status']='unverified'
    if binding_state=='rights_unknown':place['rights_status']='unknown'
    if binding_state=='source_expired':source['effective_to']='2021-01-01T00:00:00+08:00'
    with connect() as db:
        facts.import_bundle(facts.FactBundle(records=[source,place]),db,apply=True)
        if binding_state!='missing':db.execute('INSERT INTO city_object_bindings VALUES (?,?,?,?)',('amap_poi','station','place',place['fact_id']))
    client.post('/api/v1/sessions')
    found=client.post(SEARCH,json={'query':'开封站'}).json()
    result=client.post('/api/city/v1/locations/'+found['resolution_id']+'/confirm',json={'candidate_id':found['candidates'][0]['candidate_id']}).json()
    if binding_state=='verified':
        assert result['status']=='matched_fact' and result['formal_match']['fact_id']==place['fact_id']
    else:
        assert result['status']=='needs_data' and result['formal_match'] is None
