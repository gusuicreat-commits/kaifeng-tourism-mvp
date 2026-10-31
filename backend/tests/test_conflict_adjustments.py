"""Single-day conflict choices are checked against isolated synthetic engine facts."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
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


def test_no_feasible_adjustment_keeps_only_consent_to_relax(engine):
    c,t,_=engine;collect(c,t,'place','museum','must')
    opening(opens_at='2030-05-01T06:00:00+08:00',closes_at='2030-05-01T07:00:00+08:00')
    result=plan(engine,starts_at='2030-05-01T14:00:00+08:00').json()
    assert result['status']=='infeasible'
    assert not any(o['kind']=='constraints' for o in result['options'])
    assert option(result,'relax:place:museum')['requires_consent']
    assert '或换个地点' in result['reply']
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


def test_all_extra_solves_including_solo_are_capped_at_five(sample,monkeypatch):
    req,ctx=sample
    goals=tuple(Goal(goal_id=pid,target_kind='place',target_id=pid,required=True,duration_seconds=5400) for pid in ('museum','garden'))
    req=req.model_copy(update={'goals':goals,'ends_at':req.starts_at+timedelta(hours=2)})
    _,run=diagnose(req,ctx);original=exploration.engine.plan_trip;calls=[]
    def solve(candidate,context):
        calls.append(candidate)
        assert candidate.alternatives==1 and candidate.solve_seconds<=min(req.solve_seconds,1)
        return original(candidate,context)
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    result=run()
    assert result['conflicts'] and len(calls)==5
    assert option(result,'adjust:end')


def test_budget_stops_after_eight_seconds_and_keeps_verified_options(sample,monkeypatch):
    req,ctx=sample
    req=req.model_copy(update={'goals':(Goal(goal_id='visit',target_kind='place',target_id='museum',required=True),),
        'ends_at':req.starts_at+timedelta(minutes=40)})
    _,run=diagnose(req,ctx);original=exploration.engine.plan_trip;calls=[];ticks=[0]
    monkeypatch.setattr(exploration,'time',SimpleNamespace(monotonic=lambda:ticks[0]))
    def solve(candidate,context):
        calls.append(candidate);result=original(candidate,context)
        if len(calls)==2:ticks[0]=9
        return result
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    result=run()
    assert len(calls)==2 and result['conflicts'][0]['reason_code']=='too_far_alone'
    assert [o['id'] for o in result['options'] if o['kind']=='constraints']==['adjust:end']


def test_uncertain_solve_never_creates_verified_option(sample,monkeypatch):
    req,ctx=sample
    req=req.model_copy(update={'goals':(Goal(goal_id='visit',target_kind='place',target_id='museum',required=True),),
        'ends_at':req.starts_at+timedelta(minutes=40)})
    _,run=diagnose(req,ctx)
    monkeypatch.setattr(exploration.engine,'plan_trip',lambda *a:SimpleNamespace(status='timeout',itineraries=()))
    result=run()
    assert not any(o.get('patch') for o in result['options'])
    assert '已核算' not in result['reply']


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
    def enrich(ctx,req):enriched.append(ctx);return ctx
    def solve(req,ctx):contexts.append(ctx);return original(req,ctx)
    monkeypatch.setattr(exploration.engine.live_travel,'enrich',enrich)
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    response=plan(engine,starts_at='2030-05-01T14:00:00+08:00')
    result=response.json();count=len(contexts)
    assert result['status']=='infeasible' and len(enriched)==1
    assert contexts and all(ctx is enriched[0] for ctx in contexts)
    assert c.post(base(t)+'/exploration/preview',json=__import__('json').loads(response.request.content)).json()==result
    assert len(contexts)==count and len(enriched)==1
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
