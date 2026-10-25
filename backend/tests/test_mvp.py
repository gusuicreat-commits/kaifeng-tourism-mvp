import csv
import io
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo

import networkx as nx
import pytest
from fastapi.testclient import TestClient
from app.db import connect
from app.main import app
from app.crowds import crowd_summary
from app.models import Observation
from app.planner import build_graph


def csv_bytes(rows):
    stream=io.StringIO()
    keys=['scenic_id','node_id','observed_at','period_end','count','crowd_level','capacity','source_type','source_name','source_url','measurement_kind','confidence','is_simulated']
    writer=csv.DictWriter(stream,fieldnames=keys)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def record(**changes):
    return dict(scenic_id='wansui',node_id='theatre',observed_at=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat(),
                source_type='field',source_name='测试观测',measurement_kind='observation',confidence=.8,is_simulated=False,
                crowd_level=.85,**{}) | changes


def upload(client,rows):
    return client.post('/api/crowds/import',headers={'x-scenic-admin-key':'test-admin-key-012345678901234567890123456789','x-scenic-reviewer':'test-reviewer'},files={'file':('data.csv',csv_bytes(rows),'text/csv')})


def test_search_and_scenic_details(client):
    assert len(client.get('/api/scenics').json()['items'])==5
    assert [s['id'] for s in client.get('/api/scenics?q=万岁山').json()['items']]==['wansui']
    assert client.get('/api/scenics?q=无此景区').json()['items']==[]
    assert client.get('/api/scenics/no-such-id').status_code==404
    s=client.get('/api/scenics/qingming').json()
    assert s['source_url'].startswith('https://')
    assert s['location'] is None
    assert s['shows']


@pytest.mark.parametrize('sid',['wansui','qingming'])
def test_network_is_connected_and_clearly_simulated(client,sid):
    net=client.get(f'/api/scenics/{sid}/network').json()
    assert net['is_simulated'] is True
    assert net['coordinate_space']=='local_schematic_units_not_geographic'
    assert all(f['properties']['is_simulated'] for f in net['features'])
    g,_,_,_=build_graph(sid,'demo',13,{},False)
    assert nx.is_strongly_connected(g)
    assert len(g)==8
    assert client.get('/api/scenics/tieta/network').status_code==404


def test_dijkstra_is_shortest_and_geometry_matches(client):
    result=client.post('/api/routes',json={'scenic_id':'wansui'}).json()
    g,_,_,_=build_graph('wansui','demo',13,{},False)
    assert result['distance_m']==nx.shortest_path_length(g,'gate','exit',weight='distance_m')
    assert result['node_path']==['gate','plaza','culture','exit']
    coords=result['route_geojson']['features'][0]['geometry']['coordinates']
    assert coords==[g.nodes[n]['coordinates'] for n in result['node_path']]


def test_crowd_change_alters_quiet_route_not_distance_route(client):
    low={'plaza':.05,'culture':.05,'food':1,'garden':1,'theatre':1,'photo':1}
    high={'plaza':1,'culture':1,'food':.05,'garden':.05,'theatre':.05,'photo':.05}
    def run(mode,values):
        return client.post('/api/routes',json={'scenic_id':'wansui','mode':mode,'crowd_overrides':values}).json()
    assert run('quiet',low)['node_path']!=run('quiet',high)['node_path']
    assert run('shortest',low)['node_path']==run('shortest',high)['node_path']


def test_interest_changes_recommendation_and_budget_is_respected(client):
    def run(interest):
        r=client.post('/api/plans',json={'scenic_id':'wansui','available_minutes':60,'interests':[interest],'avoid_crowds':False})
        assert r.status_code==201,r.text
        p=r.json()
        assert p['total_minutes']<=60
        assert p['remaining_minutes']>=0
        assert p['node_path'][0]=='gate' and p['node_path'][-1]=='exit'
        return p
    show=run('show');food=run('food')
    assert 'theatre' in [s['id'] for s in show['stops']]
    assert 'food' in [s['id'] for s in food['stops']]
    assert show['node_path']!=food['node_path']


@pytest.mark.parametrize('sid',['wansui','qingming'])
def test_must_visit_and_walking_preferences(client,sid):
    body=dict(scenic_id=sid,available_minutes=180,must_visit=['theatre','photo'],interests=['show'])
    p=client.post('/api/plans',json=body|{'walking':'normal'}).json()
    slow=client.post('/api/plans',json=body|{'walking':'relaxed'}).json()
    assert {'theatre','photo'}.issubset({s['id'] for s in p['stops']})
    assert slow['total_minutes']<=180
    assert slow['walk_minutes']>=p['walk_minutes']
    for s in p['stops']:
        assert s['arrival_minutes']+s['stay_minutes']<=p['total_minutes']+.1
    assert client.post('/api/plans',json=body|{'available_minutes':15}).status_code==422
    assert client.post('/api/plans',json=body|{'must_visit':['no-such-point']}).status_code==422
    assert client.post('/api/plans',json=body|{'must_visit':['gate']}).status_code==422


def test_demo_is_never_current_and_reported_unknown_without_data(client):
    demo=client.get('/api/scenics/wansui/crowds').json()
    assert demo['is_simulated']
    assert all(n['current'] is None for n in demo['nodes'])
    theatre=next(n for n in demo['nodes'] if n['node_id']=='theatre')
    assert theatre['estimate']['sample_days']==7
    reported=client.get('/api/scenics/wansui/crowds?dataset=reported').json()
    assert all(n['current'] is None and n['estimate'] is None and n['category']=='unknown' for n in reported['nodes'])


def test_import_current_stale_aggregate_and_separation(client):
    now=datetime.now(timezone.utc)
    assert upload(client,[record()]).status_code==200
    node=next(n for n in client.get('/api/scenics/wansui/crowds?dataset=reported').json()['nodes'] if n['node_id']=='theatre')
    assert node['current']['level']==.85
    assert node['current']['is_simulated']==0
    assert node['estimate'] is None
    assert upload(client,[record(node_id='photo',observed_at=(now-timedelta(hours=2)).isoformat())]).status_code==200
    assert upload(client,[record(node_id='food',measurement_kind='aggregate',observed_at=(now-timedelta(hours=1)).isoformat(),period_end=(now-timedelta(minutes=1)).isoformat())]).status_code==200
    nodes=client.get('/api/scenics/wansui/crowds?dataset=reported').json()['nodes']
    assert next(n for n in nodes if n['node_id']=='photo')['current'] is None
    assert next(n for n in nodes if n['node_id']=='food')['current'] is None
    assert all(n['current'] is None for n in client.get('/api/scenics/wansui/crowds?dataset=demo').json()['nodes'])


def test_count_only_is_not_invented_density(client):
    assert upload(client,[record(crowd_level=None,count=120)]).status_code==200
    node=next(n for n in client.get('/api/scenics/wansui/crowds?dataset=reported').json()['nodes'] if n['node_id']=='theatre')
    assert node['current']['count']==120
    assert node['current']['level'] is None
    assert node['category']=='unknown'
    with connect() as db:
        db.execute('UPDATE observations SET capacity=200 WHERE is_simulated=0')
    node=next(n for n in client.get('/api/scenics/wansui/crowds?dataset=reported').json()['nodes'] if n['node_id']=='theatre')
    assert node['current']['level']==.6


@pytest.mark.parametrize('change',[{'node_id':'unknown'},{'confidence':2},{'crowd_level':-1},{'count':-5},{'source_url':'javascript:alert(1)'},{'is_simulated':''},{'observed_at':'2026-01-01T12:00:00'},{'measurement_kind':'aggregate'}, {'source_type':'invented'},{'observed_at':'2099-01-01T12:00:00+08:00'}])
def test_csv_rejects_bad_batch_atomically(client,change):
    with connect() as db: before=db.execute('SELECT count(*) FROM observations').fetchone()[0]
    result=upload(client,[record(node_id='garden'),record(**change)])
    assert result.status_code==422,result.text
    assert result.json()['detail']['errors']
    with connect() as db: assert db.execute('SELECT count(*) FROM observations').fetchone()[0]==before


def test_import_is_idempotent_and_timezone_is_canonical(client):
    r=record()
    one=upload(client,[r]).json();two=upload(client,[r]).json()
    assert one['inserted']==1 and two['updated']==1 and two['inserted']==0
    dt=datetime.fromisoformat(r['observed_at']).astimezone(ZoneInfo('Asia/Shanghai'))
    assert upload(client,[r|{'observed_at':dt.isoformat()}]).json()['updated']==1
    assert upload(client,[r,r]).status_code==422


def test_three_days_required_and_no_future_or_daily_aggregate_in_patterns(client):
    now=datetime.now(timezone.utc)
    samples=[record(observed_at=(now-timedelta(days=d)).isoformat(),crowd_level=.6) for d in [1,2,3]]
    assert upload(client,samples[:2]).status_code==200
    s=crowd_summary('wansui','reported',now.astimezone(ZoneInfo('Asia/Shanghai')).hour)
    assert next(n for n in s['nodes'] if n['node_id']=='theatre')['estimate'] is None
    assert upload(client,samples[2:]).status_code==200
    s=crowd_summary('wansui','reported',now.astimezone(ZoneInfo('Asia/Shanghai')).hour)
    assert next(n for n in s['nodes'] if n['node_id']=='theatre')['estimate']['level']==.6
    daily=record(node_id='food',measurement_kind='aggregate',observed_at=(now-timedelta(days=2)).isoformat(),period_end=(now-timedelta(days=1)).isoformat())
    assert upload(client,[daily]).status_code==200
    assert next(n for n in crowd_summary('wansui','reported')['nodes'] if n['node_id']=='food')['history']==[]


def test_imported_crowd_changes_route(client):
    now=datetime.now(timezone.utc)
    hour=now.astimezone(ZoneInfo('Asia/Shanghai')).hour
    def import_levels(levels):
        assert upload(client,[record(node_id=n,crowd_level=v,observed_at=(now-timedelta(minutes=2)).isoformat()) for n,v in levels.items()]).status_code==200
        return client.post('/api/routes',json={'scenic_id':'wansui','dataset':'reported','mode':'quiet','hour':hour}).json()['node_path']
    a=import_levels(dict(plaza=.05,culture=.05,food=1,garden=1,theatre=1,photo=1))
    b=import_levels(dict(plaza=1,culture=1,food=.05,garden=.05,theatre=.05,photo=.05))
    assert a!=b


def test_restart_preserves_plan_and_imported_data(client):
    p=client.post('/api/plans',json={'scenic_id':'qingming'}).json()
    assert upload(client,[record()]).status_code==200
    with connect() as db: before=db.execute('SELECT count(*) FROM observations').fetchone()[0]
    with TestClient(app) as restarted:
        assert restarted.get('/api/plans/'+p['id']).status_code==401
        restarted.cookies.update(client.cookies)
        assert restarted.get('/api/plans/'+p['id']).json()['node_path']==p['node_path']
        assert restarted.get('/api/health').json()['status']=='ok'
        with connect() as db: assert db.execute('SELECT count(*) FROM observations').fetchone()[0]==before


def test_invalid_api_input_and_oversized_csv(client):
    assert client.post('/api/plans',json={'scenic_id':'wansui','available_minutes':0}).status_code==422
    assert client.post('/api/plans',json={'scenic_id':'wansui','dataset':'reported','crowd_overrides':{'theatre':.2}}).status_code==422
    assert client.post('/api/routes',json={'scenic_id':'wansui','start':'unknown'}).status_code==422
    assert client.get('/api/scenics/wansui/crowds?hour=28').status_code==422
    assert client.get('/api/plans/nonexistent').status_code==404
    headers={'x-scenic-admin-key':'test-admin-key-012345678901234567890123456789','x-scenic-reviewer':'test-reviewer'}
    assert client.post('/api/crowds/import',headers=headers,files={'file':('big.csv',b'x'*(1024*1024+1),'text/csv')}).status_code==413
    assert client.post('/api/crowds/import',headers=headers,files={'file':('bad.csv',b'wrong,headers\nx,y','text/csv')}).status_code==422
    assert client.post('/api/crowds/import',files={'file':('data.csv',b'','text/csv')}).status_code==403
    assert b'is_simulated' in client.get('/api/crowds/template').content


def test_no_reliable_crowd_falls_back_to_ordinary_planning(client):
    base={'scenic_id':'wansui','dataset':'reported','available_minutes':120,'interests':['show','photo']}
    ordinary=client.post('/api/plans',json=base|{'avoid_crowds':False}).json()
    quiet=client.post('/api/plans',json=base|{'mode':'quiet'}).json()
    assert ordinary['node_path']==quiet['node_path']
    assert ordinary['total_minutes']==quiet['total_minutes']
    assert quiet['crowd_optimization_applied'] is False
    assert any('数据不足' in w for w in quiet['warnings'])


def test_official_sources_precede_field_when_both_qualify(client):
    now=(datetime.now(ZoneInfo('Asia/Shanghai'))-timedelta(days=2)).replace(hour=13,minute=30,second=0,microsecond=0)
    hour=now.astimezone(ZoneInfo('Asia/Shanghai')).hour
    rows=[]
    for d in [1,2,3]:
        when=(now-timedelta(days=d)).isoformat()
        rows.extend([record(observed_at=when,source_type='official_history',source_name='测试官方声明',crowd_level=.2),
                     record(observed_at=when,source_name='测试实地声明',crowd_level=.9)])
    rows.extend([record(observed_at=(now-timedelta(minutes=20)).isoformat(),source_type='official_history',source_name='测试官方声明',crowd_level=.3),
                 record(observed_at=(now-timedelta(minutes=1)).isoformat(),crowd_level=.9)])
    assert upload(client,rows).status_code==200
    node=next(n for n in crowd_summary('wansui','reported',hour,now=now)['nodes'] if n['node_id']=='theatre')
    assert node['current']['level']==.3
    # Current-day official sample also contributes, equal weight per day.
    assert node['estimate']['level']==.225
