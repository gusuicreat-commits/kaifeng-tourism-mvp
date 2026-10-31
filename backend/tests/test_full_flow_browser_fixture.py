"""Full-flow dates follow Beijing tomorrow without changing shared fixtures."""
import json
import math
import os
import runpy
import shutil
import subprocess
from datetime import date,datetime,timedelta,timezone
from pathlib import Path

import pytest
from app.city.facts import FactBundle
from app.city import facts
from app.city.engine.models import OperationalBundle
from app.city.engine.storage import import_operations,load_operations
from app.db import connect
from test_exploration import collect,base,rid

ROOT=Path(__file__).resolve().parents[2]


def generate(script,*args):
    node=shutil.which('node')
    if not node:pytest.fail('Node is required to validate the full-flow fixture generator')
    return subprocess.run([node,'--input-type=module','-e',script,*args],cwd=ROOT/'frontend',capture_output=True,text=True,check=True).stdout


@pytest.fixture(scope='module')
def full_flow_data():
    script="import {fullFlowFixtures,scenic,food,morning,endpoints} from './playwright.full-flow.fixture.mjs'; process.stdout.write(JSON.stringify({...fullFlowFixtures(),scenic,food,morning,endpoints}))"
    return json.loads(generate(script))


@pytest.fixture
def full_flow_client(client,full_flow_data,monkeypatch):
    from app.city.agent import provider
    monkeypatch.setattr(provider,'generate',lambda *a,**kw:pytest.fail('Full-flow must not call a model'))
    with connect() as db:
        facts.import_bundle(FactBundle.model_validate(full_flow_data['facts']),db,apply=True)
        for binding in full_flow_data['bindings']:db.execute('INSERT INTO city_object_bindings VALUES (?,?,?,?)',binding)
        import_operations(db,OperationalBundle.model_validate(full_flow_data['operations']),facts.records(db),apply=True)
    return client


@pytest.fixture
def demo_full_flow_client(full_flow_client,monkeypatch):
    monkeypatch.setenv('KAIFENG_DEMO_MODE','1')
    return full_flow_client


@pytest.mark.parametrize('instant',[
    '2026-10-31T15:59:59+00:00','2026-10-31T16:00:00+00:00',
    '2026-12-31T16:00:00+00:00','2028-02-28T00:00:00+00:00',
])
def test_full_flow_fixture_dates_and_time_order(instant):
    script="import {datedFacts} from './playwright.full-flow.fixture.mjs'; process.stdout.write(datedFacts(new Date(process.argv[1])))"
    output=generate(script,instant)
    original=(ROOT/'backend/tests/fixtures/city_facts.json').read_text()
    tomorrow=(datetime.fromisoformat(instant).astimezone(timezone(timedelta(hours=8)))+timedelta(days=1)).date().isoformat()
    assert output==original.replace('2026-10-21',tomorrow)
    records=json.loads(output)['records']
    FactBundle.model_validate_json(output)
    for record in records:
        if 'business_date' in record:assert date.fromisoformat(record['business_date']).isoformat()==tomorrow
        for start,end in [('effective_from','effective_to'),('starts_at','ends_at'),('opens_at','closes_at')]:
            if record.get(start) and record.get(end):
                assert datetime.fromisoformat(record[start])<datetime.fromisoformat(record[end])


def test_full_flow_journeys_cover_located_pairs_and_estimate_distances(full_flow_data):
    bundle=OperationalBundle.model_validate(full_flow_data['operations'])
    places=[r for r in full_flow_data['facts']['records'] if r['kind']=='place' and r.get('longitude') is not None]
    assert {(j.travel.origin_place_id,j.travel.destination_place_id,j.travel.mode) for j in bundle.journeys}=={
        (a['fact_id'],b['fact_id'],mode) for a in places for b in places for mode in ('walking','transit','driving')}
    assert all('synthetic' in j.travel.evidence.source and j.source_ids for j in bundle.journeys)
    script="""import {fullFlowFixtures,journeys} from './playwright.full-flow.fixture.mjs';
const {facts}=fullFlowFixtures();
facts.records.find(r=>r.fact_id==='test:hotel').longitude+=.001;
const missing=facts.records.find(r=>r.fact_id==='test:stage');missing.longitude=null;missing.latitude=null;
process.stdout.write(JSON.stringify(journeys(facts)));"""
    modified=OperationalBundle.model_validate_json(generate(script))
    assert all('test:stage' not in (j.travel.origin_place_id,j.travel.destination_place_id) for j in modified.journeys)
    pair=[j for j in modified.journeys if (j.travel.origin_place_id,j.travel.destination_place_id)==('test:restaurant','test:hotel')]
    expected=6371000*2*math.asin(math.cos(math.radians(34.8))*math.sin(math.radians(.001)/2))*1.3
    assert len(pair)==3
    for journey in pair:
        assert journey.distance_m==pytest.approx(expected)
        speed={'walking':1.2,'transit':5,'driving':8}[journey.travel.mode]
        assert journey.estimated_seconds==journey.travel.upper_seconds==math.ceil(expected/speed)


@pytest.mark.parametrize('offset',range(7))
def test_full_flow_morning_hours_cover_three_dates_on_every_weekday(offset):
    now=datetime(2026,10,25,12,tzinfo=timezone(timedelta(hours=8)))+timedelta(days=offset)
    script="import {fullFlowFixtures,morning} from './playwright.full-flow.fixture.mjs'; process.stdout.write(JSON.stringify({...fullFlowFixtures(new Date(process.argv[1])),morning}))"
    data=json.loads(generate(script,now.isoformat()))
    FactBundle.model_validate(data['facts'])
    records=data['facts']['records'];first=(now+timedelta(days=1)).date()
    rules=[r for r in records if r['kind']=='open_rule' and r['place_id']==data['morning']['fact_id']]
    dates=[(first+timedelta(days=i)).isoformat() for i in range(3)]
    assert [r['business_date'] for r in rules]==dates
    assert all(r['opens_at']==r['business_date']+'T08:00:00+08:00' and r['closes_at']==r['business_date']+'T12:00:00+08:00' for r in rules)
    original=json.loads((ROOT/'backend/tests/fixtures/city_facts.json').read_text().replace('2026-10-21',dates[0]))
    assert all(record in records for record in original['records'])


@pytest.mark.parametrize('with_journeys',[False,True])
def test_browser_server_optional_journey_import(tmp_path,monkeypatch,full_flow_data,with_journeys):
    import uvicorn
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    import planning_model_fixture
    monkeypatch.setattr(planning_model_fixture,'install',lambda:None)
    # The script sets disposable runtime variables; keep them local to this execution.
    monkeypatch.setattr(os,'environ',os.environ.copy())
    for env,key in [('E2E_CITY_FACTS','facts'),('E2E_OBJECT_BINDINGS','bindings'),('E2E_JOURNEYS','operations')]:
        target=tmp_path/(key+'.json');target.write_text(json.dumps(full_flow_data[key]))
        monkeypatch.setenv(env,str(target))
    if not with_journeys:monkeypatch.delenv('E2E_JOURNEYS')
    served=[]
    def check_import(*args,**kwargs):
        with connect() as db:
            imported=load_operations(db)
            assert len(imported.journeys)==(len(full_flow_data['operations']['journeys']) if with_journeys else 0)
            assert db.execute("SELECT fact_id FROM city_object_bindings WHERE kind='scenic'").fetchone()[0]==full_flow_data['scenic']['fact_id']
        served.append(True)
    monkeypatch.setattr(uvicorn,'run',check_import)
    runpy.run_path(str(ROOT/'scripts/browser_server.py'),run_name='__main__')
    assert served==[True]


@pytest.mark.parametrize('case,deep,pace,mode',[
    ('1-desktop',True,'relaxed','transit'),('1-mobile',True,'relaxed','transit'),
    ('2-quick',False,'standard','walking'),('3-switch',False,'compact','transit'),
])
def test_full_flow_inputs_are_feasible(full_flow_client,full_flow_data,case,deep,pace,mode):
    c=full_flow_client;data=full_flow_data
    assert c.post('/api/v1/sessions').status_code==200
    response=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}})
    assert response.status_code==201,response.text
    trip=response.json()
    targets=[('food',data['food'])]
    if deep:targets.insert(0,('scenic',data['scenic']))
    for kind,item in targets:
        detail=c.get(f"/api/city/v1/objects/{kind}/{item['id']}")
        assert detail.status_code==200 and detail.json()['fact_id']==item['fact_id'],detail.text
        added=collect(c,trip,kind,item['id'])
        assert added.status_code==200,added.text
    day=next(r['business_date'] for r in data['facts']['records'] if r['kind']=='open_rule')
    constraints={'business_date':day,'starts_at':day+'T09:00:00+08:00','ends_at':day+'T20:00:00+08:00',
        **data['endpoints'],'party_size':2,'pace':pace,'objective':'less_walking' if mode=='transit' else 'balanced','modes':[mode]}
    def preview(values,version=0):
        result=c.post(base(trip)+'/exploration/preview',json={'request_id':rid(),'expected_version':trip['version'],
            'basket_version':len(targets),'planning_version':version,'constraints':values})
        assert result.status_code==200,result.text
        candidate=result.json()
        assert candidate['status']=='feasible',candidate
        assert candidate['planning']['result']['status']=='feasible'
        expected={data['food']['place_id']}|({data['scenic']['fact_id']} if deep else set())
        assert {a['place_id'] for a in candidate['planning']['result']['itineraries'][0]['activities']}==expected
        return candidate
    version=0
    if case=='3-switch':
        original=preview(constraints|{'pace':'standard','objective':'balanced','modes':['walking']})
        version=original['planning_version']
    candidate=preview(constraints,version)
    assert candidate['planning']['candidate_id']
    assert c.get(base(trip)).json()['route_version']==0
    confirmed=c.post(base(trip)+'/engine/confirm',json={'request_id':rid(),'expected_version':trip['version'],
        'candidate_id':candidate['planning']['candidate_id']})
    assert confirmed.status_code==200,confirmed.text
    assert c.get(base(trip)+'/engine/current').json()['route_version']==1


@pytest.mark.parametrize('days',[2,3])
def test_full_flow_multi_day_inputs_are_feasible(full_flow_client,full_flow_data,days):
    c=full_flow_client;data=full_flow_data
    assert c.post('/api/v1/sessions').status_code==200
    response=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}})
    assert response.status_code==201,response.text
    trip=response.json()
    added=collect(c,trip,'food',data['food']['id'])
    assert added.status_code==200,added.text
    first=date.fromisoformat(next(r['business_date'] for r in data['facts']['records'] if r['kind']=='open_rule'))
    dates=[(first+timedelta(days=i)).isoformat() for i in range(days)]
    constraints={'schema_version':'city-constraints/2','start_date':dates[0],'end_date':dates[-1],'trip_days':days,
        'days':[{'day_id':'day:'+day,'business_date':day,'starts_at':day+'T09:00:00+08:00','ends_at':day+'T20:00:00+08:00',
            'origin_place_id':data['endpoints']['destination_place_id'],'destination_place_id':data['endpoints']['destination_place_id']} for day in dates],
        'party_size':2,'pace':'standard','objective':'balanced','modes':['walking']}
    result=c.post(base(trip)+'/exploration/preview',json={'request_id':rid(),'expected_version':trip['version'],
        'basket_version':1,'planning_version':0,'constraints':constraints})
    assert result.status_code==200,result.text
    candidate=result.json()
    assert candidate['result']['status']=='feasible',candidate
    plan=candidate['result']['itineraries'][0]
    assert plan['schema_version']=='city-itinerary/2'
    assert [d['business_date'] for d in plan['days']]==dates
    assert len(plan['overnights'])==days-1
    assert all(o['source_kind']=='user_place' for o in plan['overnights'])
    assert {a['place_id'] for d in plan['days'] for a in d['itinerary']['activities']}=={data['food']['place_id']}
    assert c.get(base(trip)).json()['route_version']==0
    confirmed=c.post(base(trip)+'/engine/confirm',json={'request_id':rid(),'expected_version':trip['version'],
        'candidate_id':candidate['candidate_id']})
    assert confirmed.status_code==200,confirmed.text
    current=c.get(base(trip)+'/engine/current').json()
    assert current['route_version']==1 and current['itinerary']['schema_version']=='city-itinerary/2'
    assert len(current['itinerary']['days'])==days


@pytest.fixture
def full_flow_trip(full_flow_client):
    c=full_flow_client
    assert c.post('/api/v1/sessions').status_code==200
    response=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}})
    assert response.status_code==201,response.text
    return c,response.json()


def full_flow_collect(c,trip,data,targets=('scenic','food')):
    for key in targets:
        response=collect(c,trip,'poi' if key=='morning' else key,data[key]['id'])
        assert response.status_code==200,response.text


def full_flow_preview(c,trip,data,days=1,start='09:00',end='20:00',**overrides):
    first=date.fromisoformat(next(r['business_date'] for r in data['facts']['records'] if r['kind']=='open_rule'))
    dates=[(first+timedelta(days=i)).isoformat() for i in range(days)]
    constraints={'business_date':dates[0],'starts_at':dates[0]+'T'+start+':00+08:00','ends_at':dates[0]+'T'+end+':00+08:00',
        **data['endpoints'],'party_size':2,'pace':'standard','objective':'balanced','modes':['walking'],'budget_fen':None}
    if days>1:
        constraints={'schema_version':'city-constraints/2','start_date':dates[0],'end_date':dates[-1],'trip_days':days,
            'days':[{'day_id':'day:'+day,'business_date':day,'starts_at':day+'T'+start+':00+08:00','ends_at':day+'T'+end+':00+08:00',
                'origin_place_id':data['endpoints']['destination_place_id'],'destination_place_id':data['endpoints']['destination_place_id']} for day in dates],
            'party_size':2,'pace':'standard','objective':'balanced','modes':['walking']}
    basket=c.get(base(trip)+'/basket').json()
    response=c.post(base(trip)+'/exploration/preview',json={'request_id':rid(),'expected_version':trip['version'],
        'basket_version':basket['version'],'planning_version':0,'constraints':constraints|overrides})
    assert response.status_code==200,response.text
    assert c.get(base(trip)).json()['route_version']==0
    return response.json()


def test_full_flow_short_window_explains_omitted_place(full_flow_trip,full_flow_data):
    c,trip=full_flow_trip;data=full_flow_data
    full_flow_collect(c,trip,data)
    result=full_flow_preview(c,trip,data,end='10:30')
    assert result['status']=='feasible',result
    plans=result['planning']['result']['itineraries']
    expected={data['scenic']['fact_id'],data['food']['place_id']}
    assert plans and all(len(p['activities'])==1 for p in plans)
    for index,plan in enumerate(plans):
        rows=result['unscheduled'][str(index)]
        assert len(rows)==1 and rows[0]['reason_code']=='time_conflict'
        assert rows[0]['reason'] and rows[0]['name'] in (data['scenic']['name'],data['food']['name'])
        assert rows[0]['goal_id'] not in {a['goal_id'] for a in plan['activities']}
        assert {a['place_id'] for a in plan['activities']}<expected
    assert c.get(base(trip)+'/exploration/constraints').json()['result']['unscheduled']==result['unscheduled']


def test_full_flow_afternoon_explains_closed_morning_place(full_flow_trip,full_flow_data):
    c,trip=full_flow_trip;data=full_flow_data
    full_flow_collect(c,trip,data,('morning','food'))
    result=full_flow_preview(c,trip,data,start='14:00')
    assert result['status']=='feasible',result
    plans=result['planning']['result']['itineraries'];assert plans
    for index,plan in enumerate(plans):
        assert {a['place_id'] for a in plan['activities']}=={data['food']['place_id']}
        rows=result['unscheduled'][str(index)]
        assert len(rows)==1
        assert rows[0]['key']=='poi:'+data['morning']['id'] and rows[0]['name']==data['morning']['name']
        assert rows[0]['reason_code']=='closed_in_window'
        assert rows[0]['reason']=='开放时间和你的出行时段对不上'


def test_full_flow_three_days_spread_two_places_and_confirm(full_flow_trip,full_flow_data):
    c,trip=full_flow_trip;data=full_flow_data
    full_flow_collect(c,trip,data)
    result=full_flow_preview(c,trip,data,days=3)
    assert result['result']['status']=='feasible',result
    for plan in result['result']['itineraries']:
        assert len(plan['days'])==3
        assert sum(bool(d['itinerary']['activities']) for d in plan['days'])==2
        assert sorted(a['place_id'] for d in plan['days'] for a in d['itinerary']['activities'])==sorted([data['scenic']['fact_id'],data['food']['place_id']])
    response=c.post(base(trip)+'/engine/confirm',json={'request_id':rid(),'expected_version':trip['version'],
        'candidate_id':result['candidate_id']})
    assert response.status_code==200,response.text
    current=c.get(base(trip)+'/engine/current').json()
    assert current['route_version']==1 and len(current['itinerary']['days'])==3
    assert sum(bool(d['itinerary']['activities']) for d in current['itinerary']['days'])==2


@pytest.mark.parametrize('days',[1,3])
def test_full_flow_deep_budget_returns_result(full_flow_trip,full_flow_data,days):
    c,trip=full_flow_trip;data=full_flow_data
    full_flow_collect(c,trip,data)
    budget={'budget_fen':100000} if days==1 else {'pricing':{'party_size':2,'vehicle_count':1,'driving_basis':'taxi',
        'total_budget_fen':100000,'per_person_budget_fen':None,'per_person_daily':False,'dining_per_person_day_fen':None,'hard_limit':True}}
    result=full_flow_preview(c,trip,data,days=days,pace='relaxed',objective='less_walking',modes=['transit'],**budget)
    # The fixture has no prices: a data gap is a valid result, not a rejected form submission.
    solved=result['planning']['result'] if days==1 else result['result']
    assert solved['issues'] and solved['itineraries']==[]
    saved=c.get(base(trip)+'/exploration/constraints').json()
    if days==1:
        assert result['status']=='unknown' and saved['result']['planning']==result['planning']
        assert saved['constraints']['budget_fen']==100000 and 'pricing' not in saved['constraints']
    else:
        assert saved['result']['result']==solved
        assert saved['constraints']['pricing']['total_budget_fen']==100000 and 'budget_fen' not in saved['constraints']


@pytest.mark.parametrize('days',[1,3])
def test_full_flow_demo_budget_keeps_plans_with_unknown_costs(demo_full_flow_client,full_flow_data,days):
    c=demo_full_flow_client;data=full_flow_data
    assert c.post('/api/v1/sessions').status_code==200
    response=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}})
    assert response.status_code==201,response.text
    trip=response.json()
    full_flow_collect(c,trip,data)
    budget={'budget_fen':100000} if days==1 else {'pricing':{'party_size':2,'vehicle_count':1,'driving_basis':'taxi',
        'total_budget_fen':100000,'per_person_budget_fen':None,'per_person_daily':False,'dining_per_person_day_fen':None,'hard_limit':True}}
    result=full_flow_preview(c,trip,data,days=days,pace='relaxed',objective='less_walking',modes=['transit'],**budget)
    if days==1:
        solved=result['planning']['result']
        assert result['status']=='feasible' and solved['status']=='feasible' and solved['itineraries']
        assert all(plan['cost_unknown'] for plan in solved['itineraries'])
    else:
        solved=result['result']
        assert result['status']=='preview' and solved['status']=='feasible' and solved['itineraries']
        assert all(plan['budget']['status']=='unknown' and plan['cost_unknown'] for plan in solved['itineraries'])
    assert c.get(base(trip)).json()['route_version']==0


def test_full_flow_standby_is_preserved_and_never_scheduled(full_flow_trip,full_flow_data):
    c,trip=full_flow_trip;data=full_flow_data
    full_flow_collect(c,trip,data)
    changed=collect(c,trip,'scenic',data['scenic']['id'],status='standby')
    assert changed.status_code==200,changed.text
    result=full_flow_preview(c,trip,data)
    assert result['status']=='feasible',result
    plans=result['planning']['result']['itineraries'];assert plans
    for plan in plans:
        assert {a['place_id'] for a in plan['activities']}=={data['food']['place_id']}
    assert all(row['key']!='scenic:'+data['scenic']['id'] for rows in result['unscheduled'].values() for row in rows)
    basket=c.get(base(trip)+'/basket').json()
    standby=next(i for i in basket['items'] if i['id']==data['scenic']['id'])
    assert standby['name']==data['scenic']['name'] and standby['status']=='standby'
    assert len(basket['items'])==2
