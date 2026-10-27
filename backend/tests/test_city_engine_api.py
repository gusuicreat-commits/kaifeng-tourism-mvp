"""Production gates exercised with synthetic records in pytest's isolated temporary database."""
import copy,json,sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect
from app.city import facts
from app.city.engine import service
from app.city.engine.models import OperationalBundle
from app.city.engine.storage import import_operations

FIXTURE=Path(__file__).parent/'fixtures/engine_synthetic.json'
def rid():return str(uuid4())


@pytest.fixture
def engine(client,monkeypatch):
    d=json.loads(FIXTURE.read_text())
    # Emulate verified production responses ONLY in the temporary test DB. Never export these as real evidence.
    for r in d['facts']['records']:r['is_demo']=False
    for j in d['operations']['journeys']:
        j['travel']['evidence']['kind']='verified';j['travel']['path_status']='verified';j['time_basis']='survey_envelope'
    for c in d['operations']['costs']:c['evidence']['kind']='verified'
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(d['facts']),db,apply=True)
        import_operations(db,OperationalBundle.model_validate(d['operations']),facts.records(db),apply=True)
    client.post('/api/v1/sessions')
    t=client.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    return client,t,d


def preview(e,requirements=None,request_id=None,version=None):
    c,t,d=e
    return c.post(f"/api/city/v1/trips/{t['id']}/engine/preview",json={'request_id':request_id or rid(),
          'expected_version':version or t['version'],'requirements':requirements or d['requirements']})


def confirm(e,p,request_id=None,version=None,alternative_index=0):
    c,t,d=e
    return c.post(f"/api/city/v1/trips/{t['id']}/engine/confirm",json={'request_id':request_id or rid(),
         'expected_version':version or p['base_version'],'candidate_id':p['candidate_id'],'alternative_index':alternative_index})


def current(e):return e[0].get(f"/api/city/v1/trips/{e[1]['id']}/engine/current")


def active(e,monkeypatch,at,place):
    c,t,d=e
    monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat(at))
    with connect() as db:
        db.execute("UPDATE city_trip_states SET status='ACTIVE',anchor_json=? WHERE id=?",
                   (json.dumps({'place_id':place,'confirmed_at':at,'source':'manual_known_place'}),t['id']))
    t['version']=c.get('/api/city/v1/trips/'+t['id']).json()['version']


def replan(e,req=None,**extra):
    c,t,d=e
    return c.post(f"/api/city/v1/trips/{t['id']}/engine/replan-preview",json={'request_id':rid(),
            'expected_version':t['version'],'requirements':req or d['requirements'],**extra})


def seed_multi_route(e,route_version=1):
    from multi_day_helpers import requirements,itinerary,result,draft
    from app.city.engine.models import MultiDayRequirements,MultiDayItinerary,SolveResultV2
    from app.city.state_schema import CityDraftV2
    from app.memory.storage import stamp,now
    req=MultiDayRequirements.model_validate(requirements()).model_dump_json()
    plan=MultiDayItinerary.model_validate(itinerary()).model_dump_json()
    candidate=rid();trip_id=e[1]['id']
    with connect() as db:
        db.execute('UPDATE city_trip_states SET draft_json=?,route_version=? WHERE id=?',
                   (CityDraftV2.model_validate(draft()).model_dump_json(),route_version,trip_id))
        db.execute('INSERT INTO city_engine_routes VALUES (?,?,?,?,?,?,?)',(trip_id,route_version,1,req,plan,'synthetic-snapshot',stamp()))
        db.execute('INSERT INTO city_engine_candidates VALUES (?,?,?,?,?,?,?,?,0)',
            (candidate,trip_id,1,'synthetic-snapshot',req,SolveResultV2.model_validate(result()).model_dump_json(),
             json.dumps({'days':{'day:2030-05-01':{'frozen':False}}}),stamp(now()+timedelta(minutes=15))))
    return candidate,json.loads(req),json.loads(plan)


@pytest.mark.parametrize('endpoint',['preview','replan-preview'])
def test_multi_day_engine_requests_are_explicitly_unavailable_and_never_solved(engine,monkeypatch,endpoint):
    from multi_day_helpers import requirements
    from app.city.engine.models import MultiDayRequirements
    from app.city.engine.tools import replan_trip
    c,t,_=engine
    monkeypatch.setattr(service,'plan_trip',lambda *a,**kw:pytest.fail('must not run single-day solver'))
    before=c.get('/api/city/v1/trips/'+t['id']).json()
    body={'request_id':rid(),'expected_version':1,'requirements':requirements()}
    if endpoint=='replan-preview':body.update(scope='current_and_future',day_id='day:2030-05-01')
    response=c.post(f"/api/city/v1/trips/{t['id']}/engine/{endpoint}",json=body)
    assert response.status_code==503 and response.json()['detail']['status']=='unavailable'
    assert c.get('/api/city/v1/trips/'+t['id']).json()==before
    with connect() as db:
        assert db.execute('SELECT count(*) FROM city_engine_candidates').fetchone()[0]==0
        ctx=service.context(db)
    result=replan_trip(MultiDayRequirements.model_validate(requirements()),ctx)
    assert result.status=='unavailable' and not result.itineraries


def test_multi_day_current_history_export_preserve_whole_snapshot_and_ownership(engine):
    c,t,_=engine;_,req,plan=seed_multi_route(engine)
    with connect() as db:
        before=[dict(r) for r in db.execute('SELECT * FROM city_engine_routes')]
    for suffix in ('current','versions/1'):
        r=c.get(f"/api/city/v1/trips/{t['id']}/engine/{suffix}")
        assert r.status_code==200,r.text
        assert r.json()['itinerary']==plan and len(r.json()['itinerary']['days'])==2
        assert r.json()['stale'] and r.json()['issues'][0]['code']=='MULTI_DAY_VALIDATION_UNAVAILABLE'
    snapshot=c.get('/api/city/v1/trips/'+t['id']).json()
    assert snapshot['schema_version']=='city-state/2' and len(snapshot['draft']['days'])==2
    assert c.get('/api/city/v1/trips').json()['items'][0]==snapshot
    assert c.get('/api/city/v1/trips/current').json()['trip']==snapshot
    export=c.get('/api/v1/memory/export').json()['city_trips'][0]
    assert export['draft']==snapshot['draft']
    assert json.loads(export['planning_engine']['versions'][0]['requirements_json'])==req
    assert json.loads(export['planning_engine']['versions'][0]['itinerary_json'])==plan
    assert len(json.loads(export['planning_engine']['candidates'][0]['result_json'])['itineraries'][0]['days'])==2
    with connect() as db:assert [dict(r) for r in db.execute('SELECT * FROM city_engine_routes')]==before
    with TestClient(app) as other:
        other.post('/api/v1/sessions')
        assert other.get(f"/api/city/v1/trips/{t['id']}/engine/current").status_code==404
        assert other.get('/api/v1/memory/export').json()['city_trips']==[]


def test_multi_day_engine_candidate_cannot_be_confirmed_through_single_day_trip(engine):
    from app.memory.storage import stamp,now
    from multi_day_helpers import requirements,result
    c,t,_=engine;candidate=rid()
    with connect() as db:
        db.execute('INSERT INTO city_engine_candidates VALUES (?,?,?,?,?,?,?,?,0)',
            (candidate,t['id'],1,'synthetic',json.dumps(requirements()),json.dumps(result()),'{}',stamp(now()+timedelta(minutes=15))))
    r=confirm(engine,{'candidate_id':candidate,'base_version':1})
    assert r.status_code==503 and r.json()['detail']['code']=='MULTI_DAY_UNAVAILABLE'
    with connect() as db:
        assert db.execute('SELECT consumed FROM city_engine_candidates').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM city_engine_routes').fetchone()[0]==0


@pytest.mark.parametrize('endpoint',['preview','replan-preview','confirm','assistant/context','exploration/preview'])
def test_old_write_and_context_consumers_reject_stored_multi_day_without_projection(engine,monkeypatch,endpoint):
    c,t,d=engine;candidate,_,_=seed_multi_route(engine)
    monkeypatch.setattr(service,'plan_trip',lambda *a,**kw:pytest.fail('must not solve first day'))
    path='/api/city/v1/trips/'+t['id']
    if endpoint=='assistant/context':response=c.get(path+'/'+endpoint)
    elif endpoint=='exploration/preview':
        response=c.post(path+'/'+endpoint,json={'request_id':rid(),'expected_version':1,'basket_version':0,
            'constraints':{k:v for k,v in d['requirements'].items() if k!='goals'}})
    else:
        body={'request_id':rid(),'expected_version':1}
        body.update({'candidate_id':candidate} if endpoint=='confirm' else {'requirements':d['requirements']})
        response=c.post(path+'/engine/'+endpoint,json=body)
    assert response.status_code==503,response.text
    assert response.json()['detail']['code']=='MULTI_DAY_UNAVAILABLE'
    with connect() as db:assert db.execute('SELECT route_version FROM city_trip_states').fetchone()[0]==1


def test_legacy_schema_reads_and_hashes_are_unchanged_through_full_closed_loop(engine,monkeypatch):
    from app.city import versions
    from app.city.state_schema import CityDraft
    from app.city.engine.models import Requirements,Itinerary,SolveResult
    from app.memory.storage import digest,encoded
    e=engine;c,t,d=e
    raw=json.dumps(d['requirements'],indent=3)
    assert versions.read_requirements(raw).model_dump(mode='json')==Requirements.model_validate_json(raw).model_dump(mode='json')
    request={'request_id':rid(),'expected_version':1,'requirements':d['requirements']}
    p=c.post(f"/api/city/v1/trips/{t['id']}/engine/preview",json=request).json()
    assert confirm(e,p).status_code==200
    with connect() as db:
        route=dict(service.route_row(db,t['id']));state_row=dict(db.execute('SELECT * FROM city_trip_states').fetchone())
        candidate=dict(db.execute('SELECT * FROM city_engine_candidates').fetchone())
        hashes=[tuple(r) for r in db.execute('SELECT scope,body_hash,result_json FROM city_receipts ORDER BY scope')]
        expected=digest(encoded(__import__('app.city.engine.models',fromlist=['PreviewRequest']).PreviewRequest.model_validate(request).model_dump(mode='json')))
        assert db.execute('SELECT body_hash FROM city_receipts WHERE request_id=?',(request['request_id'],)).fetchone()[0]==expected
    for reader,model,raw in ((versions.read_draft,CityDraft,state_row['draft_json']),
                             (versions.read_itinerary,Itinerary,route['itinerary_json']),
                             (versions.read_requirements,Requirements,route['requirements_json']),
                             (versions.read_result,SolveResult,candidate['result_json'])):
        assert reader(raw).model_dump(mode='json')==model.model_validate_json(raw).model_dump(mode='json')
    assert current(e).json()['itinerary']==Itinerary.model_validate_json(route['itinerary_json']).model_dump(mode='json')
    c.get('/api/v1/memory/export')
    with connect() as db:
        assert dict(service.route_row(db,t['id']))==route
        assert dict(db.execute('SELECT * FROM city_trip_states').fetchone())==state_row
        assert [tuple(r) for r in db.execute('SELECT scope,body_hash,result_json FROM city_receipts ORDER BY scope')]==hashes
    active(e,monkeypatch,'2030-05-01T08:30:00+08:00','origin')
    p2=replan(e).json();assert p2['status']=='preview'
    assert confirm(e,p2).json()['route_version']==2
    assert c.get(f"/api/city/v1/trips/{t['id']}/engine/versions/1").json()['itinerary']==json.loads(route['itinerary_json'])


@pytest.mark.parametrize('column,tag',[('requirements_json','city-engine/99'),('itinerary_json','city-itinerary/99')])
def test_unknown_saved_route_schema_is_rejected_on_current_history_and_export(engine,column,tag):
    c,t,_=engine;seed_multi_route(engine)
    with connect() as db:db.execute(f'UPDATE city_engine_routes SET {column}=?',(json.dumps({'schema_version':tag}),))
    for url in (f"/api/city/v1/trips/{t['id']}/engine/current",f"/api/city/v1/trips/{t['id']}/engine/versions/1",'/api/v1/memory/export'):
        r=c.get(url);assert r.status_code==422 and r.json()['detail']['code']=='SCHEMA_UNSUPPORTED'


def test_multi_day_export_delete_and_backup_restore_cover_all_nested_objects(engine,tmp_path):
    from app.db import database_path
    from app.memory import storage
    from multi_day_helpers import requirements
    c,t,_=engine;seed_multi_route(engine)
    req=requirements();constraints={k:req[k] for k in ('start_date','end_date','days','goals','overnights')}
    constraints['schema_version']='city-constraints/2'
    with connect() as db:
        db.execute('INSERT INTO city_exploration_states VALUES (?,?,?,?,?,?)',(t['id'],1,json.dumps(constraints),None,0,storage.stamp()))
        db.execute('INSERT INTO city_proposals VALUES (?,?,?,?,?,?,?,0)',(rid(),
            db.execute('SELECT session_id FROM city_trip_states').fetchone()[0],t['id'],1,
            json.dumps({'operation':'save_draft','draft':json.loads(db.execute('SELECT draft_json FROM city_trip_states').fetchone()[0])}),
            'synthetic',storage.stamp(storage.now()+timedelta(minutes=15))))
    export=c.get('/api/v1/memory/export').json()['city_trips'][0]
    assert export['draft']['overnights'][0]['place_id']=='hotel'
    assert export['exploration']['constraints']==constraints
    assert len(export['proposals'][0]['action']['draft']['days'])==2
    cookies=dict(c.cookies);backup=tmp_path/'before-delete.sqlite3'
    with sqlite3.connect(database_path()) as src,sqlite3.connect(backup) as dst:src.backup(dst)
    assert c.post('/api/v1/privacy/delete-request',json={'request_id':rid(),'confirmed':True}).status_code==200
    tables=('city_trip_states','city_engine_routes','city_engine_candidates','city_exploration_states','city_proposals','city_receipts','city_trip_events')
    with connect() as db:
        for table in tables:assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM city_facts').fetchone()[0]>0
    with sqlite3.connect(backup) as src,sqlite3.connect(database_path()) as dst:src.backup(dst)
    c.cookies.update(cookies)
    assert c.get('/api/city/v1/trips/'+t['id']).status_code==401
    storage.maintenance()
    with connect() as db:
        for table in tables:assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0


@pytest.mark.parametrize('value',[None,[], 'invalid', {'schema_version':'city-engine/99'}])
def test_engine_schema_dispatch_rejects_invalid_http_shapes(engine,value):
    c,t,_=engine
    for endpoint in ('preview','replan-preview'):
        r=c.post(f"/api/city/v1/trips/{t['id']}/engine/{endpoint}",json={
            'request_id':rid(),'expected_version':1,'requirements':value})
        assert r.status_code==422


def test_preview_does_not_commit_confirm_is_idempotent_and_history_is_retained(engine):
    e=engine;request_id=rid();p=preview(e,request_id=request_id).json()
    assert p['status']=='preview' and current(e).json()['itinerary'] is None
    assert preview(e,request_id=request_id).json()==p
    commit_id=rid();result=confirm(e,p,request_id=commit_id)
    assert result.status_code==200,result.text
    assert result.json()['route_version']==1 and result.json()['version']==2
    assert confirm(e,p,request_id=commit_id).json()==result.json()
    with connect() as db:assert db.execute('SELECT count(*) FROM city_engine_routes').fetchone()[0]==1
    assert not current(e).json()['stale']
    exported=e[0].get('/api/v1/memory/export').json()['city_trips'][0]['planning_engine']
    assert len(exported['versions'])==1


def test_objective_alternatives_keep_confirmation_index_and_replan_changes(engine,monkeypatch):
    from app.city.engine.models import Itinerary
    from app.city.engine.validation import compare_itineraries
    e=engine;req=e[2]['requirements']|{'modes':['walking','driving'],'objective':'less_walking'}
    enriched=[]
    def enrich(ctx,requirements):
        enriched.append(ctx);return ctx
    monkeypatch.setattr(service.live_travel,'enrich',enrich)
    p=preview(e,req).json();plans=p['result']['itineraries']
    assert p['status']=='preview' and len(plans)>1
    assert plans[0]['objectives'][0]=='less_walking'
    assert len(enriched)==1
    index=len(plans)-1
    assert confirm(e,p,alternative_index=index).status_code==200
    saved=current(e).json()['itinerary'];assert saved==plans[index]
    assert len(enriched)==1
    active(e,monkeypatch,'2030-05-01T08:30:00+08:00','origin')
    p2=replan(e,req).json();plans2=p2['result']['itineraries']
    assert p2['status']=='preview' and len(plans2)>1
    assert len(enriched)==2
    assert p2['alternative_changes']==[compare_itineraries(Itinerary.model_validate(saved),Itinerary.model_validate(i)) for i in plans2]
    assert p2['changes']==p2['alternative_changes'][0]
    index2=len(plans2)-1
    assert confirm(e,p2,alternative_index=index2).status_code==200
    assert current(e).json()['itinerary']==plans2[index2]
    assert len(enriched)==2


def test_identity_and_csrf_cannot_be_bypassed(engine):
    e=engine;p=preview(e).json();c,t,d=e
    with TestClient(app) as other:
        other.post('/api/v1/sessions')
        assert other.get(f"/api/city/v1/trips/{t['id']}/engine/current").status_code==404
        assert other.post(f"/api/city/v1/trips/{t['id']}/engine/confirm",json={'request_id':rid(),
          'candidate_id':p['candidate_id'],'expected_version':1}).status_code==404
    assert c.post(f"/api/city/v1/trips/{t['id']}/engine/preview",headers={'Origin':'https://foreign.invalid'},json={}).status_code==403
    invalid=d['requirements']|{'owner':'someone','facts':[]}
    assert preview(e,invalid).status_code==422


def test_two_previews_cannot_overwrite_concurrent_commit(engine):
    e=engine;p=preview(e).json();q=preview(e).json()
    assert confirm(e,p).status_code==200
    assert confirm(e,q).status_code==409
    assert current(e).json()['route_version']==1


def test_actual_concurrent_confirms_commit_one_version(engine):
    e=engine;p=preview(e).json();q=preview(e).json()
    from app.memory.session import Session
    with connect() as db:
        r=db.execute('SELECT * FROM conversation_sessions').fetchone();s=Session(r['id'],r['guest_token_hash'],None)
    from app.city.engine.models import ConfirmRequest
    def run(candidate):
        try:return service.confirm_itinerary(s,e[1]['id'],ConfirmRequest(request_id=rid(),expected_version=1,candidate_id=candidate['candidate_id']))['route_version']
        except Exception as exc:return getattr(exc,'status_code',500)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,[p,q]))
    assert sorted(results)==[1,409]


def test_failed_confirmation_rolls_back_route_state_event_and_receipt(engine,monkeypatch):
    e=engine;p=preview(e).json()
    def fail(*args):raise sqlite3.OperationalError('injected failure')
    monkeypatch.setattr(service.state,'event',fail)
    assert confirm(e,p).status_code==503
    assert current(e).json()['itinerary'] is None
    with connect() as db:
        assert db.execute('SELECT version FROM city_trip_states').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM city_engine_routes').fetchone()[0]==0
        assert db.execute('SELECT consumed FROM city_engine_candidates').fetchone()[0]==0


@pytest.mark.parametrize('mutation',['cancel','expiry','candidate_expiry','operation_revision'])
def test_stale_candidate_rejected_and_old_route_retained(engine,mutation):
    e=engine;p=preview(e).json()
    with connect() as db:
        if mutation in ('cancel','expiry'):
            item=facts.records(db)['show_session','s1'].model_dump(mode='json');item['version']=2
            item.update({'business_status':'cancelled'} if mutation=='cancel' else {'effective_to':'2021-01-01T00:00:00+08:00'})
            facts.import_bundle(facts.FactBundle(records=[item]),db,apply=True)
        elif mutation=='candidate_expiry':db.execute("UPDATE city_engine_candidates SET expires_at='2020-01-01T00:00:00+00:00'")
        else:
            row=db.execute("SELECT * FROM city_engine_operations WHERE kind='cost' LIMIT 1").fetchone()
            item=json.loads(row['content']);item['revision']=2;item['upper_fen']+=10
            import_operations(db,OperationalBundle(costs=[item]),facts.records(db),apply=True)
    assert confirm(e,p).status_code==409
    assert current(e).json()['itinerary'] is None


def test_cancelled_fact_marks_committed_history_stale_even_if_replanning_fails(engine):
    e=engine;p=preview(e).json();assert confirm(e,p).status_code==200
    with connect() as db:
        item=facts.records(db)['show_session','s1'].model_dump(mode='json')|{'version':2,'business_status':'cancelled'}
        facts.import_bundle(facts.FactBundle(records=[item]),db,apply=True)
    read=current(e).json();assert read['stale'] and read['route_version']==1
    assert preview(e,version=2).json()['status']=='needs_data'
    assert current(e).json()['route_version']==1


def test_completed_prefix_and_locked_show_survive_adding_a_restaurant(engine,monkeypatch):
    e=engine;p=preview(e).json();assert confirm(e,p).status_code==200
    c,t,d=e
    with connect() as db:
        row=db.execute('SELECT * FROM city_trip_states').fetchone();draft=json.loads(row['draft_json'])
        stop=draft['stops'][0];assert stop['stop_id']=='visit'
        stop.update(progress='completed',actual_start='2030-05-01T09:00:00+08:00',actual_end='2030-05-01T09:30:00+08:00')
        db.execute('UPDATE city_trip_states SET draft_json=?',(json.dumps(draft),))
    active(e,monkeypatch,'2030-05-01T09:40:00+08:00','museum')
    req=copy.deepcopy(d['requirements']);req['goals']=req['goals'][1:]
    req['goals'].append({'goal_id':'rest','target_kind':'place','target_id':'garden','kind':'rest','duration_seconds':600})
    r=replan(e,req);assert r.status_code==200,r.text
    p2=r.json();assert p2['status']=='preview',p2
    plan=p2['result']['itineraries'][0]
    assert plan['completed_prefix'][0]==stop
    assert next(a for a in plan['activities'] if a['goal_id']=='show')['session_id']=='s1'
    assert confirm(e,p2).status_code==200
    assert current(e).json()['route_version']==2
    with connect() as db:assert json.loads(db.execute('SELECT draft_json FROM city_trip_states').fetchone()[0])['stops'][0]==stop


def test_locked_goal_cannot_be_silently_removed(engine,monkeypatch):
    e=engine;p=preview(e).json();confirm(e,p)
    active(e,monkeypatch,'2030-05-01T09:00:00+08:00','origin')
    req=copy.deepcopy(e[2]['requirements']);req['goals']=[g for g in req['goals'] if g['goal_id']!='show']
    assert replan(e,req).status_code==409
    assert current(e).json()['route_version']==1


def test_ongoing_activity_defaults_to_preserved_and_can_be_explicitly_ended(engine,monkeypatch):
    e=engine;p=preview(e).json();confirm(e,p)
    itinerary=current(e).json()['itinerary'];start=next(a['starts_at'] for a in itinerary['activities'] if a['goal_id']=='visit')
    active(e,monkeypatch,(datetime.fromisoformat(start)+timedelta(minutes=5)).isoformat(),'museum')
    r=replan(e);assert r.status_code==200,r.text
    assert r.json()['result']['itineraries'][0]['ongoing']['stop_id']=='visit'
    explicit=replan(e,ongoing={'stop_id':'visit','remaining_seconds':600},end_ongoing=True)
    assert explicit.status_code==200,explicit.text
    result=confirm(e,explicit.json());assert result.status_code==200,result.text
    with connect() as db:
        first=json.loads(db.execute('SELECT draft_json FROM city_trip_states').fetchone()[0])['stops'][0]
        assert first['stop_id']=='visit' and first['progress']=='skipped'


def test_replan_failure_and_stale_anchor_preserve_original(engine,monkeypatch):
    e=engine;p=preview(e).json();confirm(e,p)
    active(e,monkeypatch,'2030-05-01T19:55:00+08:00','origin')
    r=replan(e);assert r.status_code==200,r.text
    assert r.json()['status']!='preview' and current(e).json()['route_version']==1
    monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2030-05-01T20:15:00+08:00'))
    assert replan(e).status_code==422


def test_restore_historical_version_revalidates_and_creates_new_version(engine,monkeypatch):
    e=engine;p=preview(e).json();assert confirm(e,p).status_code==200
    req=copy.deepcopy(e[2]['requirements']);req['goals'].append({'goal_id':'garden','target_kind':'place','target_id':'garden','required':False})
    p2=preview(e,req,version=2).json();assert confirm(e,p2).status_code==200
    active(e,monkeypatch,'2030-05-01T08:30:00+08:00','origin')
    r=replan(e,restore_route_version=1);assert r.status_code==200,r.text
    assert confirm(e,r.json()).status_code==200
    assert current(e).json()['route_version']==3
    assert e[0].get(f"/api/city/v1/trips/{e[1]['id']}/engine/versions/1").json()['route_version']==1


def test_export_delete_and_history_off_cover_engine_children(engine):
    e=engine;p=preview(e).json();confirm(e,p);q=preview(e,version=2).json()
    c,t,d=e
    c.put('/api/v1/memory/history',json={'enabled':False})
    assert confirm(e,q).status_code==409
    exported=c.get('/api/v1/memory/export').json()
    assert len(exported['city_trips'][0]['planning_engine']['versions'])==1
    response=c.post('/api/v1/privacy/delete-request',json={'request_id':rid(),'confirmed':True});assert response.status_code==200
    with connect() as db:
        for table in ('city_engine_routes','city_engine_candidates','city_receipts'):
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM city_engine_operations').fetchone()[0]>0


def test_facts_changed_during_solve_rejects_preview_without_state_writes(engine,monkeypatch):
    e=engine;original=service.plan_trip
    def race(req,ctx):
        r=original(req,ctx)
        with connect() as db:
            item=facts.records(db)['place','museum'].model_dump(mode='json')|{'version':2,'name':'[TEST] renamed'}
            facts.import_bundle(facts.FactBundle(records=[item]),db,apply=True)
        return r
    monkeypatch.setattr(service,'plan_trip',race)
    assert preview(e).status_code==409
    with connect() as db:
        assert db.execute('SELECT count(*) FROM city_engine_candidates').fetchone()[0]==0
        assert db.execute('SELECT version FROM city_trip_states').fetchone()[0]==1


def test_same_id_different_payload_conflicts_and_expired_departure_cannot_confirm(engine,monkeypatch):
    e=engine;request_id=rid();p=preview(e,request_id=request_id).json()
    req=e[2]['requirements']|{'pace':'compact'}
    assert preview(e,req,request_id=request_id).status_code==409
    monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2030-05-01T09:01:00+08:00'))
    assert confirm(e,p).status_code==409
    assert current(e).json()['itinerary'] is None


def test_solve_failure_does_not_write_formal_version(engine,monkeypatch):
    e=engine;p=preview(e).json();confirm(e,p)
    from app.city.engine.models import SolveResult
    def failure(req,ctx):return SolveResult(status='unavailable',computed_at=ctx.assessed_at)
    monkeypatch.setattr(service,'plan_trip',failure)
    assert preview(e,version=2).json()['status']=='unavailable'
    assert current(e).json()['route_version']==1


def test_deleted_session_cannot_restore_engine_route_from_old_backup(engine,tmp_path):
    from app.memory import storage
    from app.db import database_path
    e=engine;p=preview(e).json();confirm(e,p)
    backup=tmp_path/'backup.sqlite3'
    with sqlite3.connect(database_path()) as src,sqlite3.connect(backup) as dst:src.backup(dst)
    e[0].post('/api/v1/privacy/delete-request',json={'request_id':rid(),'confirmed':True})
    with sqlite3.connect(backup) as src,sqlite3.connect(database_path()) as dst:src.backup(dst)
    storage.maintenance()
    with connect() as db:
        assert db.execute('SELECT count(*) FROM city_engine_routes').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM city_engine_candidates').fetchone()[0]==0


def test_hard_budget_replanning_requires_expenses_and_does_not_reset_budget(engine,monkeypatch):
    e=engine;e[2]['requirements']['budget_fen']=2000
    p=preview(e).json();assert confirm(e,p).status_code==200
    active(e,monkeypatch,'2030-05-01T08:30:00+08:00','origin')
    missing=replan(e).json()
    assert missing['status']=='needs_data' and missing['result']['issues'][0]['code']=='SPENT_COST_REQUIRED'
    over=replan(e,spent_cost_fen=500,spend_as_of='2030-05-01T08:30:00+08:00').json()
    assert over['status']=='infeasible'
    valid=replan(e,spent_cost_fen=0,spend_as_of='2030-05-01T08:30:00+08:00').json()
    assert valid['status']=='preview'
    assert valid['result']['itineraries'][0]['known_cost_upper_fen']<=2000
    assert confirm(e,valid).status_code==200


def test_operation_cli_dry_run_and_atomic_versions(engine,tmp_path):
    import subprocess,sys
    from app.db import database_path
    e=engine;target=tmp_path/'copy.sqlite3'
    with sqlite3.connect(database_path()) as src,sqlite3.connect(target) as dst:src.backup(dst)
    with sqlite3.connect(target) as db:
        record=json.loads(db.execute("SELECT content FROM city_engine_operations WHERE kind='cost' LIMIT 1").fetchone()[0])
    record['revision']=2;record['upper_fen']+=100
    package=tmp_path/'operations.json';package.write_text(json.dumps({'costs':[record]}))
    command=[sys.executable,str(Path(__file__).resolve().parents[2]/'scripts/import_city_operations.py'),str(package),'--database',str(target)]
    before=target.read_bytes();dry=subprocess.run(command,capture_output=True,text=True)
    assert dry.returncode==0,dry.stderr
    assert json.loads(dry.stdout)['changed']==1 and target.read_bytes()==before
    applied=subprocess.run(command+['--apply'],capture_output=True,text=True);assert applied.returncode==0,applied.stderr
    repeat=subprocess.run(command+['--apply'],capture_output=True,text=True);assert json.loads(repeat.stdout)['changed']==0
    package.write_text(json.dumps({'costs':[record|{'revision':3,'place_id':'unknown'}]}))
    bad=subprocess.run(command+['--apply'],capture_output=True,text=True);assert bad.returncode==1
    with sqlite3.connect(target) as db:assert db.execute("SELECT max(revision) FROM city_engine_operations WHERE kind='cost'").fetchone()[0]==2


def test_remove_optional_future_place_and_less_walking_do_not_touch_history(engine,monkeypatch):
    e=engine;req=copy.deepcopy(e[2]['requirements']);req['goals'][0]['required']=False
    p=preview(e,req).json();assert confirm(e,p).status_code==200
    active(e,monkeypatch,'2030-05-01T08:30:00+08:00','origin')
    req['goals']=req['goals'][1:];req.update(modes=['walking','driving'],objective='less_walking')
    r=replan(e,req).json();assert r['status']=='preview'
    assert r['changes']['removed']==['visit']
    assert r['result']['itineraries'][0]['walking_distance_m']==0
    assert current(e).json()['route_version']==1
    assert confirm(e,r).status_code==200


def test_fixed_food_goal_keeps_supply_dependency_after_replanning(engine,monkeypatch):
    e=engine;p=preview(e).json();assert confirm(e,p).status_code==200
    active(e,monkeypatch,'2030-05-01T08:30:00+08:00','origin')
    q=replan(e).json();assert confirm(e,q).status_code==200
    with connect() as db:
        saved=json.loads(db.execute('SELECT requirements_json FROM city_engine_routes ORDER BY route_version DESC LIMIT 1').fetchone()[0])
        assert next(g for g in saved['goals'] if g['goal_id']=='lunch')['target_kind']=='food'
        supply=facts.records(db)['supply','supply'].model_dump(mode='json')|{'version':2,'business_status':'cancelled'}
        facts.import_bundle(facts.FactBundle(records=[supply]),db,apply=True)
    assert current(e).json()['stale']
