"""Synthetic city storage/import tests. No production facts or paid services."""
import copy
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.city import facts, state
from app.db import connect, database_path
from app.main import app
from app.memory import storage
from app.memory.session import Session

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent/'fixtures/city_facts.json'


def rid():return str(uuid4())


def full_draft():
    current=storage.now().astimezone(facts.TZ)
    start=current.replace(hour=0,minute=0,second=0,microsecond=0)
    return {'business_date':start.date().isoformat(),'starts_at':start.isoformat(),
            'ends_at':start.replace(hour=23,minute=59).isoformat(),
            'origin_place_id':'test:gate','destination_place_id':'test:hotel',
            'dish_interest_ids':['test:food'],'stops':[
                {'stop_id':'meal','place_id':'test:restaurant','kind':'dining','duration_seconds':1800},
                {'stop_id':'show','place_id':'test:stage','kind':'show','session_id':'test:session','locked':True}]}


@pytest.fixture
def city(client):
    bundle=facts.FactBundle.model_validate_json(FIXTURE.read_text())
    with connect() as db:facts.import_bundle(bundle,db,apply=True)
    assert client.post('/api/v1/sessions').status_code==200
    return client


def create(client,draft=None,request_id=None):
    response=client.post('/api/city/v1/trips',json={'request_id':request_id or rid(),'draft':draft or {}})
    assert response.status_code==201,response.text
    return response.json()


def update(client,t,action,request_id=None,proposal=False):
    url='/api/city/v1/trips/'+t['id']+('/changes' if proposal else '')
    return client.request('POST' if proposal else 'PATCH',url,json={
        'request_id':request_id or rid(),'expected_version':t['version'],'action':action})


def confirm(client,t,proposal,request_id=None):
    return client.post('/api/city/v1/trips/'+t['id']+'/confirm',json={
        'request_id':request_id or rid(),'expected_version':t['version'],'proposal_id':proposal['proposal_id']})


@pytest.mark.parametrize('operation',['create','save','propose'])
def test_multi_day_draft_writes_are_unavailable_without_mutating_old_trip(city,operation):
    from multi_day_helpers import draft
    t=create(city);before=t
    if operation=='create':response=city.post('/api/city/v1/trips',json={'request_id':rid(),'draft':draft()})
    else:response=update(city,t,{'operation':'save_draft','draft':draft()},proposal=operation=='propose')
    assert response.status_code==503,response.text
    assert response.json()['detail']['status']=='unavailable'
    assert city.get('/api/city/v1/trips/'+t['id']).json()==before
    with connect() as db:
        assert db.execute('SELECT count(*) FROM city_trip_states').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM city_proposals').fetchone()[0]==0


@pytest.mark.parametrize('action',[
    {'operation':'save_draft','draft':{}},{'operation':'transition','status':'ACTIVE'},
    {'operation':'skip_stop','stop_id':'future'},
    {'operation':'mark_completed','stop_id':'future','actual_start':'2030-05-01T09:00:00+08:00','actual_end':'2030-05-01T09:10:00+08:00'},
    {'operation':'set_anchor','anchor':{'place_id':'test:gate','confirmed_at':'2030-05-01T09:00:00+08:00'}}])
def test_stored_multi_day_cannot_be_overwritten_by_old_state_operations(city,action):
    from multi_day_helpers import draft
    t=create(city)
    with connect() as db:db.execute('UPDATE city_trip_states SET draft_json=?',(json.dumps(draft()),))
    before=city.get('/api/city/v1/trips/'+t['id']).json()
    for propose in (False,True):
        r=update(city,t,action,proposal=propose)
        assert r.status_code==503 and r.json()['detail']['code']=='MULTI_DAY_UNAVAILABLE'
    assert city.get('/api/city/v1/trips/'+t['id']).json()==before


@pytest.mark.parametrize('tag',['city-draft/999','city-draft/1'])
def test_unknown_draft_tags_do_not_fall_back_on_missing_or_default_tags(city,tag):
    from multi_day_helpers import draft
    response=city.post('/api/city/v1/trips',json={'request_id':rid(),'draft':draft()|{'schema_version':tag}})
    assert response.status_code==422
    t=create(city)
    with connect() as db:db.execute('UPDATE city_trip_states SET draft_json=?',(json.dumps({'schema_version':tag}),))
    r=city.get('/api/city/v1/trips/'+t['id'])
    assert r.status_code==422 and r.json()['detail']['code']=='SCHEMA_UNSUPPORTED'


def test_multi_day_workspace_read_keeps_null_and_missing_without_rewriting_and_refuses_write(city):
    from app.city import exploration_state
    from multi_day_helpers import requirements
    from fastapi import HTTPException
    t=create(city);req=requirements()
    constraints={'schema_version':'city-constraints/2','start_date':req['start_date'],'end_date':req['end_date'],
        'days':[{'business_date':req['start_date'],'origin_place_id':None},{'business_date':req['end_date']}]}
    raw=json.dumps(constraints,indent=3)
    with connect() as db:
        db.execute('INSERT INTO city_exploration_states VALUES (?,?,?,?,?,?)',(t['id'],4,raw,None,0,storage.stamp()))
        assert exploration_state.read(db,t['id'])['constraints']==constraints
        with pytest.raises(HTTPException) as exc:exploration_state.save(db,t['id'],constraints,None,0)
        assert exc.value.status_code==503
        assert db.execute('SELECT constraints_json FROM city_exploration_states').fetchone()[0]==raw
    assert city.get('/api/city/v1/trips/'+t['id']+'/exploration/constraints').json()['constraints']==constraints
    response=city.post('/api/city/v1/trips/'+t['id']+'/exploration/preview',json={
        'request_id':rid(),'expected_version':1,'basket_version':0,'constraints':constraints})
    assert response.status_code==503 and response.json()['detail']['status']=='unavailable'


def test_multi_day_old_state_proposal_cannot_confirm_a_v2_draft(city):
    from multi_day_helpers import draft
    t=create(city);pid=rid()
    with connect() as db:
        db.execute('INSERT INTO city_proposals VALUES (?,?,?,?,?,?,?,0)',(pid,
            db.execute('SELECT session_id FROM city_trip_states').fetchone()[0],t['id'],1,
            json.dumps({'operation':'save_draft','draft':draft()}),state.catalog_revision(facts.records(db)),storage.stamp(storage.now()+timedelta(minutes=15))))
    r=confirm(city,t,{'proposal_id':pid})
    assert r.status_code==503 and r.json()['detail']['code']=='MULTI_DAY_UNAVAILABLE'
    with connect() as db:assert db.execute('SELECT consumed FROM city_proposals').fetchone()[0]==0


@pytest.mark.parametrize('action',[
    {'operation':'close_day','day_id':'day:2030-05-01'},
    {'operation':'mark_completed','day_id':'day:2030-05-01','stop_id':'future',
     'actual_start':'2030-05-01T09:00:00+08:00','actual_end':'2030-05-01T09:15:00+08:00'},
    {'operation':'skip_stop','day_id':'day:2030-05-02','stop_id':'future'}])
def test_explicit_v2_daily_progress_requests_require_day_id_and_remain_unavailable(city,action):
    from app.city.state_schema import MutateCityTripV2
    from pydantic import ValidationError
    t=create(city);body={'schema_version':'city-state/2','request_id':rid(),'expected_version':1,'action':action}
    assert MutateCityTripV2.model_validate(body).action.day_id==action['day_id']
    missing=body|{'action':{k:v for k,v in action.items() if k!='day_id'}}
    with pytest.raises(ValidationError):MutateCityTripV2.model_validate(missing)
    for suffix,method in (('','PATCH'),('/changes','POST')):
        r=city.request(method,'/api/city/v1/trips/'+t['id']+suffix,json=body)
        assert r.status_code==503 and r.json()['detail']['status']=='unavailable'
    assert city.get('/api/city/v1/trips/'+t['id']).json()==t


def test_city_draft_recovers_after_restart_without_changing_legacy(city):
    legacy=city.post('/api/v1/trips',json={'request_id':rid()}).json()
    t=create(city,full_draft())
    assert t['route_version']==0 and not t['readiness']['planning_available']
    with TestClient(app) as restarted:
        restarted.cookies.update(city.cookies)
        assert restarted.get('/api/city/v1/trips/'+t['id']).json()==t
        assert restarted.get('/api/city/v1/trips/current').json()['trip']==t
        assert restarted.get('/api/v1/trips/'+legacy['id']).json()==legacy
        s=restarted.get('/api/v1/sessions/current').json()
        assert s['active_trip_id']==legacy['id'] and s['active_city_trip_id']==t['id']


def test_partial_draft_missing_data_are_explicit_and_unknown_ids_rejected(city):
    t=create(city,{'business_date':'2026-10-21'})
    assert 'origin_place_id' in t['readiness']['missing_fields'] and 'stops' in t['readiness']['missing_fields']
    bad=city.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{'origin_place_id':'invented-place'}})
    assert bad.status_code==422
    draft=full_draft();draft['stops'][0]['place_id']='test:gate'
    assert city.post('/api/city/v1/trips',json={'request_id':rid(),'draft':draft}).status_code==422


def test_cross_owner_csrf_private_cache_and_expiry(city):
    t=create(city,full_draft())
    with TestClient(app) as other:
        other.post('/api/v1/sessions')
        assert other.get('/api/city/v1/trips/'+t['id']).status_code==404
        assert update(other,t,{'operation':'transition','status':'ACTIVE'}).status_code==404
    r=city.patch('/api/city/v1/trips/'+t['id'],headers={'Origin':'https://evil.invalid'},json={
        'request_id':rid(),'expected_version':1,'action':{'operation':'transition','status':'ACTIVE'}})
    assert r.status_code==403
    assert 'no-store' in city.get('/api/city/v1/trips/'+t['id']).headers['cache-control']
    with connect() as db:db.execute('UPDATE conversation_sessions SET expires_at=?',(storage.stamp(storage.now()-timedelta(seconds=1)),))
    assert city.get('/api/city/v1/trips/'+t['id']).status_code==401


def test_idempotency_payload_binding_and_expected_version(city):
    request=rid();t=create(city,full_draft(),request)
    assert create(city,full_draft(),request)==t
    assert city.post('/api/city/v1/trips',json={'request_id':request}).status_code==409
    action={'operation':'save_draft','draft':t['draft']|{'interests':['culture']}}
    req=rid();saved=update(city,t,action,req)
    assert saved.status_code==200 and saved.json()['version']==2
    assert update(city,t,action,req).json()==saved.json()
    assert update(city,t,action).status_code==409
    assert update(city,saved.json(),{'operation':'save_draft','draft':saved.json()['draft']},req).status_code==409


def test_locked_change_requires_confirmation_and_rechecks_fact_revision(city):
    t=create(city,full_draft());draft=copy.deepcopy(t['draft']);draft['stops']=draft['stops'][:1]
    action={'operation':'save_draft','draft':draft}
    assert update(city,t,action).json()['detail']['code']=='CONFIRMATION_REQUIRED'
    proposal=update(city,t,action,proposal=True).json()
    assert city.get('/api/city/v1/trips/'+t['id']).json()==t
    request=rid();done=confirm(city,t,proposal,request)
    assert done.status_code==200 and len(done.json()['draft']['stops'])==1
    assert confirm(city,t,proposal,request).json()==done.json()
    assert confirm(city,done.json(),proposal).status_code==409

    t=create(city,full_draft());p=update(city,t,action,proposal=True).json()
    with connect() as db:
        record=facts.records(db)['food','test:food'].model_dump(mode='json')|{'version':2,'name':'[测试]修订菜品'}
        facts.import_bundle(facts.FactBundle.model_validate({'records':[record]}),db,apply=True)
    assert confirm(city,t,p).status_code==409
    assert city.get('/api/city/v1/trips/'+t['id']).json()['version']==1


def test_expired_candidate_and_history_off_cannot_resurrect_city_proposals(city):
    t=create(city,full_draft());body={'operation':'save_draft','draft':t['draft']|{'interests':['photo']}}
    request=rid();proposal=update(city,t,body,request,True).json()
    with connect() as db:db.execute('UPDATE city_proposals SET expires_at=?',(storage.stamp(storage.now()-timedelta(seconds=1)),))
    assert confirm(city,t,proposal).status_code==409
    second=update(city,t,body,proposal=True).json()
    assert city.put('/api/v1/memory/history',json={'enabled':False}).status_code==200
    assert confirm(city,t,second).status_code==409
    assert update(city,t,body,request,True).json()['status']=='proposal_cleared'


def test_progress_anchor_and_important_skip_keep_completed_prefix(city,monkeypatch):
    # Wall-clock midnight previously moved the 40-minute completion into yesterday.
    # Pin one coherent business-day clock; production validation remains unchanged.
    from app.memory import session
    moment=storage.now().astimezone(facts.TZ).replace(hour=12,minute=0,second=0,microsecond=0)
    for module in (storage,state,session):monkeypatch.setattr(module,'now',lambda:moment)
    t=create(city,full_draft());active=update(city,t,{'operation':'transition','status':'ACTIVE'}).json()
    now=storage.now()
    action={'operation':'mark_completed','stop_id':'meal','actual_start':storage.stamp(now-timedelta(minutes=40)),
            'actual_end':storage.stamp(now-timedelta(minutes=10))}
    done=update(city,active,action)
    assert done.status_code==200,done.text
    done=done.json();assert done['draft']['stops'][0]['progress']=='completed'
    anchor=update(city,done,{'operation':'set_anchor','anchor':{'place_id':'test:restaurant','confirmed_at':storage.stamp()}})
    assert anchor.status_code==200,anchor.text
    anchored=anchor.json()
    assert anchored['anchor']['place_id']=='test:restaurant'
    assert update(city,anchored,{'operation':'skip_stop','stop_id':'show'}).status_code==409
    p=update(city,anchored,{'operation':'skip_stop','stop_id':'show'},proposal=True).json()
    skipped=confirm(city,anchored,p).json()
    assert skipped['draft']['stops'][0]==done['draft']['stops'][0]
    assert skipped['draft']['stops'][1]['progress']=='skipped'
    bad=copy.deepcopy(skipped['draft']);bad['stops'][0]['actual_end']=bad['starts_at']
    assert update(city,skipped,{'operation':'save_draft','draft':bad}).status_code==422


def test_progress_cannot_be_injected_or_completed_out_of_order(city):
    t=create(city,full_draft());active=update(city,t,{'operation':'transition','status':'ACTIVE'}).json()
    at=storage.stamp()
    assert update(city,active,{'operation':'mark_completed','stop_id':'show','actual_start':at,'actual_end':at}).status_code==422
    assert update(city,active,{'operation':'set_anchor','anchor':{'place_id':'test:gate','confirmed_at':storage.stamp(storage.now()+timedelta(minutes=1))}}).status_code==422
    response=city.post('/api/city/v1/trips',json={'request_id':rid(),'owner_user_id':'someone'})
    assert response.status_code==422


def test_two_concurrent_updates_only_commit_one_version(city):
    t=create(city,full_draft())
    with connect() as db:
        row=db.execute('SELECT * FROM conversation_sessions').fetchone();s=Session(row['id'],row['guest_token_hash'])
    from app.city.state_schema import MutateCityTrip
    def write(interest):
        body=MutateCityTrip.model_validate({'request_id':rid(),'expected_version':1,
              'action':{'operation':'save_draft','draft':t['draft']|{'interests':[interest]}}})
        try:return state.mutate(s,t['id'],body)['version']
        except Exception as e:return getattr(e,'status_code',None)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(write,['food','culture']))
    assert sorted(results)==[2,409]


def test_transaction_failure_leaves_draft_event_and_receipt_unchanged(city,monkeypatch):
    t=create(city,full_draft())
    def fail(*args):raise sqlite3.OperationalError('injected event failure')
    monkeypatch.setattr(state,'event',fail)
    assert update(city,t,{'operation':'save_draft','draft':t['draft']|{'interests':['photo']}}).status_code==503
    assert city.get('/api/city/v1/trips/'+t['id']).json()==t
    with connect() as db:
        assert db.execute('SELECT count(*) FROM city_receipts').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM city_trip_events').fetchone()[0]==1


def test_export_delete_restore_includes_all_new_visitor_objects_but_preserves_public_facts(city,tmp_path):
    t=create(city,full_draft());update(city,t,{'operation':'save_draft','draft':t['draft']},proposal=True)
    exported=city.get('/api/v1/memory/export').json()['city_trips'][0]
    assert exported['id']==t['id'] and len(exported['proposals'])==1 and exported['events'] and exported['receipts']
    cookies=dict(city.cookies);backup=tmp_path/'backup.sqlite3'
    with sqlite3.connect(database_path()) as src,sqlite3.connect(backup) as dst:src.backup(dst)
    assert city.post('/api/v1/privacy/delete-request',json={'request_id':rid(),'confirmed':True}).status_code==200
    for table in ('city_trip_states','city_proposals','city_receipts','city_trip_events'):
        with connect() as db:assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
    with connect() as db:assert db.execute('SELECT count(*) FROM city_facts').fetchone()[0]==14
    with sqlite3.connect(backup) as src,sqlite3.connect(database_path()) as dst:src.backup(dst)
    city.cookies.update(cookies)
    assert city.get('/api/city/v1/trips/'+t['id']).status_code==401
    storage.maintenance()
    with connect() as db:assert db.execute('SELECT count(*) FROM city_trip_states').fetchone()[0]==0


def test_finished_city_ttl_is_not_extended_by_archiving(city):
    t=create(city)
    cancelled=update(city,t,{'operation':'transition','status':'CANCELLED'}).json()
    with connect() as db:
        ended=storage.stamp(storage.now()-timedelta(hours=71))
        db.execute('UPDATE city_trip_states SET ended_at=? WHERE id=?',(ended,t['id']))
    archived=update(city,cancelled,{'operation':'transition','status':'ARCHIVED'})
    assert archived.status_code==200
    with connect() as db:
        assert db.execute('SELECT ended_at FROM city_trip_states WHERE id=?',(t['id'],)).fetchone()[0]==ended
        db.execute('UPDATE city_trip_states SET ended_at=?',(storage.stamp(storage.now()-timedelta(hours=73)),))
    assert city.get('/api/city/v1/trips/'+t['id']).status_code==404
    storage.maintenance()
    with connect() as db:assert db.execute('SELECT count(*) FROM city_receipts').fetchone()[0]==0


def test_missing_ledger_blocks_city_access_without_hiding_public_facts(city):
    t=create(city);storage.ledger_path().unlink()
    assert city.get('/api/city/v1/trips/'+t['id']).status_code==503
    assert city.get('/api/city/v1/facts?kind=place').status_code==200


def test_fact_import_atomic_versioned_idempotent_and_dependency_checked(city):
    original=json.loads(FIXTURE.read_text())
    with connect() as db:
        report=facts.import_bundle(facts.FactBundle.model_validate(original),db,apply=True)
        assert report['changed']==0
        update_record=original['records'][1]|{'version':2,'name':'[测试]新名称'}
        facts.import_bundle(facts.FactBundle(records=[update_record]),db,apply=True)
    with connect() as db:
        before=db.execute('SELECT count(*) FROM city_fact_revisions').fetchone()[0]
        invalid=original['records'][2]|{'version':2,'parent_place_id':'missing'}
        with pytest.raises(ValueError):facts.import_bundle(facts.FactBundle(records=[invalid]),db,apply=True)
        assert db.execute('SELECT count(*) FROM city_fact_revisions').fetchone()[0]==before
        with pytest.raises(ValueError):facts.import_bundle(facts.FactBundle(records=[original['records'][1]]),db,apply=True)


def test_supply_candidate_unknown_end_cancelled_and_stale_are_not_ready(city):
    with connect() as db:
        items=facts.records(db)
        supply=items['supply','test:supply'].model_dump(mode='json')|{'validation_status':'unverified'}
        assert not facts.status(facts.Supply.model_validate(supply),items)['ready']
        s=items['show_session','test:session'].model_dump(mode='json')|{'ends_at':None}
        assert 'SHOW_END_UNKNOWN' in facts.status(facts.ShowSession.model_validate(s),items)['issues']
        source=items['source','test:source'].model_dump(mode='json')|{'effective_to':'2021-01-01T00:00:00+08:00'}
        items['source','test:source']=facts.Source.model_validate(source)
        assert not facts.status(items['supply','test:supply'],items)['ready']
        cancelled=items['show_session','test:session'].model_dump(mode='json')|{'business_status':'cancelled'}
        assert 'CANCELLED' in facts.status(facts.ShowSession.model_validate(cancelled),items)['issues']


def test_cli_dry_run_does_not_create_database_then_apply_and_revision_conflict(city,tmp_path):
    target=tmp_path/'catalog.sqlite3'
    command=[sys.executable,str(ROOT/'scripts/import_city_facts.py'),str(FIXTURE),'--database',str(target)]
    dry=subprocess.run(command,capture_output=True,text=True)
    assert dry.returncode==0,dry.stderr
    assert not target.exists() and json.loads(dry.stdout)['status']=='dry_run'
    applied=subprocess.run(command+['--apply'],capture_output=True,text=True)
    assert applied.returncode==0,applied.stderr
    with sqlite3.connect(target) as db:assert db.execute('SELECT count(*) FROM city_facts').fetchone()[0]==14
    broken=json.loads(FIXTURE.read_text());broken['records'][1]['name']='different without version bump'
    path=tmp_path/'invalid.json';path.write_text(json.dumps(broken))
    result=subprocess.run([sys.executable,str(ROOT/'scripts/import_city_facts.py'),str(path),'--database',str(target),'--apply'],capture_output=True,text=True)
    assert result.returncode==1
    with sqlite3.connect(target) as db:assert db.execute('SELECT count(*) FROM city_fact_revisions').fetchone()[0]==14


def test_restore_cli_accepts_pre_city_backup_without_modifying_source(city,tmp_path):
    legacy=city.post('/api/v1/trips',json={'request_id':rid()}).json()
    source=tmp_path/'pre-city.sqlite3';destination=tmp_path/'restored.sqlite3'
    with sqlite3.connect(database_path()) as src,sqlite3.connect(source) as dst:src.backup(dst)
    with sqlite3.connect(source) as db:
        for table in ('city_receipts','city_proposals','city_trip_events','city_trip_states','city_fact_revisions','city_facts'):
            db.execute(f'DROP TABLE {table}')
        db.execute('ALTER TABLE conversation_sessions DROP COLUMN active_city_trip_id')
    before=source.read_bytes()
    result=subprocess.run([sys.executable,str(ROOT/'scripts/restore_memory_backup.py'),str(source),'--destination',str(destination),
                           '--ledger',str(storage.ledger_path())],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert source.read_bytes()==before
    with sqlite3.connect(destination) as db:
        assert db.execute('SELECT id FROM trip_states').fetchone()[0]==legacy['id']
        assert db.execute('SELECT count(*) FROM city_trip_states').fetchone()[0]==0


@pytest.mark.parametrize('mutation', ['missing_method','wrong_session_date','unknown_food','parent_cycle','stage_type'])
def test_fact_import_rejects_bad_verification_dates_and_entity_relations(city,mutation):
    bundle=json.loads(FIXTURE.read_text())
    if mutation=='missing_method':bundle['records'][1].pop('verification_method')
    elif mutation=='wrong_session_date':bundle['records'][8]['business_date']='2026-10-22'
    elif mutation=='unknown_food':bundle['records'][11]['food_id']='missing-food'
    elif mutation=='parent_cycle':bundle['records'][1]['parent_place_id']='test:stage'
    else:bundle['records'][3]['place_type']='poi'
    changed_index={'missing_method':1,'wrong_session_date':8,'unknown_food':11,'parent_cycle':1,'stage_type':3}[mutation]
    bundle['records'][changed_index]['version']=2
    with connect() as db:
        before=db.execute('SELECT count(*) FROM city_fact_revisions').fetchone()[0]
        with pytest.raises(ValueError):facts.import_bundle(facts.FactBundle.model_validate(bundle),db,apply=True)
        assert db.execute('SELECT count(*) FROM city_fact_revisions').fetchone()[0]==before


def test_unverified_candidates_can_be_imported_but_internal_rights_do_not_publish(city):
    candidate={'kind':'food','fact_id':'candidate','name':'待核验菜品','rights_status':'permitted'}
    internal={'kind':'food','fact_id':'internal','name':'内部候选','rights_status':'internal_only'}
    with connect() as db:
        report=facts.import_bundle(facts.FactBundle(records=[candidate,internal]),db,apply=True)
        assert not any(r['ready'] for r in report['records'])
    response=city.get('/api/city/v1/facts?kind=food').json()['items']
    assert {r['record']['fact_id'] for r in response}=={'test:food','candidate'}
    assert city.post('/api/city/v1/facts',json={'records':[]}).status_code==405
