"""Whole-trip writes against isolated synthetic dated facts, without network providers."""
import copy,json,sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import pytest
from app.db import connect
from app.city import facts,state
from app.city.engine import service
from app.city.engine.models import MultiDayRequirements
from app.city.state_schema import CityDraftV2
from test_city_engine_api import engine,preview,confirm,current,rid
from multi_day_helpers import requirements


@pytest.fixture
def multi_engine(engine,monkeypatch):
    c,t,data=engine;req=requirements(3)
    for d in req['days']:d['ends_at']=d['business_date']+'T20:00:00+08:00'
    req['goals']=[{'goal_id':'visit:'+d['business_date'],'target_kind':'place','target_id':'museum',
          'duration_seconds':600,'required':d['business_date']!='2030-05-02','allowed_dates':[d['business_date']],'fixed_date':d['business_date']} for d in req['days']]
    req['alternatives']=1
    records=[]
    for offset in (1,2):
        for r in data['facts']['records']:
            if r['kind']!='open_rule':continue
            changed=copy.deepcopy(r);changed['fact_id']+=f':day{offset}'
            changed['business_date']=(datetime.fromisoformat(r['business_date'])+timedelta(days=offset)).date().isoformat()
            for key in ('opens_at','closes_at','last_entry_at'):
                if changed.get(key):changed[key]=(datetime.fromisoformat(changed[key])+timedelta(days=offset)).isoformat()
            records.append(changed)
    with connect() as db:facts.import_bundle(facts.FactBundle(records=tuple(records)),db,apply=True)
    monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2030-04-30T08:00:00+08:00'))
    return engine,req


def saved_rows():
    with connect() as db:
        return [[dict(r) for r in db.execute('SELECT * FROM '+table)] for table in
            ('city_trip_states','city_engine_routes','city_engine_candidates','city_receipts','city_trip_events')]


def start_today(e,monkeypatch,at='2030-05-02T09:05:00+08:00',place='hotel',complete_past=True):
    moment=datetime.fromisoformat(at);monkeypatch.setattr(service,'now',lambda:moment)
    monkeypatch.setattr(state,'now',lambda:moment)
    with connect() as db:
        row=service.route_row(db,e[1]['id']);draft=CityDraftV2.model_validate_json(db.execute('SELECT draft_json FROM city_trip_states WHERE id=?',(e[1]['id'],)).fetchone()[0])
        days=[]
        for d in draft.days:
            if complete_past and d.business_date<moment.date():
                stops=tuple(s.model_copy(update={'progress':'completed','progress_source':'user_reported',
                    'actual_start':d.starts_at+timedelta(minutes=10),'actual_end':d.starts_at+timedelta(minutes=20)}) for s in d.stops)
                d=d.model_copy(update={'stops':stops,'status':'closed'})
            days.append(d)
        db.execute("UPDATE city_trip_states SET status='ACTIVE',draft_json=?,anchor_json=? WHERE id=?",
            (draft.model_copy(update={'days':tuple(days)}).model_dump_json(),json.dumps({'place_id':place,'confirmed_at':at,'source':'manual_known_place'}),e[1]['id']))
        e[1]['version']=db.execute('SELECT version FROM city_trip_states WHERE id=?',(e[1]['id'],)).fetchone()[0]
    return moment


def remaining(e,req,**extra):
    return e[0].post(f"/api/city/v1/trips/{e[1]['id']}/engine/replan-preview",json={
        'request_id':rid(),'expected_version':e[1]['version'],'requirements':req,**extra})


def committed(multi_engine):
    e,req=multi_engine;p=preview(e,req);assert p.status_code==200,p.text
    assert p.json()['status']=='preview',p.text
    r=confirm(e,p.json());assert r.status_code==200,r.text
    e[1]['version']=r.json()['version']
    return e,req,r.json()


def test_whole_preview_confirmation_idempotency_read_and_overnight(multi_engine):
    e,req=multi_engine;before=current(e).json();draft=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()
    request_id=rid();p=preview(e,req,request_id=request_id);assert p.status_code==200,p.text
    assert p.json()['status']=='preview',p.text
    assert preview(e,req,request_id=request_id).json()==p.json()
    assert current(e).json()==before
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==draft
    cid=rid();r=confirm(e,p.json(),request_id=cid);assert r.status_code==200,r.text
    assert r.json()['route_version']==1 and r.json()['version']==2
    assert confirm(e,p.json(),request_id=cid).json()==r.json()
    plan=r.json()['itinerary'];assert len(plan['days'])==3
    for left,right in zip(plan['days'],plan['days'][1:]):
        assert left['requirements']['destination_place_id']==right['requirements']['origin_place_id']=='hotel'
    read=current(e);assert read.status_code==200 and not read.json()['stale'],read.text
    assert read.json()['itinerary']==plan
    with connect() as db:assert db.execute('SELECT count(*) FROM city_engine_routes').fetchone()[0]==1


def test_confirm_concurrency_one_whole_version_and_failure_rolls_back(multi_engine,monkeypatch):
    e,req=multi_engine;p=preview(e,req).json();before=saved_rows()
    original=state.event
    def fail(*a,**kw):raise sqlite3.IntegrityError('injected event failure')
    monkeypatch.setattr(state,'event',fail)
    assert confirm(e,p).status_code==503
    assert saved_rows()==before
    monkeypatch.setattr(state,'event',original)
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:confirm(e,p),range(2)))
    assert sorted(r.status_code for r in responses)==[200,409]
    with connect() as db:
        assert db.execute('SELECT count(*) FROM city_engine_routes').fetchone()[0]==1
        assert db.execute('SELECT route_version FROM city_trip_states').fetchone()[0]==1


def test_candidate_progress_and_fact_conflicts_preserve_formal(multi_engine):
    e,req=multi_engine;p=preview(e,req).json()
    with connect() as db:db.execute('UPDATE city_trip_states SET version=version+1 WHERE id=?',(e[1]['id'],))
    before=saved_rows();r=confirm(e,p,version=2)
    assert r.status_code==409 and saved_rows()==before
    p=preview(e,req,version=2).json()
    with connect() as db:db.execute("DELETE FROM city_engine_operations WHERE kind='journey'")
    before=saved_rows();r=confirm(e,p,version=2)
    assert r.status_code==409 and saved_rows()==before


def test_today_replan_freezes_yesterday_and_locks_tomorrow(multi_engine,monkeypatch):
    e,req,old=committed(multi_engine);start_today(e,monkeypatch)
    before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()
    p=remaining(e,req,day_id='day:2030-05-02');assert p.status_code==200,p.text
    assert p.json()['status']=='preview',p.text
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before
    plans=p.json()['result']['itineraries']
    for plan in plans:
        assert plan['days'][0]==old['itinerary']['days'][0]
        assert plan['days'][2]==old['itinerary']['days'][2]
        assert plan['days'][1]['requirements']['starts_at']=='2030-05-02T09:07:00+08:00'
        assert plan['days'][1]['original_requirements']['starts_at']=='2030-05-02T09:00:00+08:00'
    r=confirm(e,p.json());assert r.status_code==200,r.text
    assert r.json()['route_version']==2
    after=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()['draft']
    assert after['days'][0]==before['draft']['days'][0] and after['days'][2]==before['draft']['days'][2]
    assert [d['frozen'] for d in current(e).json()['days']]==[True,False,False]
    assert [d['locked'] for d in current(e).json()['days']]==[False,False,True]


def test_today_ongoing_preserves_identity_and_no_duplicate(multi_engine,monkeypatch):
    e,req,old=committed(multi_engine)
    a=old['itinerary']['days'][1]['itinerary']['activities'][0]
    moment=datetime.fromisoformat(a['starts_at'])+timedelta(minutes=1)
    start_today(e,monkeypatch,moment.isoformat(),'museum')
    p=remaining(e,req,ongoing={'stop_id':a['goal_id'],'remaining_seconds':1200})
    assert p.status_code==200 and p.json()['status']=='preview',p.text
    current_plan=p.json()['result']['itineraries'][0]['days'][1]['itinerary']
    assert current_plan['ongoing']['stop_id']==a['goal_id'] and not current_plan['activities']
    r=confirm(e,p.json());assert r.status_code==200,r.text
    assert r.json()['route_version']==2
    draft=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()['draft']
    assert [s['stop_id'] for s in draft['days'][1]['stops']]==[a['goal_id']]


@pytest.mark.parametrize('scope',['current_and_future','selected_future_day'])
def test_unsupported_scopes_explicitly_unavailable(multi_engine,monkeypatch,scope):
    e,req,_=committed(multi_engine);start_today(e,monkeypatch)
    before=saved_rows();r=remaining(e,req,scope=scope,day_id='day:2030-05-03')
    assert r.status_code==503 and r.json()['detail']['status']=='unavailable'
    assert saved_rows()==before


def test_replan_rejects_future_mutation_historical_pending_and_wrong_day(multi_engine,monkeypatch):
    e,req,_=committed(multi_engine);start_today(e,monkeypatch,complete_past=False)
    before=saved_rows();r=remaining(e,req);assert r.status_code==422 and r.json()['detail']['code']=='HISTORY_PENDING'
    assert saved_rows()==before
    start_today(e,monkeypatch)
    r=remaining(e,req,day_id='day:2030-05-03');assert r.status_code==422
    modified=copy.deepcopy(req);modified['goals'][-1]['duration_seconds']=2000
    assert remaining(e,modified).status_code==409
    modified=copy.deepcopy(req);modified['days'][-1]['origin_place_id']='origin'
    modified['overnights'][-1]['place_id']='origin'
    assert remaining(e,modified).status_code in (409,422)
    r=remaining(e,req,restore_route_version=999);assert r.status_code==404


def test_restore_today_is_a_new_whole_version_not_historical_overwrite(multi_engine,monkeypatch):
    e,req,first=committed(multi_engine);start_today(e,monkeypatch)
    changed=copy.deepcopy(req)
    changed['goals'].append({'goal_id':'new-rest','target_kind':'place','target_id':'hotel','kind':'rest',
        'duration_seconds':600,'allowed_dates':['2030-05-02'],'fixed_date':'2030-05-02'})
    p=remaining(e,changed);assert p.json()['status']=='preview',p.text
    r=confirm(e,p.json());assert r.status_code==200,r.text
    e[1]['version']=r.json()['version']
    p=remaining(e,changed,restore_route_version=1);assert p.json()['status']=='preview',p.text
    restored=p.json()['result']['itineraries'][0]
    assert 'new-rest' not in {a['goal_id'] for a in restored['days'][1]['itinerary']['activities']}
    assert restored['days'][0]==first['itinerary']['days'][0] and restored['days'][2]==first['itinerary']['days'][2]
    r=confirm(e,p.json());assert r.status_code==200 and r.json()['route_version']==3,r.text
    assert e[0].get(f"/api/city/v1/trips/{e[1]['id']}/engine/versions/1").json()['itinerary']==first['itinerary']


def test_export_delete_covers_all_daily_children_and_candidates(multi_engine):
    e,req,old=committed(multi_engine);p=preview(e,req,version=2);assert p.status_code==200,p.text
    c=e[0];export=c.get('/api/v1/memory/export').json()['city_trips'][0]
    assert len(export['draft']['days'])==3
    assert all(len(json.loads(r['itinerary_json'])['days'])==3 for r in export['planning_engine']['versions'])
    assert all(len(json.loads(r['result_json'])['itineraries'][0]['days'])==3 for r in export['planning_engine']['candidates'])
    response=c.post('/api/v1/privacy/delete-request',json={'request_id':rid(),'confirmed':True});assert response.status_code==200,response.text
    with connect() as db:
        for table in ('city_trip_states','city_engine_routes','city_engine_candidates','city_trip_events','city_proposals','city_receipts'):
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0


def test_overnight_evidence_is_server_owned_and_hotel_binding_is_unavailable(multi_engine):
    e,req=multi_engine;forged=copy.deepcopy(req)
    for o in forged['overnights']:o.update(place_evidence='verified',stay_evidence='verified',evidence_refs=['client:verified'])
    p=preview(e,forged);assert p.status_code==200 and p.json()['status']=='preview',p.text
    for o in p.json()['result']['itineraries'][0]['overnights']:
        assert o['place_evidence']=='verified' and o['stay_evidence']=='unknown' and o['evidence_refs']==['place:hotel']
    forged['overnights'][0]['source_kind']='selected_hotel'
    before=saved_rows();r=preview(e,forged)
    assert r.status_code==503 and r.json()['detail']['status']=='unavailable'
    assert saved_rows()==before


def test_read_reports_invalid_endpoint_even_for_an_empty_transfer_day(multi_engine):
    e,req=multi_engine;req=copy.deepcopy(req);req['goals']=[]
    p=preview(e,req).json();r=confirm(e,p);assert r.status_code==200,r.text
    with connect() as db:
        place=facts.records(db)['place','origin'].model_dump(mode='json')
        place.update(version=2,validation_status='unverified',reviewer=None,verification_method=None,verified_at=None)
        facts.import_bundle(facts.FactBundle(records=[place]),db,apply=True)
    read=current(e).json()
    assert read['stale'] and 'ENDPOINT_NOT_READY' in {i['code'] for i in read['issues']}


def complete_ongoing(e,monkeypatch,activity,moment):
    from app.city import state
    end=moment+timedelta(minutes=20)
    monkeypatch.setattr(service,'now',lambda:end);monkeypatch.setattr(state,'now',lambda:end)
    r=e[0].patch('/api/city/v1/trips/'+e[1]['id'],json={'schema_version':'city-state/2',
        'request_id':rid(),'expected_version':e[1]['version'],'action':{
            'operation':'mark_completed','day_id':'day:'+activity['starts_at'][:10],
            'stop_id':activity['goal_id'],'actual_start':moment.isoformat(),'actual_end':end.isoformat()}})
    assert r.status_code==200,r.text
    e[1]['version']=r.json()['version']
    return r.json()


@pytest.mark.parametrize('next_day',[False,True])
def test_completed_ongoing_never_blocks_later_today_or_next_day(multi_engine,monkeypatch,next_day):
    e,req,first=committed(multi_engine)
    a=first['itinerary']['days'][0]['itinerary']['activities'][0]
    moment=datetime.fromisoformat(a['starts_at'])+timedelta(minutes=1)
    start_today(e,monkeypatch,moment.isoformat(),'museum',complete_past=False)
    p=remaining(e,req,ongoing={'stop_id':a['goal_id'],'remaining_seconds':600})
    assert p.json()['status']=='preview',p.text
    r=confirm(e,p.json());assert r.status_code==200,r.text
    e[1]['version']=r.json()['version'];reserved=r.json()['itinerary']['days'][0]
    completed=complete_ongoing(e,monkeypatch,a,moment)
    if next_day:
        second=first['itinerary']['days'][1]['itinerary']['activities'][0]
        now=datetime.fromisoformat(second['starts_at'])+timedelta(minutes=1)
        start_today(e,monkeypatch,now.isoformat(),'museum',complete_past=False)
        p=remaining(e,req,ongoing={'stop_id':second['goal_id'],'remaining_seconds':600})
    else:
        now=moment+timedelta(minutes=21)
        start_today(e,monkeypatch,now.isoformat(),'museum',complete_past=False)
        p=remaining(e,req)
    assert p.status_code==200 and p.json()['status']=='preview',p.text
    plan=p.json()['result']['itineraries'][0]
    if next_day:
        assert plan['days'][0]['status']=='closed'
        assert plan['days'][0]['itinerary']==reserved['itinerary']
        assert plan['days'][0]['requirements']==reserved['requirements']
        assert plan['days'][1]['itinerary']['ongoing']['stop_id']==second['goal_id']
    else:
        assert plan['days'][0]['itinerary']['completed_prefix'][0]['stop_id']==a['goal_id']
        assert plan['days'][0]['itinerary']['ongoing'] is None
    r=confirm(e,p.json());assert r.status_code==200 and r.json()['route_version']==3,r.text
    if next_day:
        assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()['draft']['days'][0]==completed['draft']['days'][0]


@pytest.mark.parametrize('change',['expire','basket','planning','future_plan'])
def test_whole_candidate_baselines_and_future_integrity_reject_atomically(multi_engine,monkeypatch,change):
    e,req,_=committed(multi_engine);start_today(e,monkeypatch)
    p=remaining(e,req).json();assert p['status']=='preview'
    with connect() as db:
        if change=='expire':db.execute("UPDATE city_engine_candidates SET expires_at='2020-01-01T00:00:00+00:00' WHERE id=?",(p['candidate_id'],))
        elif change=='basket':
            from app.memory.storage import stamp
            db.execute('INSERT INTO city_baskets VALUES (?,?,?)',(e[1]['id'],1,stamp()))
        elif change=='planning':
            from app.memory.storage import stamp
            db.execute('INSERT INTO city_exploration_states VALUES (?,?,?,?,?,?)',(e[1]['id'],1,'null',None,0,stamp()))
        else:
            saved=json.loads(db.execute('SELECT result_json FROM city_engine_candidates WHERE id=?',(p['candidate_id'],)).fetchone()[0])
            saved['itineraries'][0]['days'][2]['itinerary']['activities'][0]['duration_seconds']+=1
            db.execute('UPDATE city_engine_candidates SET result_json=? WHERE id=?',(json.dumps(saved),p['candidate_id']))
    before=saved_rows();r=confirm(e,p)
    assert r.status_code==409,r.text
    assert saved_rows()==before
