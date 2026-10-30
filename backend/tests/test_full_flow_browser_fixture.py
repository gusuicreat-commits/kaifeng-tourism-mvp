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
    script="import {fullFlowFixtures,scenic,food,endpoints} from './playwright.full-flow.fixture.mjs'; process.stdout.write(JSON.stringify({...fullFlowFixtures(),scenic,food,endpoints}))"
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
