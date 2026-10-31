"""E26: programs collected from a scenic page are planned inside their park's visit."""
import json
from datetime import date
from uuid import uuid4
import pytest
from app.db import connect
from app.city import facts
from app.city.engine import service as engine_service
from app.city.engine.models import TripGoal
from app.city.engine.storage import OperationalBundle, import_operations
from app.city.exploration import match_programs, _park_programs_multi
from test_city_engine_api import FIXTURE
from test_exploration import base, collect, constraints


def rid():return str(uuid4())


def _bundle(extra_programs=()):
    """The engine fixture with `garden` turned into a scenic park that hosts both stages behind one gate."""
    d=json.loads(FIXTURE.read_text());records=d['facts']['records']
    garden=next(r for r in records if r['kind']=='place' and r['fact_id']=='garden')
    garden['place_type']='scenic'
    records.append(garden|{'fact_id':'gate','name':'[合成测试]gate','place_type':'entrance','parent_place_id':'garden'})
    meta={k:garden[k] for k in ('version','validation_status','is_demo','rights_status','verified_at','reviewer','verification_method','effective_from','effective_to','source_ids')}
    records.append(meta|{'kind':'entrance','fact_id':'entrance:garden','place_id':'garden','entrance_place_id':'gate','direction':'both'})
    for r in records:
        r['is_demo']=False
        if r['kind']=='place' and r['fact_id'] in ('stage1','stage2'):r['parent_place_id']='garden'
        if r['kind']=='open_rule' and r['place_id']=='garden':r['last_entry_at']='2030-05-01T21:00:00+08:00'
    program=next(r for r in records if r['kind']=='program')
    for fact_id,name,aliases in extra_programs:
        records.append(program|{'fact_id':fact_id,'name':name,'aliases':list(aliases),'place_id':'garden'})
    inside={'garden','stage1','stage2','gate'}
    for j in d['operations']['journeys']:
        j['travel']['evidence']['kind']='verified';j['travel']['path_status']='verified';j['time_basis']='survey_envelope'
        a=j['travel']['origin_place_id'] in inside;b=j['travel']['destination_place_id'] in inside
        j['boundaries']=[{'park_id':'garden','gate_place_id':'gate','direction':'exit' if a else 'entry','seconds':120,'cost_upper_fen':0,
                          'evidence':j['travel']['evidence']}] if a!=b else []
    for c in d['operations']['costs']:c['evidence']['kind']='verified'
    return d


@pytest.fixture
def park(client):
    d=_bundle()
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(d['facts']),db,apply=True)
        import_operations(db,OperationalBundle.model_validate(d['operations']),facts.records(db),apply=True)
    client.post('/api/v1/sessions')
    t=client.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    return client,t,d


def _plan(e,**overrides):
    c,t,d=e;b=c.get(base(t)+'/basket').json()
    r=c.post(base(t)+'/exploration/preview',json={'request_id':rid(),'expected_version':t['version'],'basket_version':b['version'],'constraints':constraints(d)|overrides})
    assert r.status_code==200,r.text
    return r.json()


def _clock(value):return value[11:16]


def test_program_alone_adds_its_park_and_covers_a_session(park):
    c,t,d=park
    collect(c,t,'program','program','must')
    p=_plan(park)
    assert p['status']=='feasible',p
    notes=p['program_notes'];assert len(notes)==1
    (goal_id,items),=notes.items()
    assert [(_clock(i['starts_at']),i['name'],i['key']) for i in items]==[('11:00','[合成测试]演出','program:program')]
    for itinerary in p['planning']['result']['itineraries']:
        visit=next(a for a in itinerary['activities'] if a['goal_id']==goal_id)
        assert visit['place_id']=='garden'
        # The park visit must contain the show with the in-park buffer on both sides.
        assert _clock(visit['starts_at'])<='10:50' and _clock(visit['departs_at'])>='11:40'
    assert '加入路线' in p['reply']
    assert not any(row['key']=='program:program' for rows in p['unscheduled'].values() for row in rows)
    confirmed=c.post(base(t)+'/engine/confirm',json={'request_id':rid(),'expected_version':p['planning']['base_version'],'candidate_id':p['planning']['candidate_id']})
    assert confirmed.status_code==200,confirmed.text
    item=next(i for i in c.get(base(t)+'/basket').json()['items'] if i['kind']=='program')
    assert item['status']=='arranged' and item['stop_ids']


def test_program_reuses_a_park_already_in_the_basket(park):
    c,t,d=park
    collect(c,t,'place','garden','want');collect(c,t,'program','program','want')
    p=_plan(park)
    assert p['status']=='feasible',p
    for itinerary in p['planning']['result']['itineraries']:
        assert [a['place_id'] for a in itinerary['activities']].count('garden')<=1
    (goal_id,_),=p['program_notes'].items()
    assert p['goal_keys'][goal_id]=='place:garden'


def test_session_outside_trip_window_is_reported_not_silently_dropped(park):
    c,t,d=park
    collect(c,t,'place','museum','must');collect(c,t,'program','program','want')
    p=_plan(park,starts_at='2030-05-01T16:00:00+08:00',ends_at='2030-05-01T20:00:00+08:00')
    assert p['status']=='feasible',p
    assert not p.get('program_notes')
    rows=[row for rows in p['unscheduled'].values() for row in rows if row['key']=='program:program']
    assert rows and rows[0]['reason_code']=='session_pending'


def test_only_unfit_program_gives_unknown_with_reason(park):
    c,t,d=park
    collect(c,t,'program','program','must')
    p=_plan(park,starts_at='2030-05-01T16:00:00+08:00',ends_at='2030-05-01T20:00:00+08:00')
    assert p['status']=='unknown' and p['issues'][0]['code']=='SESSION_PENDING' and p['planning'] is None
    assert p['unscheduled']['0'][0]['reason_code']=='session_pending'


def test_program_without_park_keeps_original_show_planning(client):
    d=json.loads(FIXTURE.read_text())
    for r in d['facts']['records']:r['is_demo']=False
    for j in d['operations']['journeys']:
        j['travel']['evidence']['kind']='verified';j['travel']['path_status']='verified';j['time_basis']='survey_envelope'
    for x in d['operations']['costs']:x['evidence']['kind']='verified'
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(d['facts']),db,apply=True)
        import_operations(db,OperationalBundle.model_validate(d['operations']),facts.records(db),apply=True)
    client.post('/api/v1/sessions');t=client.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    collect(client,t,'program','program','must')
    p=_plan((client,t,d))
    assert not p.get('program_notes')
    assert p['status']=='feasible',p
    assert any(a['place_id'] in ('stage1','stage2') for a in p['planning']['result']['itineraries'][0]['activities'])


def test_program_name_matching_is_exact_within_the_park(client):
    d=_bundle(extra_programs=[('p_fire','打铁花',()),('p_dup1','重名节目',()),('p_dup2','重名节目',())])
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(d['facts']),db,apply=True)
        ctx=engine_service.context(db)
    found=match_programs(ctx,'garden',['《打铁花》',' 打铁花 ','打铁','[合成测试]演出','重名节目','不存在'])
    assert found['《打铁花》']=='p_fire' and found[' 打铁花 ']=='p_fire'
    assert found['打铁'] is None and found['不存在'] is None
    assert found['[合成测试]演出']=='program'
    assert found['重名节目'] is None
    assert match_programs(ctx,'museum',['打铁花'])=={'打铁花':None}
    assert match_programs(ctx,None,['打铁花'])=={'打铁花':None}


def test_multi_day_program_becomes_its_park_visit(client):
    d=_bundle()
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(d['facts']),db,apply=True)
        ctx=engine_service.context(db)
    days=(date(2030,5,1),date(2030,5,2))
    goals=[TripGoal(goal_id='basket:p',target_kind='program',target_id='program',kind='show',required=True,allowed_dates=days),
           TripGoal(goal_id='basket:m',target_kind='place',target_id='museum',kind='visit',allowed_dates=days)]
    out,notices=_park_programs_multi(goals,ctx,[])
    assert [g.target_id for g in out]==['museum','garden']
    garden=out[1];assert garden.required and garden.allowed_dates==days and garden.target_kind=='place'
    assert notices and '场次以当天公告为准' in notices[0]
    existing=[TripGoal(goal_id='basket:g',target_kind='place',target_id='garden',kind='visit',allowed_dates=days),goals[0]]
    out,_=_park_programs_multi(existing,ctx,[])
    assert [(g.goal_id,g.required) for g in out]==[('basket:g',True)]


def test_unreachable_first_session_retries_the_next_one(park,monkeypatch):
    from app.city import exploration
    monkeypatch.setattr(exploration,'SHOW_ARRIVAL_LEAD_SECONDS',60)  # 让 11:00 场先被选中，但 10:45 出发赶不上
    c,t,d=park
    collect(c,t,'program','program','must')
    p=_plan(park,starts_at='2030-05-01T10:45:00+08:00')
    assert p['status']=='feasible',p
    (_,items),=p['program_notes'].items()
    assert [_clock(i['starts_at']) for i in items]==['15:00']


def test_no_reachable_session_keeps_park_visit_and_explains(park,monkeypatch):
    from app.city import exploration
    monkeypatch.setattr(exploration,'SHOW_ARRIVAL_LEAD_SECONDS',60)
    c,t,d=park
    collect(c,t,'program','program','must')
    p=_plan(park,starts_at='2030-05-01T10:45:00+08:00',ends_at='2030-05-01T14:00:00+08:00')
    assert p['status']=='feasible',p
    assert not p.get('program_notes')
    assert any(a['place_id']=='garden' for a in p['planning']['result']['itineraries'][0]['activities'])
    rows=[row for row in p['unscheduled']['0'] if row['key']=='program:program']
    assert len(rows)==1 and rows[0]['reason_code']=='session_pending'
    assert '加入路线' in p['reply']
