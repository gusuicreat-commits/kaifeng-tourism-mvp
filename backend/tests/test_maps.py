import logging
import httpx
import pytest
from app import maps

KEY='a'*32
CODE='b'*32
SERVICE='c'*32

@pytest.fixture(autouse=True)
def settings(monkeypatch):
    maps._rates.clear()
    monkeypatch.setenv('AMAP_JS_KEY',KEY)
    monkeypatch.setenv('AMAP_JS_SECURITY_CODE',CODE)
    monkeypatch.setenv('AMAP_WEB_SERVICE_KEY',SERVICE)


def upstream(monkeypatch, handler):
    original=httpx.AsyncClient
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False
        assert kwargs['trust_env'] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(maps.httpx,'AsyncClient',client)


def test_config_public_identifier_only(client):
    r=client.get('/api/maps/config')
    assert r.status_code==200 and r.json()['key']==KEY
    assert r.json()['service_host'].endswith('/_AMapService')
    assert CODE not in r.text and SERVICE not in r.text
    assert 'no-store' in r.headers['cache-control']

@pytest.mark.parametrize('setting', ['AMAP_JS_KEY','AMAP_JS_SECURITY_CODE'])
def test_missing_config_fails_closed(client,monkeypatch,setting):
    monkeypatch.setenv(setting,'')
    assert client.get('/api/maps/config').json()['configured'] is False
    assert client.get('/api/maps/_AMapService/v3/ip').status_code==503


def test_webservice_key_cannot_be_js_key(client,monkeypatch):
    monkeypatch.setenv('AMAP_JS_KEY',SERVICE)
    assert not client.get('/api/maps/config').json()['configured']

@pytest.mark.parametrize('url',[
    '/api/maps/_AMapService/https://evil.test',
    '/api/maps/_AMapService/v3/key',
    '/api/maps/_AMapService/v3/ip?url=https://evil.test',
    '/api/maps/_AMapService/v3/ip?jscode=attack',
    '/api/maps/_AMapService/v3/ip?callback=alert(1)',
    '/api/maps/_AMapService/v3/ip?key=a&key=b',
    '/api/maps/_AMapService/v3/ip?key=wrong',
    '/api/maps/_AMapService/v3/ip?KEY=wrong',
])
def test_reject_unsafe_proxy_requests(client,url):
    assert client.get(url).status_code in (400,403,404)


def test_cross_site_and_post_rejected(client):
    assert client.get('/api/maps/config',headers={'Origin':'https://evil.test'}).status_code==403
    assert client.get('/api/maps/_AMapService/v3/ip',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    assert client.post('/api/maps/_AMapService/v3/ip').status_code==405

@pytest.mark.parametrize('path,host',[('v4/map/styles','webapi.amap.com'),('v3/direction/walking','restapi.amap.com')])
def test_proxy_fixed_host_injects_secret_strips_headers_and_redacts(client,monkeypatch,caplog,path,host):
    def handler(req):
        assert req.url.host==host
        assert req.url.params['key']==KEY and req.url.params['jscode']==CODE
        assert 'cookie' not in req.headers and 'authorization' not in req.headers
        return httpx.Response(200,json={'status':'1','echo':CODE,'other':SERVICE},headers={'Set-Cookie':'secret=x'})
    upstream(monkeypatch,handler)
    with caplog.at_level(logging.INFO,logger='httpx'):
        r=client.get('/api/maps/_AMapService/'+path,headers={'Cookie':'private=x','Authorization':'Bearer private'})
    assert r.status_code==200 and CODE not in r.text and SERVICE not in r.text
    assert 'set-cookie' not in r.headers and 'no-store' in r.headers['cache-control']
    assert CODE not in caplog.text and SERVICE not in caplog.text

@pytest.mark.parametrize('status',[302,401,429,500])
def test_upstream_error_does_not_leak_or_redirect(client,monkeypatch,status):
    upstream(monkeypatch,lambda _: httpx.Response(status,text=CODE,headers={'location':'https://evil.test'}))
    r=client.get('/api/maps/_AMapService/v3/ip')
    assert r.status_code==502 and CODE not in r.text and 'location' not in r.headers


def test_timeout_safe(client,monkeypatch):
    def handler(req):raise httpx.ReadTimeout('private '+CODE,request=req)
    upstream(monkeypatch,handler)
    r=client.get('/api/maps/_AMapService/v3/ip')
    assert r.status_code==502 and CODE not in r.text


def test_proxy_response_limits_and_mime(client,monkeypatch):
    upstream(monkeypatch,lambda _:httpx.Response(200,content=b'x'*(2*1024*1024+1)))
    assert client.get('/api/maps/_AMapService/v3/ip').status_code==502


def test_jsonp_is_restricted_identifier(client,monkeypatch):
    upstream(monkeypatch,lambda _:httpx.Response(200,text='amap_cb({"status":"1"})',headers={'content-type':'application/javascript'}))
    assert client.get('/api/maps/_AMapService/v3/ip?callback=amap_cb').status_code==200


def test_rate_limit(client):
    maps._rates['testclient']=(120,__import__('time').monotonic())
    assert client.get('/api/maps/config').status_code==429


def test_official_sdk_duplicate_identical_s_is_normalized(client,monkeypatch):
    def handler(req):
        assert req.url.params.get_list('s')==['rsv3']
        return httpx.Response(200,json={'status':'1'})
    upstream(monkeypatch,handler)
    assert client.get('/api/maps/_AMapService/v3/direction/walking?s=rsv3&s=rsv3').status_code==200
    assert client.get('/api/maps/_AMapService/v3/direction/walking?s=rsv3&s=different').status_code==400


def test_jsonp_json_mime_is_corrected_for_nosniff(client,monkeypatch):
    upstream(monkeypatch,lambda _:httpx.Response(200,text='amap_cb({"status":"1"})',headers={'content-type':'application/json'}))
    r=client.get('/api/maps/_AMapService/v3/ip?callback=amap_cb')
    assert r.headers['content-type'].startswith('application/javascript')
    assert r.headers['x-content-type-options']=='nosniff'


def test_map_anchor_remains_same_authorized_assistant_state(client):
    import json
    from datetime import timedelta
    from pathlib import Path
    from uuid import uuid4
    from app.city import facts
    from app.db import connect
    from app.memory import storage
    bundle=facts.FactBundle.model_validate_json((Path(__file__).parent/'fixtures/city_facts.json').read_text())
    with connect() as db:facts.import_bundle(bundle,db,apply=True)
    client.post('/api/v1/sessions')
    moment=storage.now().astimezone(facts.TZ)
    draft={'business_date':moment.date().isoformat(),'starts_at':moment.replace(hour=0,minute=0,second=0).isoformat(),
           'ends_at':moment.replace(hour=23,minute=59,second=59).isoformat(),
           'origin_place_id':'test:gate','destination_place_id':'test:hotel'}
    trip=client.post('/api/city/v1/trips',json={'request_id':str(uuid4()),'draft':draft}).json()
    path='/api/city/v1/trips/'+trip['id']
    r=client.patch(path,json={'request_id':str(uuid4()),'expected_version':trip['version'],
                            'action':{'operation':'set_anchor','anchor':{'place_id':'test:gate','confirmed_at':moment.isoformat()}}})
    assert r.status_code==200,r.text
    saved=r.json();assert saved['route_version']==0
    ctx=client.get(path+'/assistant/context').json()
    assert 'test:gate' in json.dumps(ctx)
    assert 'manual_known_place' in json.dumps(ctx)
    assert 'live_tracking' in json.dumps(ctx)
    assert client.get(path).json()==saved


def test_uvicorn_map_access_log_drops_query_and_coordinates(caplog):
    with caplog.at_level(logging.INFO,logger='uvicorn.access'):
        logging.getLogger('uvicorn.access').info('%s - "%s %s HTTP/%s" %d',
            'client','GET','/api/maps/_AMapService/v3/ip?key='+KEY+'&location=114.3,34.8','1.1',200)
    assert KEY not in caplog.text and '114.3' not in caplog.text
    assert '/api/maps/_AMapService/v3/ip' in caplog.text
