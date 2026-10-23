"""Controlled station/garden records test wiring only, never real Kaifeng evidence."""
import copy
import json
from pathlib import Path
from uuid import uuid4
import pytest
from app.db import connect
from app.city import facts
from app.city.engine.models import OperationalBundle
from app.city.engine.storage import import_operations

PATH='/api/city/v1/sample-assessments/kaifeng-station-qingming'
BODY={'origin_fact_id':'test:station','destination_fact_id':'test:garden','business_date':'2030-05-01',
      'starts_at':'2030-05-01T09:00:00+08:00','ends_at':'2030-05-01T12:00:00+08:00'}

@pytest.fixture
def sample(client):
    fixture=json.loads((Path(__file__).parent/'fixtures/engine_synthetic.json').read_text())
    template=fixture['facts']['records'][0]
    meta={k:v for k,v in template.items() if k in ('version','validation_status','rights_status','verified_at','reviewer','verification_method','effective_from','effective_to')}
    meta['is_demo']=False # Controlled production-gate emulation ONLY in pytest tmp DB.
    source={**template,'is_demo':False}
    def place(identity,typ,parent=None):
        return {**meta,'source_ids':[source['fact_id']],'kind':'place','fact_id':identity,'name':'[合成测试]'+identity,
                'place_type':typ,'parent_place_id':parent,'longitude':114.3,'latitude':34.8,'coordinate_system':'GCJ02'}
    traffic_source=source|{'fact_id':'traffic:source'}
    records=[source,traffic_source,place('test:station','transport'),place('test:exit','entrance','test:station'),
             place('test:garden','scenic'),place('test:entry','entrance','test:garden')]
    for owner,gate,direction in [('test:station','test:exit','exit'),('test:garden','test:entry','entry')]:
        records.append({**meta,'source_ids':[source['fact_id']],'kind':'entrance','fact_id':'relation:'+gate,
                        'place_id':owner,'entrance_place_id':gate,'direction':direction})
    records.append({**meta,'source_ids':[source['fact_id']],'kind':'open_rule','fact_id':'test:open',
        'place_id':'test:garden','business_date':BODY['business_date'],'opens_at':'2030-05-01T08:00:00+08:00',
        'closes_at':'2030-05-01T20:00:00+08:00','last_entry_at':'2030-05-01T19:00:00+08:00',
        'reentry':'forbidden','reservation':'not_required'})
    ev={'evidence_id':'test:survey','revision':'1','kind':'verified','source':'SYNTHETIC TEST ONLY',
        'checked_at':'2020-01-01T00:00:00+08:00','valid_from':'2020-01-01T00:00:00+08:00','valid_until':'2035-01-01T00:00:00+08:00'}
    journey={'source_ids':['traffic:source'],'journey_id':'test:journey','revision':1,'travel':{'origin_place_id':'test:exit',
        'destination_place_id':'test:entry','mode':'walking','lower_seconds':900,'upper_seconds':1200,
        'evidence':ev,'path_status':'verified','path_ref':'test:survey-path','coordinate_system':'GCJ02'},
        'distance_m':1000,'time_basis':'survey_envelope','cost_lower_fen':0,'cost_upper_fen':0,
        'boundaries':[{'park_id':'test:garden','gate_place_id':'test:entry','direction':'entry','seconds':300,'cost_upper_fen':0,'evidence':ev}]}
    with connect() as db:
        facts.import_bundle(facts.FactBundle(records=records),db,apply=True)
        import_operations(db,OperationalBundle(journeys=[journey]),facts.records(db),apply=True)
    client.post('/api/v1/sessions')
    return client,records,journey


def test_empty_fact_database_stops_before_engine(client):
    client.post('/api/v1/sessions')
    result=client.post(PATH,json=BODY).json()
    assert result['status']=='needs_data'
    assert {'ORIGIN_FACT_MISSING','DESTINATION_FACT_MISSING'}<={i['code'] for i in result['issues']}
    assert result['engine_status'] is None


def test_transfer_uses_existing_ortools_and_writes_no_route(sample):
    c,records,j=sample
    result=c.post(PATH,json=BODY).json()
    assert result['status']=='transfer_preview',result
    assert 'OR-Tools CP-SAT' in result['engine_metrics']['algorithm']
    assert result['matched_origin_entrance_id']=='test:exit'
    assert result['transfer_preview']['itineraries'][0]['navigation_certified'] is False
    assert result['writes']==[]
    with connect() as db:
        assert db.execute('SELECT count(*) FROM city_engine_candidates').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM city_engine_routes').fetchone()[0]==0
    # The exact normalized requirements also pass existing authorized preview and confirmation.
    trip=c.post('/api/city/v1/trips',json={'request_id':str(uuid4()),'draft':{}}).json()
    preview=c.post(f"/api/city/v1/trips/{trip['id']}/engine/preview",json={'request_id':str(uuid4()),'expected_version':trip['version'],'requirements':result['requirements']}).json()
    assert preview['status']=='preview',preview
    confirm=c.post(f"/api/city/v1/trips/{trip['id']}/engine/confirm",json={'request_id':str(uuid4()),'expected_version':preview['base_version'],'candidate_id':preview['candidate_id']})
    assert confirm.status_code==200,confirm.text
    assert confirm.json()['route_version']==1


@pytest.mark.parametrize('mutation,expected',[
 ('missing_exit','ORIGIN_ENTRANCE_UNRESOLVED'),('wrong_direction','DESTINATION_ENTRANCE_UNRESOLVED'),
 ('unknown_rights','ORIGIN_FACT_NOT_READY'),('expired_source','ORIGIN_FACT_NOT_READY'),
 ('missing_rule','OPEN_RULE_MISSING_OR_AMBIGUOUS'),('unknown_access','ACCESS_RULE_NEEDS_CONFIRMATION'),
 ('closed','HARD_CONSTRAINT_CONFLICT'),('missing_travel','TRAVEL_RECORD_MISSING'),
 ('stale_travel','TRAVEL_RECORD_NOT_VALID'),('point_eta','TRAVEL_RECORD_NOT_VALID'),
 ('missing_boundary','TRAVEL_RECORD_NOT_VALID'),
 ('missing_rights_source','TRAVEL_RECORD_NOT_VALID'),('traffic_rights_unknown','TRAVEL_RECORD_NOT_VALID')])
def test_gaps_fail_closed(sample,mutation,expected):
    c,records,j=sample
    with connect() as db:
        if mutation in ('missing_exit','wrong_direction','missing_rule'):
            if mutation=='missing_exit':db.execute("DELETE FROM city_facts WHERE kind='entrance' AND id='relation:test:exit'")
            elif mutation=='missing_rule':db.execute("DELETE FROM city_facts WHERE kind='open_rule'")
            else:
                record=next(r for r in records if r['fact_id']=='relation:test:entry')|{'direction':'exit','version':2}
                facts.import_bundle(facts.FactBundle(records=[record]),db,apply=True)
        elif mutation in ('unknown_rights','expired_source','unknown_access','closed'):
            identity={'unknown_rights':'test:station','expired_source':records[0]['fact_id'],'unknown_access':'test:open','closed':'test:open'}[mutation]
            record=copy.deepcopy(next(r for r in records if r['fact_id']==identity));record['version']=2
            if mutation=='unknown_rights':record['rights_status']='unknown'
            elif mutation=='expired_source':record['effective_to']='2021-01-01T00:00:00+08:00'
            elif mutation=='unknown_access':record['reservation']='unknown'
            else:record['opens_at']='2030-05-01T13:00:00+08:00'
            facts.import_bundle(facts.FactBundle(records=[record]),db,apply=True)
        elif mutation=='traffic_rights_unknown':
            record=next(r for r in records if r['fact_id']=='traffic:source')|{'version':2,'rights_status':'unknown'}
            facts.import_bundle(facts.FactBundle(records=[record]),db,apply=True)
        elif mutation=='missing_travel':db.execute('DELETE FROM city_engine_operations')
        else:
            changed=copy.deepcopy(j);changed['revision']=2
            if mutation=='stale_travel':changed['travel']['evidence']['valid_until']='2030-05-01T10:00:00+08:00'
            elif mutation=='point_eta':changed['time_basis']='provider_estimate'
            elif mutation=='missing_rights_source':changed['source_ids']=[]
            else:changed['boundaries']=[]
            import_operations(db,OperationalBundle(journeys=[changed]),facts.records(db),apply=True)
    result=c.post(PATH,json=BODY).json()
    assert result['status']==('infeasible' if mutation=='closed' else 'needs_data'),result
    assert expected in [i['code'] for i in result['issues']],result
    assert result['transfer_preview'] is None


def test_utc_inputs_normalize_to_shanghai_and_midnight_requires_confirmation(sample):
    c,_,_=sample
    utc=BODY|{'starts_at':'2030-05-01T01:00:00Z','ends_at':'2030-05-01T04:00:00Z'}
    result=c.post(PATH,json=utc).json()
    assert result['status']=='transfer_preview'
    assert result['time_input']['normalized_starts_at']=='2030-05-01T09:00:00+08:00'
    early=BODY|{'starts_at':'2030-05-01T02:04:00+08:00','ends_at':'2030-05-01T07:03:00+08:00'}
    result=c.post(PATH,json=early).json()
    assert 'EARLY_TIME_CONFIRMATION_REQUIRED' in [i['code'] for i in result['issues']]
    confirmed=c.post(PATH,json=early|{'early_time_confirmed':True}).json()
    assert 'EARLY_TIME_CONFIRMATION_REQUIRED' not in [i['code'] for i in confirmed['issues']]
    assert confirmed['status']=='infeasible' # The synthetic garden is still closed at this arrival time.


@pytest.mark.parametrize('change',[
 {'starts_at':'2030-05-01T09:00:00'}, {'ends_at':'2030-05-02T01:00:00+08:00'},
 {'ends_at':'2030-05-01T08:00:00+08:00'}])
def test_invalid_time_input_rejected(client,change):
    client.post('/api/v1/sessions')
    assert client.post(PATH,json=BODY|change).status_code==422

@pytest.mark.parametrize('mutation',['missing_rights_source','point_eta','closed'])
def test_formal_engine_cannot_bypass_sample_admission(sample,mutation):
    c,records,j=sample
    with connect() as db:
        if mutation=='closed':
            rule=next(r for r in records if r['fact_id']=='test:open')|{'version':2,'opens_at':'2030-05-01T13:00:00+08:00'}
            facts.import_bundle(facts.FactBundle(records=[rule]),db,apply=True)
        else:
            changed=copy.deepcopy(j);changed['revision']=2
            if mutation=='missing_rights_source':changed['source_ids']=[]
            else:changed['time_basis']='provider_estimate'
            import_operations(db,OperationalBundle(journeys=[changed]),facts.records(db),apply=True)
    trip=c.post('/api/city/v1/trips',json={'request_id':str(uuid4()),'draft':{}}).json()
    requirements={k:v for k,v in BODY.items() if k in ('business_date','starts_at','ends_at')}
    requirements.update(origin_place_id='test:exit',destination_place_id='test:entry',goals=[],modes=['walking'])
    result=c.post(f"/api/city/v1/trips/{trip['id']}/engine/preview",json={'request_id':str(uuid4()),'expected_version':1,'requirements':requirements}).json()
    assert result['result']['status']==('infeasible' if mutation=='closed' else 'needs_data'),result
    assert not result['result']['itineraries']
