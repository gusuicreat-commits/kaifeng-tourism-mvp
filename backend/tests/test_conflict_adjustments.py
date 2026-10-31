"""Single-day conflict choices are checked against isolated synthetic engine facts."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import pytest
from app.db import connect
from app.city import exploration,facts
from app.city.engine.models import Goal
from test_city_engine import sample,change_fact
from test_city_engine_api import engine
from test_city_assistant import city
from test_exploration import collect,plan,base,answer_body


def opening(place='museum',**patch):
    with connect() as db:
        old=facts.records(db)['open_rule','open:'+place]
        updated=type(old).model_validate(old.model_dump()|patch)
        db.execute('UPDATE city_facts SET record_json=? WHERE kind=? AND id=?',
                   (updated.model_dump_json(),'open_rule',old.fact_id))


def option(result,identity):return next(o for o in result['options'] if o['id']==identity)


def apply_patch(engine,result,choice):
    values=dict(result['constraints']);values.update(choice['patch'])
    if 'business_date' in choice['patch']:
        for key in ('starts_at','ends_at'):values[key]=values['business_date']+values[key][10:]
    response=plan(engine,**values)
    assert response.status_code==200,response.text
    return response.json()


def test_morning_required_has_specific_conflict_and_feasible_start_patch(engine):
    c,t,_=engine;collect(c,t,'place','museum','must')
    opening(opens_at='2030-05-01T08:00:00+08:00',closes_at='2030-05-01T12:00:00+08:00')
    result=plan(engine,starts_at='2030-05-01T14:00:00+08:00').json()
    assert result['status']=='infeasible',result
    assert result['conflicts']==[{'goal_id':next(iter(result['goal_keys'])),'key':'place:museum',
        'name':'[合成测试]museum','reason_code':'closed_in_window','reason':'开放时间和你的出行时段对不上'}]
    assert not any(o['id']=='adjust:end' for o in result['options'])
    choice=option(result,'adjust:start')
    assert choice['patch']=={'starts_at':'2030-05-01T11:00:00+08:00'}
    assert '已核算能排进[合成测试]museum' in choice['label']
    assert '资料或计算条件不足' not in result['reply']
    assert all(o['requires_consent'] for o in result['options'])
    assert c.get(base(t)).json()['route_version']==0
    assert c.get(base(t)+'/basket').json()['items'][0]['priority']=='must'
    assert apply_patch(engine,result,choice)['status']=='feasible'


def test_combined_required_conflict_solo_feasible_and_extend_end(engine):
    c,t,_=engine
    for pid in ('museum','garden'):collect(c,t,'place',pid,'must')
    result=plan(engine,ends_at='2030-05-01T10:15:00+08:00').json()
    assert result['status']=='infeasible',result
    assert [r['reason_code'] for r in result['conflicts']]==['time_conflict']*2
    for row in result['conflicts']:
        other=next(r['name'] for r in result['conflicts'] if r!=row)
        assert row['reason']=='和'+other+'放在一起时间不够'
    choice=option(result,'adjust:end')
    assert choice['patch']=={'ends_at':'2030-05-01T11:15:00+08:00'}
    assert apply_patch(engine,result,choice)['status']=='feasible'
    assert all(i['priority']=='must' for i in c.get(base(t)+'/basket').json()['items'])


def test_next_day_uses_actual_dated_rule_and_patch_repreviews(engine):
    c,t,_=engine;collect(c,t,'place','museum','must')
    with connect() as db:
        old=facts.records(db)['open_rule','open:museum']
        tomorrow=old.model_copy(update={'fact_id':'open:museum:next','business_date':old.business_date+timedelta(days=1),
            'opens_at':old.opens_at+timedelta(days=1),'closes_at':old.closes_at+timedelta(days=1)})
        facts.import_bundle(facts.FactBundle(records=[tomorrow]),db,apply=True)
    opening(opens_at='2030-05-01T06:00:00+08:00',closes_at='2030-05-01T07:00:00+08:00')
    result=plan(engine,starts_at='2030-05-01T14:00:00+08:00').json()
    assert result['status']=='infeasible'
    assert option(result,'adjust:date')['patch']=={'business_date':'2030-05-02'}
    assert apply_patch(engine,result,option(result,'adjust:date'))['status']=='feasible'


def test_missing_next_day_data_keeps_consent_to_relax_and_reports_unfinished(engine):
    c,t,_=engine;collect(c,t,'place','museum','must')
    opening(opens_at='2030-05-01T06:00:00+08:00',closes_at='2030-05-01T07:00:00+08:00')
    result=plan(engine,starts_at='2030-05-01T14:00:00+08:00').json()
    assert result['status']=='infeasible'
    assert not any(o['kind']=='constraints' for o in result['options'])
    assert option(result,'relax:place:museum')['requires_consent']
    assert '没来得及核算完' in result['reply']
    assert '已核算能排进' not in result['reply']


def test_unknown_opening_keeps_previous_data_missing_behavior(engine,monkeypatch):
    c,t,_=engine;collect(c,t,'place','museum','must');opening(opens_at=None,closes_at=None)
    calls=[];original=exploration.engine.plan_trip
    def solve(*args):calls.append(args);return original(*args)
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    result=plan(engine).json()
    assert result['status']=='unknown' and result['planning']['result']['status']=='needs_data'
    assert not result.get('conflicts') and not result['options'] and len(calls)==1
    assert result['reply']=='资料或计算条件不足，暂不能可靠判定。'


def diagnose(req,ctx):
    solved=exploration.engine.plan_trip(req,ctx)
    keys={g.goal_id:'place:'+g.target_id for g in req.goals}
    meta={g.goal_id:{'name':'[合成测试]'+g.target_id,'status':'pending','goal':g} for g in req.goals}
    return solved,lambda:exploration._conflict_adjustments(solved,req,ctx,keys,meta)


@pytest.mark.parametrize('duration,minutes,tried',[(10800,180,[180,60,120]),(7200,120,[180,60,120]),(3000,60,[180,60])])
def test_largest_start_relaxation_is_refined_to_smallest_feasible(sample,monkeypatch,duration,minutes,tried):
    req,ctx=sample
    req=req.model_copy(update={'starts_at':req.starts_at.replace(hour=14),'ends_at':req.ends_at.replace(hour=15),
        'goals':(Goal(goal_id='visit',target_kind='place',target_id='museum',required=True,duration_seconds=duration),)})
    ctx=change_fact(ctx,'open_rule','open:museum',closes_at=req.ends_at)
    solved,run=diagnose(req,ctx);prepared=[];prepare=exploration.engine.prepare
    def preflight(candidate,context):prepared.append(candidate);return prepare(candidate,context)
    monkeypatch.setattr(exploration.engine,'prepare',preflight)
    result=run();choice=option(result,'adjust:start')
    assert not solved.itineraries and solved.status in ('infeasible','needs_data')
    assert choice['patch']=={'starts_at':(req.starts_at-timedelta(minutes=minutes)).isoformat()}
    # 先试最大、再试最小，最后才试中间；同一天仍为必去的“提前开始”候选去重后应等于 tried。
    tried_start=[]
    for c in prepared:
        if c.business_date==req.business_date and c.goals[0].required and c.starts_at<req.starts_at:
            offset=int((req.starts_at-c.starts_at).total_seconds()/60)
            if offset not in tried_start:tried_start.append(offset)
    assert tried_start==tried
    plan=exploration.engine.plan_trip
    assert plan(req.model_copy(update={'starts_at':req.starts_at-timedelta(minutes=minutes)}),ctx).status=='feasible'
    assert all(not plan(req.model_copy(update={'starts_at':req.starts_at-timedelta(minutes=m)}),ctx).itineraries
               for m in (60,120) if m<minutes)


def test_infeasible_maximum_prunes_each_time_direction(sample,monkeypatch):
    req,ctx=sample
    req=req.model_copy(update={'starts_at':req.starts_at.replace(hour=14),'ends_at':req.ends_at.replace(hour=15),
        'goals':(Goal(goal_id='visit',target_kind='place',target_id='museum',required=True,duration_seconds=18000),)})
    ctx=change_fact(ctx,'open_rule','open:museum',closes_at=req.ends_at)
    _,run=diagnose(req,ctx);prepared=[];prepare=exploration.engine.prepare
    def preflight(candidate,context):prepared.append(candidate);return prepare(candidate,context)
    monkeypatch.setattr(exploration.engine,'prepare',preflight)
    result=run()
    same_day=[c for c in prepared if c.business_date==req.business_date and c.goals[0].required]
    assert [(c.starts_at,c.ends_at) for c in same_day]==[
        (req.starts_at,req.ends_at+timedelta(hours=3)),(req.starts_at-timedelta(hours=3),req.ends_at)]
    assert not any(o['kind']=='constraints' for o in result['options'])


@pytest.mark.parametrize('status',['needs_data','timeout'])
def test_unfinished_maximum_skips_smaller_candidates_but_tries_next_direction(sample,monkeypatch,status):
    req,ctx=sample
    req=req.model_copy(update={'starts_at':req.starts_at.replace(hour=12),'ends_at':req.ends_at.replace(hour=14),
        'goals':(Goal(goal_id='visit',target_kind='place',target_id='museum',required=True),)})
    solved=SimpleNamespace(itineraries=(),status='infeasible',issues=(SimpleNamespace(code='HARD_CONSTRAINT_CONFLICT'),),metrics={})
    calls=[];solve=exploration.engine.plan_trip
    def track(candidate,context):
        calls.append(candidate)
        if candidate.ends_at>req.ends_at:return SimpleNamespace(status=status,itineraries=())
        return solve(candidate,context)
    monkeypatch.setattr(exploration.engine,'plan_trip',track)
    result=exploration._conflict_adjustments(solved,req,ctx,{'visit':'place:museum'},
        {'visit':{'name':'[合成测试]museum','status':'pending','locked':True}})
    assert [c.ends_at for c in calls if c.ends_at>req.ends_at]==[req.ends_at+timedelta(hours=3)]
    assert [c.starts_at for c in calls if c.starts_at<req.starts_at]==[
        req.starts_at-timedelta(minutes=m) for m in (180,60)]
    assert option(result,'adjust:start')['patch']=={'starts_at':(req.starts_at-timedelta(hours=1)).isoformat()}


def test_required_pair_solo_not_counted_and_offers_end_start_relax(sample,monkeypatch):
    req,ctx=sample
    goals=tuple(Goal(goal_id=pid,target_kind='place',target_id=pid,required=True,duration_seconds=3600) for pid in ('museum','garden'))
    req=req.model_copy(update={'goals':goals,'ends_at':req.starts_at+timedelta(minutes=90)})
    solved,run=diagnose(req,ctx);original=exploration.engine.plan_trip;calls=[]
    def solve(candidate,context):
        calls.append(candidate)
        assert candidate.alternatives==1 and candidate.solve_seconds<=min(req.solve_seconds,1)
        return original(candidate,context)
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    result=run()
    assert solved.status=='infeasible' and result['conflicts']
    assert {'adjust:end','adjust:start','relax:conflicts'}<= {o['id'] for o in result['options']}
    # 单独试排是诊断，不计入 8 次调整候选上限。
    assert len([c for c in calls if len(c.goals)==1])==2
    assert len([c for c in calls if len(c.goals)>1])==4


def test_budget_stops_after_fifteen_seconds_and_keeps_verified_options(sample,monkeypatch):
    req,ctx=sample
    req=req.model_copy(update={'goals':(Goal(goal_id='visit',target_kind='place',target_id='museum',required=True),),
        'ends_at':req.starts_at+timedelta(minutes=40)})
    solved=SimpleNamespace(itineraries=(),status='infeasible',issues=(SimpleNamespace(code='HARD_CONSTRAINT_CONFLICT'),),metrics={})
    original=exploration.engine.plan_trip;calls=[];ticks=[0]
    monkeypatch.setattr(exploration,'time',SimpleNamespace(monotonic=lambda:ticks[0]))
    def solve(candidate,context):
        calls.append(candidate);result=original(candidate,context)
        if len(calls)==2:ticks[0]=16
        return result
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    result=exploration._conflict_adjustments(solved,req,ctx,{'visit':'place:museum'},
        {'visit':{'name':'[合成测试]museum','status':'pending','locked':True}})
    assert len(calls)==2 and result['conflicts'][0]['reason_code']=='too_far_alone'
    assert [o['id'] for o in result['options'] if o['kind']=='constraints']==['adjust:end']


@pytest.mark.parametrize('status',['needs_data','timeout'])
def test_uncertain_solve_never_creates_verified_option(sample,monkeypatch,status):
    req,ctx=sample
    req=req.model_copy(update={'goals':(Goal(goal_id='visit',target_kind='place',target_id='museum',required=True),),
        'ends_at':req.starts_at+timedelta(minutes=40)})
    _,run=diagnose(req,ctx)
    monkeypatch.setattr(exploration.engine,'plan_trip',lambda *a:SimpleNamespace(status=status,itineraries=()))
    result=run()
    assert not any(o.get('patch') for o in result['options'])
    assert '已核算' not in result['reply']
    assert '没来得及核算完' in result['reply'] and '本轮核算未找到' not in result['reply']


def test_session_unknown_or_transport_missing_is_not_time_conflict(sample):
    req,ctx=sample
    req=req.model_copy(update={'goals':(req.goals[1],)})
    for changed in (change_fact(ctx,'show_session','s1',ends_at=None),
                    replace(ctx,operations=ctx.operations.model_copy(update={'journeys':()}))):
        solved,run=diagnose(req,changed)
        assert solved.status=='needs_data' and run() is None


def test_group_relaxation_is_atomic_and_label_matches_verified_route(engine):
    c,t,_=engine
    for pid in ('museum','garden'):
        collect(c,t,'place',pid,'must')
        opening(pid,opens_at='2030-05-01T07:00:00+08:00',closes_at='2030-05-01T22:00:00+08:00')
        with connect() as db:
            old=facts.records(db)['place',pid]
            updated=old.model_copy(update={'suggested_stay_min':480})
            db.execute('UPDATE city_facts SET record_json=? WHERE kind=? AND id=?',
                (updated.model_dump_json(),'place',pid))
    result=plan(engine,starts_at='2030-05-01T07:00:00+08:00',ends_at='2030-05-01T22:00:00+08:00').json()
    assert result['status']=='infeasible',result
    choice=option(result,'relax:conflicts')
    assert set(choice['object_keys'])=={'place:museum','place:garden'}
    assert '先排' in choice['label']
    response=c.post(base(t)+'/exploration/answer',json=answer_body(c,t,result,option_id=choice['id']))
    assert response.status_code==200,response.text
    assert all(i['priority']=='want' for i in response.json()['basket']['items'])
    agreed=plan(engine,**result['constraints']).json()
    assert agreed['status']=='feasible'
    assert len(agreed['planning']['result']['itineraries'][0]['activities'])==1
    assert c.get(base(t)).json()['route_version']==0


def test_session_outside_requested_window_has_no_session_reason_and_checked_end(engine):
    c,t,_=engine;collect(c,t,'show','s1','must')
    result=plan(engine,ends_at='2030-05-01T10:00:00+08:00').json()
    assert result['status']=='infeasible' and result['conflicts'][0]['reason_code']=='no_session'
    assert not any(o['id']=='adjust:start' for o in result['options'])
    assert apply_patch(engine,result,option(result,'adjust:end'))['status']=='feasible'


def test_counterfactuals_respect_business_day_limits(sample,monkeypatch):
    req,ctx=sample
    goals=tuple(Goal(goal_id=pid,target_kind='place',target_id=pid,required=True,duration_seconds=21600) for pid in ('museum','garden','restaurant'))
    # Three six-hour visits cannot jointly fit a 15-hour day.
    for pid in ('museum','garden','restaurant'):
        ctx=change_fact(ctx,'open_rule','open:'+pid,opens_at=req.starts_at.replace(hour=7),closes_at=req.ends_at.replace(hour=22))
    req=req.model_copy(update={'goals':goals,'starts_at':req.starts_at.replace(hour=7),'ends_at':req.ends_at.replace(hour=22)})
    _,run=diagnose(req,ctx);original=exploration.engine.plan_trip;calls=[]
    def solve(candidate,context):calls.append(candidate);return original(candidate,context)
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    result=run()
    assert result and len(calls)<=5
    assert all(r.starts_at.hour>=7 and r.ends_at.hour<=22 and r.business_date==req.business_date for r in calls)
    assert not any(o['kind']=='constraints' for o in result['options'])


def test_conflict_preview_is_idempotent_and_shares_enriched_context(engine,monkeypatch):
    c,t,_=engine;collect(c,t,'place','museum','must')
    opening(opens_at='2030-05-01T08:00:00+08:00',closes_at='2030-05-01T12:00:00+08:00')
    enriched=[];contexts=[];original=exploration.engine.plan_trip
    def enrich(ctx,req,**kwargs):enriched.append(ctx);return ctx
    def solve(req,ctx):contexts.append(ctx);return original(req,ctx)
    monkeypatch.setattr(exploration.engine.live_travel,'enrich',enrich)
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    response=plan(engine,starts_at='2030-05-01T14:00:00+08:00')
    result=response.json();count=len(contexts)
    assert result['status']=='infeasible' and len(enriched)>1
    enrich_count=len(enriched)
    assert contexts and all(ctx is enriched[0] for ctx in contexts)
    assert c.post(base(t)+'/exploration/preview',json=__import__('json').loads(response.request.content)).json()==result
    assert len(contexts)==count and len(enriched)==enrich_count
    assert c.get(base(t)+'/exploration/constraints').json()['result']==result
    assert c.get(base(t)).json()['route_version']==0


def test_orchestration_uses_specific_conflict_reply(city,monkeypatch):
    from test_city_assistant import path
    from test_city_agent import run
    from test_planning_understanding import model,field
    from test_exploration import rid
    c=city['client']
    response=c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum','priority':'must'}})
    assert response.status_code==200,response.text
    opening(opens_at='2030-05-01T08:00:00+08:00',closes_at='2030-05-01T12:00:00+08:00')
    text='2030-05-01下午两点到晚上八点，从[合成测试]origin出发，返回[合成测试]hotel'
    model(monkeypatch,text,[field('date','2030-05-01','2030-05-01'),field('start_time','14:00','下午两点'),
        field('end_time','20:00','晚上八点'),field('origin','[合成测试]origin','[合成测试]origin'),
        field('destination','[合成测试]hotel','[合成测试]hotel')])
    task=run(city,text);result=task['result']['planning_workflow']['result']
    assert result['status']=='infeasible' and result['conflicts']
    assert result['reply'] in task['result']['reply']
    assert '资料或计算条件不足' not in task['result']['reply']


def test_relaxation_matches_preference_projection_when_must_becomes_want(engine):
    c,t,_=engine
    for pid in ('museum','garden'):
        collect(c,t,'place',pid,'must')
        opening(pid,opens_at='2030-05-01T07:00:00+08:00',closes_at='2030-05-01T22:00:00+08:00')
        with connect() as db:
            old=facts.records(db)['place',pid]
            updated=old.model_copy(update={'suggested_stay_min':480,**({'place_type':'stage'} if pid=='museum' else {})})
            db.execute('UPDATE city_facts SET record_json=? WHERE kind=? AND id=?',
                (updated.model_dump_json(),'place',pid))
    result=plan(engine,starts_at='2030-05-01T07:00:00+08:00',ends_at='2030-05-01T22:00:00+08:00',
        preferences={'avoid':['shows']}).json()
    choice=option(result,'relax:conflicts')
    assert choice['label'].endswith('先排[合成测试]garden')
    response=c.post(base(t)+'/exploration/answer',json=answer_body(c,t,result,option_id=choice['id']))
    assert response.status_code==200,response.text
    agreed=plan(engine,**result['constraints']).json()
    assert agreed['status']=='feasible'
    assert [a['place_id'] for a in agreed['planning']['result']['itineraries'][0]['activities']]==['garden']


def timed_travel(monkeypatch,*,count=2,modes=('walking',),destination=None):
    from test_live_travel_multi_day_cache import travel_trip,Provider
    from test_multi_day_travel import daily
    from app.city.engine import live_travel
    multi,ctx,_=travel_trip(monkeypatch,count=count)
    req=daily(multi,multi.days[0])
    req=req.model_copy(update={'modes':modes,'destination_place_id':destination or req.destination_place_id,
        'goals':tuple(g.model_copy(update={'duration_seconds':5400}) for g in req.goals),
        'ends_at':req.starts_at+timedelta(hours=2)})
    ctx=live_travel.enrich(ctx,req,provider=Provider(max_requests=1000))
    return req,ctx


def test_closed_afternoon_skips_travel_and_solve_until_feasible_earlier_start(monkeypatch):
    from app.city.engine import live_travel
    from test_live_travel_multi_day_cache import Provider
    req,ctx=timed_travel(monkeypatch)
    req=req.model_copy(update={'starts_at':req.starts_at.replace(hour=16,minute=30),
        'ends_at':req.ends_at.replace(hour=18),
        'goals':(req.goals[0],req.goals[1].model_copy(update={'required':False}))})
    ctx=change_fact(ctx,'open_rule','open:candidate:0',closes_at=req.starts_at.replace(hour=15))
    _,run=diagnose(req,ctx);provider=Provider();prepared=[];enriched=[];solved=[]
    prepare=exploration.engine.prepare;enrich=live_travel.enrich;solve=exploration.engine.plan_trip
    monkeypatch.setattr(live_travel,'provider_from_environment',lambda:provider)
    def preflight(candidate,context):
        prepared.append((candidate,context,provider.request_count));return prepare(candidate,context)
    def track(context,candidate,**kwargs):
        enriched.append((candidate,provider.request_count));return enrich(context,candidate,**kwargs)
    def checked(candidate,context):solved.append(candidate);return solve(candidate,context)
    monkeypatch.setattr(exploration.engine,'prepare',preflight)
    monkeypatch.setattr(live_travel,'enrich',track)
    monkeypatch.setattr(exploration.engine,'plan_trip',checked)
    result=run();choice=option(result,'adjust:start')
    assert choice['patch']=={'starts_at':req.starts_at.replace(hour=13).isoformat()}
    delayed=[row for row in prepared if row[0].ends_at>req.ends_at and row[0].business_date==req.business_date]
    assert len(delayed)==1 and delayed[0][1] is ctx
    # 改为想去与原时段相同，先 enrich；随后才是提前开始候选。
    assert enriched[0][1]==0 and enriched[0][0].starts_at==req.starts_at and not any(g.required for g in enriched[0][0].goals)
    assert any(c.starts_at==req.starts_at.replace(hour=13) for c,_ in enriched)
    assert all(candidate.ends_at<=req.ends_at or candidate.business_date!=req.business_date for candidate,_ in enriched)
    assert all(candidate.ends_at<=req.ends_at or candidate.business_date!=req.business_date for candidate in solved)
    assert '已核算能排进' in choice['label'] and '没来得及' not in result['reply']
    assert provider.closed==1


@pytest.mark.parametrize('target_kind',['place','session'])
def test_all_prepare_time_conflicts_skip_provider_and_report_no_feasible_option(monkeypatch,target_kind):
    from app.city.engine import live_travel
    from test_live_travel_multi_day_cache import Provider
    req,ctx=timed_travel(monkeypatch,count=1)
    if target_kind=='place':
        for rule in tuple(ctx.facts.values()):
            if isinstance(rule,facts.OpenRule) and rule.place_id=='candidate:0':
                ctx=change_fact(ctx,'open_rule',rule.fact_id,opens_at=rule.opens_at.replace(hour=6),
                    closes_at=rule.closes_at.replace(hour=7))
    else:
        req=req.model_copy(update={'starts_at':req.starts_at.replace(hour=14),'ends_at':req.ends_at.replace(hour=16),
            'goals':(Goal(goal_id='show',target_kind='session',target_id='s1',kind='show',required=True),)})
        ctx=change_fact(ctx,'show_session','s1',stage_place_id='candidate:0')
    solved=exploration.engine.plan_trip(req,ctx);provider=Provider();created=[];prepared=[];prepare=exploration.engine.prepare
    def factory():created.append(provider);return provider
    def preflight(candidate,context):prepared.append(candidate);return prepare(candidate,context)
    monkeypatch.setattr(exploration.engine,'prepare',preflight)
    monkeypatch.setattr(live_travel,'provider_from_environment',factory)
    monkeypatch.setattr(live_travel,'enrich',lambda *a,**kw:pytest.fail('time conflict needs no travel'))
    monkeypatch.setattr(exploration.engine,'plan_trip',lambda *a:pytest.fail('time conflict needs no solve'))
    goal=req.goals[0]
    result=exploration._conflict_adjustments(solved,req,ctx,{goal.goal_id:'place:'+goal.target_id},
        {goal.goal_id:{'name':'[合成测试]'+goal.target_id,'status':'pending','locked':True}},replanning=target_kind=='session')
    assert len(prepared)==(4 if target_kind=='place' else 1) and not created and provider.request_count==0
    assert not any(o.get('patch') for o in result['options'])
    assert '本轮核算未找到延后、提前或换一天的可行方案' in result['reply']
    assert '没来得及' not in result['reply']


@pytest.mark.parametrize('demo_reference',[False,True])
def test_changed_horizon_enriches_each_candidate_and_reuses_quotes(monkeypatch,demo_reference):
    from app.city.engine import live_travel
    from test_live_travel_multi_day_cache import Provider
    req,ctx=timed_travel(monkeypatch);ctx=replace(ctx,demo_reference=demo_reference)
    candidate=req.model_copy(update={'ends_at':req.ends_at+timedelta(hours=2)})
    before=exploration.engine.plan_trip(candidate,ctx)
    assert before.status=='needs_data' and any(i.code=='TRAVEL_DATA_INCOMPLETE' for i in before.issues)
    solved,run=diagnose(req,ctx)
    assert solved.status=='infeasible'
    provider=Provider();created=[];enrichments=[];enrich=live_travel.enrich
    def factory():created.append(provider);return provider
    def track(context,candidate,**kwargs):
        enriched=enrich(context,candidate,**kwargs)
        enrichments.append((candidate,kwargs,enriched));return enriched
    monkeypatch.setattr(live_travel,'provider_from_environment',factory)
    monkeypatch.setattr(live_travel,'enrich',track)
    result=run();choice=option(result,'adjust:end')
    assert choice['patch']=={'ends_at':candidate.ends_at.isoformat()}
    assert '已核算能排进' in choice['label'] and '可以这样调整' in result['reply']
    assert created==[provider] and provider.closed==1
    assert len(enrichments)>1 and all(row[1]['provider'] is provider for row in enrichments)
    assert all(row[1]['quotes'] is enrichments[0][1]['quotes'] for row in enrichments)
    assert enrichments[-1][1]['metrics']['cache_hits']>0
    keys=[(*call[:-1],call[-1].strftime('%H:%M')) for call in provider.calls]
    assert len(keys)==len(set(keys))==provider.request_count
    checked=next(context for request,_,context in enrichments if request.ends_at==candidate.ends_at and len(request.goals)==2)
    assert exploration.engine.plan_trip(candidate,checked).status=='feasible'


def test_request_limit_stops_candidates_and_reports_unfinished(monkeypatch):
    from app.city.engine import live_travel
    from test_live_travel_multi_day_cache import Provider
    req,ctx=timed_travel(monkeypatch,count=6,modes=('walking','driving','transit'),destination='candidate:5')
    _,run=diagnose(req,ctx);provider=Provider();calls=[];enrich=live_travel.enrich
    monkeypatch.setattr(live_travel,'provider_from_environment',lambda:provider)
    def track(*args,**kwargs):calls.append(args[1]);return enrich(*args,**kwargs)
    monkeypatch.setattr(live_travel,'enrich',track)
    result=run()
    assert provider.max_requests==provider.request_count==30 and provider.closed==1
    assert len(calls)==2<len(req.goals) and all(len(candidate.goals)==1 for candidate in calls)
    assert not any(o.get('patch') for o in result['options'])
    assert '没来得及核算完' in result['reply'] and '稍后再试' in result['reply']
    assert '本轮核算未找到' not in result['reply']


def test_enrichment_time_uses_total_budget_and_closes_provider(monkeypatch):
    from app.city.engine import live_travel
    from test_live_travel_multi_day_cache import Clock,TimedProvider
    req,ctx=timed_travel(monkeypatch);_,run=diagnose(req,ctx);clock=Clock()
    provider=TimedProvider(clock,seconds=8);calls=[];enrich=live_travel.enrich
    monkeypatch.setattr(exploration,'time',SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(live_travel.time,'sleep',clock.sleep)
    monkeypatch.setattr(live_travel,'provider_from_environment',lambda:provider)
    def track(*args,**kwargs):calls.append(kwargs);return enrich(*args,**kwargs)
    monkeypatch.setattr(live_travel,'enrich',track)
    monkeypatch.setattr(exploration.engine,'plan_trip',lambda *a:pytest.fail('budget expired before solve'))
    result=run()
    assert len(calls)==1 and calls[0]['deadline']==15
    assert provider.request_count==2 and provider.closed==1
    assert '没来得及核算完' in result['reply'] and not any(o.get('patch') for o in result['options'])


def test_all_infeasible_candidates_keep_original_reply(sample,monkeypatch):
    req,ctx=sample
    req=req.model_copy(update={'goals':(Goal(goal_id='visit',target_kind='place',target_id='museum',required=True),),
        'ends_at':req.starts_at+timedelta(minutes=40),'starts_at':req.starts_at.replace(hour=7)})
    # Force an initial conflict; an infeasible maximum extension prunes smaller ones.
    solved=SimpleNamespace(itineraries=(),status='infeasible',issues=(SimpleNamespace(code='HARD_CONSTRAINT_CONFLICT'),),metrics={})
    calls=[]
    def solve(candidate,context):calls.append(candidate);return SimpleNamespace(status='infeasible',itineraries=())
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    result=exploration._conflict_adjustments(solved,req,ctx,{'visit':'place:museum'},
        {'visit':{'name':'[合成测试]museum','status':'pending'}})
    assert len(calls)==3 and not any(o.get('patch') for o in result['options'])
    assert '本轮核算未找到延后、提前或换一天的可行方案' in result['reply']
    assert '没来得及核算完' not in result['reply']


def test_date_and_relaxation_candidates_also_enrich_new_horizons(monkeypatch):
    from app.city.engine import live_travel
    from test_live_travel_multi_day_cache import Provider
    req,ctx=timed_travel(monkeypatch,count=1)
    rule=ctx.planning_rules('candidate:0',req.business_date,req.starts_at,req.ends_at)[0]
    ctx=change_fact(ctx,'open_rule',rule.fact_id,opens_at=req.starts_at.replace(hour=7),closes_at=req.starts_at.replace(hour=8))
    tomorrow=req.model_copy(update={'business_date':req.business_date+timedelta(days=1),
        'starts_at':req.starts_at+timedelta(days=1),'ends_at':req.ends_at+timedelta(days=1)})
    before=exploration.engine.plan_trip(tomorrow,ctx)
    assert before.status=='needs_data' and any(i.code=='TRAVEL_DATA_INCOMPLETE' for i in before.issues)
    _,run=diagnose(req,ctx);provider=Provider();calls=[];enrich=live_travel.enrich
    monkeypatch.setattr(live_travel,'provider_from_environment',lambda:provider)
    def track(context,candidate,**kwargs):calls.append(candidate);return enrich(context,candidate,**kwargs)
    monkeypatch.setattr(live_travel,'enrich',track)
    result=run()
    assert option(result,'adjust:date')['patch']=={'business_date':tomorrow.business_date.isoformat()}
    assert any(candidate.business_date==tomorrow.business_date for candidate in calls)
    assert any(not any(goal.required for goal in candidate.goals) for candidate in calls)
    keys=[(*call[:-1],call[-1].strftime('%H:%M')) for call in provider.calls]
    assert len(keys)==len(set(keys))==provider.request_count and provider.closed==1


def test_provider_is_closed_if_candidate_enrichment_raises(monkeypatch):
    from app.city.engine import live_travel
    from test_live_travel_multi_day_cache import Provider
    req,ctx=timed_travel(monkeypatch);_,run=diagnose(req,ctx);provider=Provider()
    monkeypatch.setattr(live_travel,'provider_from_environment',lambda:provider)
    def fail(*args,**kwargs):raise RuntimeError('synthetic enrichment failure')
    monkeypatch.setattr(live_travel,'enrich',fail)
    with pytest.raises(RuntimeError,match='synthetic enrichment failure'):run()
    assert provider.closed==1
