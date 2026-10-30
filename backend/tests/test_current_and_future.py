"""Suffix replanning on isolated dates and operations, no external providers."""
import copy,json,sqlite3
from datetime import datetime,timedelta
import pytest
from app.db import connect
from app.city import state
from app.city.engine import service
from test_city_engine_api import engine,preview,confirm,current,rid
from test_hotel_overnight_binding import hotel_trip
from test_multi_day_api import multi_engine,committed,start_today,remaining,saved_rows


def suffix(e,req,**extra):return remaining(e,req,scope='current_and_future',**extra)


def test_suffix_preview_confirm_read_keeps_history_and_future_required(multi_engine,monkeypatch):
    e,req,old=committed(multi_engine);start_today(e,monkeypatch)
    before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()
    p=suffix(e,req);assert p.status_code==200 and p.json()['status']=='preview',p.text
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before
    for plan in p.json()['result']['itineraries']:
        assert plan['days'][0]==old['itinerary']['days'][0]
        a=plan['days'][2]['itinerary']['activities'][0];old_a=old['itinerary']['days'][2]['itinerary']['activities'][0]
        assert (a['place_id'],a['session_id'],a['starts_at'],a['departs_at'])==(old_a['place_id'],old_a['session_id'],old_a['starts_at'],old_a['departs_at'])
    r=confirm(e,p.json());assert r.status_code==200,r.text
    after=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()
    assert after['route_version']==2 and after['draft']['days'][0]==before['draft']['days'][0]
    saved=current(e).json();assert not saved['stale'],saved


def test_suffix_can_move_allowed_goal_and_reports_cross_day_diff(multi_engine,monkeypatch):
    e,req=multi_engine;req=copy.deepcopy(req)
    req['goals'][1].update(allowed_dates=['2030-05-02','2030-05-03'],fixed_date=None,required=False,duration_seconds=1800,target_id='restaurant')
    with connect() as db:
        records=[dict(r) for r in db.execute("SELECT * FROM city_facts WHERE kind='open_rule' AND json_extract(record_json,'$.place_id')='restaurant' AND json_extract(record_json,'$.business_date')='2030-05-03'")]
        for record in records:db.execute("DELETE FROM city_facts WHERE kind='open_rule' AND id=?",(record['id'],))
    p=preview(e,req).json();r=confirm(e,p);assert r.status_code==200,r.text;e[1]['version']=r.json()['version']
    old=current(e).json();g='visit:2030-05-02'
    # The original optimizer chooses day 2 for this flexible goal.
    assert any(a['goal_id']==g for a in old['itinerary']['days'][1]['itinerary']['activities']),old
    with connect() as db:
        for record in records:db.execute('INSERT INTO city_facts VALUES (?,?,?,?)',tuple(record.values()))
    start_today(e,monkeypatch,at='2030-05-02T19:45:00+08:00')
    p=suffix(e,req);assert p.status_code==200 and p.json()['candidate_id'],p.text
    plan=p.json()['result']['itineraries'][0]
    assert not any(a['goal_id']==g for a in plan['days'][1]['itinerary']['activities'])
    assert any(a['goal_id']==g for a in plan['days'][2]['itinerary']['activities'])
    assert p.json()['changes']['day_moves']==[{'goal_id':g,'before_day_id':'day:2030-05-02','after_day_id':'day:2030-05-03'}]
    r=confirm(e,p.json());assert r.status_code==200,r.text


def test_today_ongoing_is_preserved_and_not_reallocated(multi_engine,monkeypatch):
    e,req,old=committed(multi_engine);a=old['itinerary']['days'][1]['itinerary']['activities'][0]
    start_today(e,monkeypatch,(datetime.fromisoformat(a['starts_at'])+timedelta(seconds=60)).isoformat(),'museum')
    p=suffix(e,req,ongoing={'stop_id':a['goal_id'],'remaining_seconds':1200})
    assert p.status_code==200 and p.json()['candidate_id'],p.text
    plan=p.json()['result']['itineraries'][0];ongoing=plan['days'][1]['itinerary']['ongoing']
    assert ongoing['original_activity']==a
    assert not any(x['goal_id']==a['goal_id'] for d in plan['days'] for x in d['itinerary']['activities'])
    assert confirm(e,p.json()).status_code==200


@pytest.mark.parametrize('mutation',['history','budget','hotel','future_required','fixed_date','allow_more','remove_required','draft','stale_position','before_start','history_pending'])
def test_suffix_rejects_broken_protection_without_writes(multi_engine,monkeypatch,mutation):
    e,req,old=committed(multi_engine);start_today(e,monkeypatch,complete_past=mutation!='history_pending');changed=copy.deepcopy(req)
    if mutation=='history':changed['days'][0]['pace']='relaxed'
    elif mutation=='budget':changed['pricing']={'party_size':1,'total_budget_fen':1}
    elif mutation=='hotel':changed['overnights'][0]['place_id']='origin'
    elif mutation=='future_required':changed['goals'][-1]['duration_seconds']=1800
    elif mutation=='fixed_date':changed['goals'][-1]['fixed_date']=None
    elif mutation=='allow_more':changed['goals'][1]['allowed_dates']=['2030-05-02','2030-05-03']
    elif mutation=='remove_required':changed['goals'].pop()
    elif mutation=='draft':
        with connect() as db:db.execute("UPDATE city_trip_states SET status='DRAFT' WHERE id=?",(e[1]['id'],))
    elif mutation=='stale_position':monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2030-05-02T09:22:00+08:00'))
    elif mutation=='before_start':monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2030-05-02T08:00:00+08:00'))
    before=saved_rows();response=suffix(e,changed)
    assert response.status_code in (409,422),response.text
    assert saved_rows()==before


@pytest.mark.parametrize('mutation',['day','goal','total','progress','hotel','midnight','fact','position','version','rollback'])
def test_suffix_confirmation_guards_and_atomic_rollback(multi_engine,monkeypatch,mutation):
    e,req,old=committed(multi_engine);start_today(e,monkeypatch)
    p=suffix(e,req).json();assert p.get('candidate_id'),p
    if mutation in ('day','goal','total','progress'):
        with connect() as db:
            row=db.execute('SELECT result_json FROM city_engine_candidates WHERE id=?',(p['candidate_id'],)).fetchone();body=json.loads(row[0]);plan=body['itineraries'][0]
            if mutation=='day':plan['days'][0]['requirements']['pace']='relaxed'
            elif mutation=='goal':plan['days'][2]['requirements']['goals'][0]['required']=False
            elif mutation=='total':plan['known_cost_upper_fen']+=1
            else:plan['days'][1]['itinerary']['ongoing']={'stop_id':'forged'}
            db.execute('UPDATE city_engine_candidates SET result_json=? WHERE id=?',(json.dumps(body),p['candidate_id']))
    elif mutation=='hotel':
        with connect() as db:db.execute('UPDATE city_engine_candidates SET progress_json=json_set(progress_json,\'$.hotel_baseline\',\'forged\') WHERE id=?',(p['candidate_id'],))
    elif mutation=='midnight':monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2030-05-03T00:00:01+08:00'))
    elif mutation=='fact':
        with connect() as db:db.execute("DELETE FROM city_engine_operations WHERE kind='journey'")
    elif mutation=='position':
        with connect() as db:db.execute('UPDATE city_trip_states SET anchor_json=NULL WHERE id=?',(e[1]['id'],))
    elif mutation=='version':
        with connect() as db:db.execute('UPDATE city_trip_states SET version=version+1 WHERE id=?',(e[1]['id'],))
    else:
        def fail(*a,**kw):raise sqlite3.IntegrityError('injected suffix transaction failure')
        monkeypatch.setattr(state,'event',fail)
    before=saved_rows();response=confirm(e,p);assert response.status_code in (409,422,503),response.text
    assert saved_rows()==before


@pytest.mark.parametrize('kind',['delay','rest','extend','skip','planning'])
def test_suffix_agent_preview_context_and_confirmation(multi_engine,monkeypatch,kind):
    from app import language
    from app.city.agent import provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    e,req,old=committed(multi_engine)
    a=old['itinerary']['days'][1]['itinerary']['activities'][0]
    moment=start_today(e,monkeypatch) if kind=='skip' else start_today(e,monkeypatch,(datetime.fromisoformat(a['starts_at'])+timedelta(seconds=60)).isoformat(),'museum')
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    agent.TASKS.clear()
    if kind=='planning':
        text='今天和后续天一起调整，每天少走路'
        raw={'planning':{'operation':'update','quote':text,'fields':[{'field':'objective','value':'less_walking','quote':'每天少走路','scope':'every_day'}]}}
    else:
        text='今天和后续天一起调整，'+{'delay':'耽误了20分钟','rest':'先休息20分钟','extend':'当前站多待20分钟','skip':'下一站不去了，重新排'}[kind]
        raw={'state_request':{'operation':'skip_replan' if kind=='skip' else kind,'quote':text,'scope':'current_and_future',
            'scope_quote':'今天和后续天一起调整','minutes':None if kind=='skip' else 20,'next_stop':kind=='skip'}}
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    task=run({'client':e[0],'trip':e[1]},text)
    assert task['status']=='needs_confirmation',json.dumps(task,ensure_ascii=False)
    assert task['result']['reply'].count('今天剩余及后续天')==1
    p=task['result']['planning'];assert p['scope']=='current_and_future'
    assert p['result']['itineraries'][0]['days'][0]==old['itinerary']['days'][0]
    pending=e[0].get('/api/city/v1/trips/'+e[1]['id']+'/assistant/context?purpose=modify').json()['pending_changes']
    candidate=next(pending for pending in pending if pending.get('candidate_id')==p['candidate_id'])
    assert candidate['scope']=='current_and_future' and len(candidate['changes']['days'])==3
    r=e[0].post('/api/city/v1/trips/'+e[1]['id']+'/assistant/confirm',json={'request_id':rid(),'confirmed':True,**task['result']['confirmation']})
    assert r.status_code==200,r.text
    assert current(e).json()['route_version']==2
    agent.TASKS.clear()


@pytest.mark.parametrize('text,quote,scope',[
    ('今天和明天都少走路','少走路','current_and_future'),
    ('今天剩下的和明天的行程一起重新排一下，少走点路','少走点路','current_and_future'),
    ('从今天开始后面的行程都改成少走路','少走路','current_and_future'),
    ('每个景点都少走路','少走路',None),
    ('剩下的时间少走路','剩下的时间少走路',None),
    ('明天少走路','少走路','selected_future_day'),
])
def test_confirmed_agent_recovers_unsupported_daily_scope_for_dated_routing(multi_engine,monkeypatch,text,quote,scope):
    from app import language
    from app.city.agent import provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    e,req,old=committed(multi_engine);moment=start_today(e,monkeypatch)
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    agent.TASKS.clear()
    raw={'planning':{'operation':'update','quote':text,'fields':[
        {'field':'objective','value':'less_walking','quote':quote,'scope':'every_day'}]}}
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()
    task=run({'client':e[0],'trip':e[1]},text)
    if scope is None:
        assert task['status']=='needs_clarification',task
        assert task['result']['reply']=='多天行程里只调整今天剩余的安排，请在行程页使用“调整今天剩余”；如果要连同后面几天一起改，可以说“今天和明天都……”。'
        assert not task['result'].get('confirmation')
    else:
        assert task['status']=='needs_confirmation',json.dumps(task,ensure_ascii=False)
        assert task['result']['planning']['scope']==scope
        if scope=='selected_future_day':assert task['result']['planning']['business_date']=='2030-05-03'
    assert not task['interpretation'].get('dropped_fields')
    recovered=[f for f in task['interpretation']['recovered_fields'] if f['field']=='objective']
    assert recovered==[{'field':'objective','source':'continuous_scope' if scope=='current_and_future' else 'unsupported_every_day','quote':quote}]
    assert '没采用' not in task['result']['reply'] and '旧单日入口' not in task['result']['reply']
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before
    agent.TASKS.clear()


@pytest.mark.parametrize('text',['今天和明天都少走路','少走路'])
def test_continuous_daily_scope_is_not_recovered_for_unconfirmed_multi_day_draft(multi_engine,monkeypatch,text):
    from types import SimpleNamespace
    from app.memory.session import Session
    from app.city.agent.models import PlanningIntent
    from app.city.agent.planning_intent import grounded
    from app.city.assistant import context
    from app.city.engine.models import MultiDayRequirements
    e,req=multi_engine;moment=datetime.fromisoformat('2030-05-02T09:00:00+08:00')
    monkeypatch.setattr(context,'now',lambda:moment)
    with connect() as db:
        row=db.execute('SELECT * FROM conversation_sessions LIMIT 1').fetchone();session=Session(row['id'],row['guest_token_hash'],None)
    snapshot=context.load(session,e[1]['id'],'modify')
    snapshot=SimpleNamespace(**(vars(snapshot)|{'multi_requirements':MultiDayRequirements.model_validate(req)}))
    dropped=[];meta={}
    planning=PlanningIntent(operation='update',quote=text,fields=[
        {'field':'objective','value':'less_walking','quote':'少走路','scope':'every_day'}])
    result=grounded(planning,text,snapshot,dropped,meta)
    assert snapshot.multi_requirements and snapshot.multi_itinerary is None and not result.fields
    assert dropped==[{'field':'objective','check':'scope_not_in_quote'}]
    assert not meta.get('recovered_fields')


def test_suffix_agent_relaxed_pace_with_recovered_tomorrow_date(multi_engine,monkeypatch):
    from app import language
    from app.city.agent import provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    e,req,old=committed(multi_engine);moment=start_today(e,monkeypatch)
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    agent.TASKS.clear();text='连着明天一起，节奏放慢一点'
    raw={'planning':{'operation':'update','quote':text,'fields':[
        {'field':'pace','value':'relaxed','quote':'节奏放慢一点','scope':'every_day'}]}}
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()
    task=run({'client':e[0],'trip':e[1]},text)
    assert task['status']=='needs_confirmation',json.dumps(task,ensure_ascii=False)
    assert {'field':'date','source':'date_grammar'} in task['interpretation']['recovered_fields']
    p=task['result']['planning'];assert p['scope']=='current_and_future'
    for plan in p['result']['itineraries']:
        assert plan['days'][0]==old['itinerary']['days'][0]
        assert [day['requirements']['pace'] for day in plan['days'][1:]]==['relaxed','relaxed']
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before
    agent.TASKS.clear()


@pytest.mark.parametrize('date,accepted',[('今天',True),('明天',True),('5月1日',False),('5月4日',False)])
def test_suffix_date_fields_stay_within_today_and_trip_range(multi_engine,monkeypatch,date,accepted):
    from app.city.agent import suffix_planning
    from app.city.agent.models import Understanding
    from app.city.assistant import context
    from app.memory.session import Session
    e,req,_=committed(multi_engine);moment=start_today(e,monkeypatch)
    monkeypatch.setattr(context,'now',lambda:moment)
    with connect() as db:
        row=db.execute('SELECT * FROM conversation_sessions LIMIT 1').fetchone();session=Session(row['id'],row['guest_token_hash'],None)
    snapshot=context.load(session,e[1]['id'],'modify');calls=[]
    def preview(*a,**kw):calls.append(a[2]);return {'status':'preview'}
    monkeypatch.setattr(service,'preview',preview)
    text='今天和后续天一起调整，'+date+'节奏放慢一点'
    intent=Understanding(planning={'operation':'update','quote':text,'fields':[
        {'field':'date','value':date,'quote':date},{'field':'pace','value':'relaxed','quote':'节奏放慢一点'}]})
    before=saved_rows();result=suffix_planning.preview(session,e[1]['id'],snapshot,intent,rid(),text)
    if accepted:
        assert result['status']=='preview' and len(calls)==1
        assert [day.pace for day in calls[0].requirements.days[1:]]==['relaxed','relaxed']
    else:
        assert result=={'status':'needs_clarification','reply':'该日期不属于今天及后续未开始的行程。'}
        assert not calls
    assert saved_rows()==before


def test_mismatched_agent_scope_cannot_expand_tomorrow_to_whole_suffix(multi_engine,monkeypatch):
    from app.city.assistant import context,tools
    from app.city.agent.toolbelt import preview_adjustment
    from app.city.agent.models import StateIntent
    from app.memory.session import Session
    from fastapi import HTTPException
    e,req,_=committed(multi_engine);moment=start_today(e,monkeypatch)
    monkeypatch.setattr(context,'now',lambda:moment)
    with connect() as db:
        row=db.execute('SELECT * FROM conversation_sessions LIMIT 1').fetchone();session=Session(row['id'],row['guest_token_hash'],None)
    snap=context.load(session,e[1]['id'],'modify');before=saved_rows()
    with pytest.raises(HTTPException) as exc:preview_adjustment(session,e[1]['id'],snap,StateIntent(operation='rest',quote='明天休息20分钟',scope='current_and_future',scope_quote='明天',minutes=20),rid())
    assert exc.value.status_code==422 and saved_rows()==before


def test_suffix_preserves_paid_lodging_and_whole_budget(hotel_trip,monkeypatch):
    from test_trip_budget import price_ops,priced_select,options
    e,req=hotel_trip;price_ops(e);priced_select(e);req['pricing']=options(total_budget_fen=180000)
    saved=confirm(e,preview(e,req).json());assert saved.status_code==200,saved.text;e[1]['version']=saved.json()['version']
    start_today(e,monkeypatch)
    req=current(e).json()['requirements'];req['pricing']['spending']={'amount_fen':31000,'as_of':'2030-05-02T09:05:00+08:00','covered_cost_ids':['lodging:flyai:1']}
    paid=remaining(e,req);assert paid.status_code==200 and paid.json()['candidate_id'],paid.text
    saved=confirm(e,paid.json());assert saved.status_code==200,saved.text;e[1]['version']=saved.json()['version']
    base=current(e).json();p=suffix(e,base['requirements']);assert p.status_code==200 and p.json()['candidate_id'],p.text
    budget=p.json()['result']['itineraries'][0]['budget']
    assert budget['by_category']['spent']==31000 and not budget['by_category'].get('lodging')
    assert budget['items'][0:] and sum(i['category']=='spent' for i in budget['items'])==1
    saved=confirm(e,p.json());assert saved.status_code==200,saved.text
    assert not current(e).json()['stale']


@pytest.mark.parametrize('mode',['unknown','over'])
def test_suffix_hard_budget_does_not_confirm_incomplete_or_expensive_trip(hotel_trip,monkeypatch,mode):
    from test_trip_budget import price_ops,priced_select,options
    e,req=hotel_trip;price_ops(e);priced_select(e);req['pricing']=options(total_budget_fen=180000,hard_limit=True)
    saved=confirm(e,preview(e,req).json());assert saved.status_code==200,saved.text;e[1]['version']=saved.json()['version'];start_today(e,monkeypatch)
    # Model an already confirmed budget changing at the source, not a request bypass.
    from app.city import travel_services
    with connect() as db:
        services=travel_services.read(db,e[1]['id']);selection=services['selection']['hotels'];selection['price_selection']['amount_fen']=180001 if mode=='over' else 15000
        if mode=='unknown':selection['price_selection']['expires_at']='2030-05-01T08:00:00+08:00'
        travel_services.write(db,e[1]['id'],services)
    before=saved_rows();p=suffix(e,current(e).json()['requirements'])
    assert p.status_code==200 and not p.json()['candidate_id'],p.text
    assert len(saved_rows()[1])==len(before[1])


@pytest.mark.parametrize('text',['只改明天，今天和后续天不要一起改','假如今天和后续天一起调整呢','这是日志原文：“今天和后续天一起调整”'])
def test_scope_denial_or_example_never_authorizes_the_suffix(multi_engine,monkeypatch,text):
    from app.city.assistant import context,tools
    from app.memory.session import Session
    from fastapi import HTTPException
    e,_,_=committed(multi_engine);moment=start_today(e,monkeypatch);monkeypatch.setattr(context,'now',lambda:moment)
    with connect() as db:
        row=db.execute('SELECT * FROM conversation_sessions LIMIT 1').fetchone();session=Session(row['id'],row['guest_token_hash'],None)
    snap=context.load(session,e[1]['id'],'modify')
    with pytest.raises(HTTPException) as exc:tools.continuous_scope(snap,text)
    assert exc.value.status_code==422


def test_suffix_idempotency_and_concurrent_confirmation_only_commit_once(multi_engine,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    e,req,_=committed(multi_engine);start_today(e,monkeypatch);request=rid()
    first=suffix(e,req,request_id=request);assert first.status_code==200 and first.json()['candidate_id'],first.text
    assert suffix(e,req,request_id=request).json()==first.json()
    assert first.json()['result']['metrics']['assignment_attempts']<=8
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:confirm(e,first.json()),range(2)))
    assert sorted(r.status_code for r in results)==[200,409]
    assert current(e).json()['route_version']==2


def test_expired_unscheduled_optional_is_not_revived_or_causes_invalid_suffix(multi_engine,monkeypatch):
    e,req=multi_engine;req=copy.deepcopy(req)
    req['goals'].append({'goal_id':'missed-optional','target_kind':'place','target_id':'museum','required':False,
        'duration_seconds':3600,'allowed_dates':['2030-05-01','2030-05-02'],
        'window_start':'2030-05-01T09:01:00+08:00','window_end':'2030-05-01T09:02:00+08:00'})
    p=preview(e,req);assert p.status_code==200 and p.json()['candidate_id'],p.text
    r=confirm(e,p.json());assert r.status_code==200,r.text;e[1]['version']=r.json()['version'];start_today(e,monkeypatch)
    p=suffix(e,req);assert p.status_code==200 and p.json()['candidate_id'],p.text
    assert not any(a['goal_id']=='missed-optional' for d in p.json()['result']['itineraries'][0]['days'] for a in d['itinerary']['activities'])
    assert confirm(e,p.json()).status_code==200


@pytest.mark.parametrize('scope',['selected_future_day','current_and_future'])
def test_single_day_route_cannot_be_reinterpreted_as_a_multi_day_edit(engine,scope):
    from multi_day_helpers import requirements
    e=engine;saved=confirm(e,preview(e).json());assert saved.status_code==200,saved.text
    e[1]['version']=saved.json()['version'];before=saved_rows()
    r=remaining(e,requirements(),scope=scope,day_id='day:2030-05-02')
    assert r.status_code==422,r.text
    assert saved_rows()==before


@pytest.mark.parametrize('text,expected',[
    ('今天剩下的和明天的都重新排一下',True),('今天和后面两天一起调整',True),('从今天开始后面的行程都改成少走路',True),
    ('今天以及之后几天都不要太赶',True),('连同明天后天一起改',True),('往后几天都改成九点出发',True),('今天及以后都少走路',True),
    ('今天后面的安排帮我改一下',False),('今天的后面还有什么',False),('今天天气怎么样，明天呢',False),
    ('今天剩下的时间少走路',False),('今天之后的那个景点几点开门',False),('后面那个景点不去了',False),
])
def test_continuous_scope_needs_later_days_not_the_rest_of_today(text,expected):
    from app.city.assistant.tools import has_continuous_scope
    assert has_continuous_scope(text) is expected
