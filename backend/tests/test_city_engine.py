"""Controlled synthetic cases only. No real map calls, LLM, or production DB imports."""
import copy
import json
from dataclasses import replace
from datetime import datetime,timedelta
from pathlib import Path
from uuid import uuid4
import pytest
from app.city import facts
from app.city.schema import Stop
from app.city.engine.models import Requirements, OperationalBundle, Goal, Itinerary
from app.city.engine.context import PlanningContext
from app.city.engine.tools import plan_trip,replan_trip
from app.city.engine.prepare import prepare
from app.city.engine.validation import validate_itinerary,compare_itineraries

FIXTURE=Path(__file__).parent/'fixtures/engine_synthetic.json'


@pytest.fixture
def sample():
    d=json.loads(FIXTURE.read_text());records=facts.FactBundle.model_validate(d['facts']).records
    ctx=PlanningContext({(r.kind,r.fact_id):r for r in records},OperationalBundle.model_validate(d['operations']),datetime.fromisoformat(d['assessed_at']),True)
    return Requirements.model_validate(d['requirements']),ctx


def change_fact(ctx,kind,identity,**patch):
    records=dict(ctx.facts);before=records[kind,identity]
    records[kind,identity]=type(before).model_validate(before.model_dump()|patch)
    return replace(ctx,facts=records)


def goal(gid,pid,**kwargs):return Goal(goal_id=gid,target_kind='place',target_id=pid,**kwargs)


def test_initial_joint_plan_standalone_and_return_has_real_leg(sample):
    req,ctx=sample;r=plan_trip(req,ctx)
    assert r.status=='feasible',r
    assert r.itineraries[0].objectives[0]=='balanced'
    assert {o for p in r.itineraries for o in p.objectives}=={'balanced','less_walking','lower_cost'}
    for p in r.itineraries:
        assert {a.goal_id for a in p.activities}=={'visit','show','lunch'}
        assert len(p.legs)==4 and p.legs[-1].journey.travel.destination_place_id=='hotel'
        assert p.returns_at<=req.ends_at and p.evidence_mode=='synthetic'
        assert not validate_itinerary(prepare(req,ctx),p)
    assert r.metrics['candidate_count']==3 and r.metrics['elapsed_ms']>=0


def test_two_fixed_shows_same_program_and_two_places(sample):
    req,ctx=sample
    g=Goal(goal_id='second',target_kind='session',target_id='s2',kind='show',required=True)
    r=plan_trip(req.model_copy(update={'goals':req.goals+(g,)}),ctx)
    assert r.status=='feasible'
    assert {a.session_id for a in r.itineraries[0].activities if a.kind=='show'}=={'s1','s2'}


def test_same_stage_distinct_sessions_are_distinct_activities(sample):
    req,ctx=sample;ctx=change_fact(ctx,'show_session','s2',stage_place_id='stage1')
    g=Goal(goal_id='second',target_kind='session',target_id='s2',kind='show',required=True)
    r=plan_trip(req.model_copy(update={'goals':req.goals+(g,)}),ctx)
    assert r.status=='feasible'
    assert [a.place_id for a in r.itineraries[0].activities if a.kind=='show']==['stage1','stage1']


def test_program_selects_an_available_session_and_no_duplicates(sample):
    req,ctx=sample;ctx=change_fact(ctx,'show_session','s1',business_status='cancelled')
    g=Goal(goal_id='program',target_kind='program',target_id='program',kind='show',required=True)
    req=req.model_copy(update={'goals':(g,)})
    r=plan_trip(req,ctx);assert r.status=='feasible'
    assert r.itineraries[0].activities[0].session_id=='s2'


def test_overlapping_fixed_shows_are_infeasible_without_dropping_required(sample):
    req,ctx=sample
    ctx=change_fact(ctx,'show_session','s2',starts_at=req.starts_at.replace(hour=11,minute=5),ends_at=req.starts_at.replace(hour=11,minute=35))
    g=Goal(goal_id='second',target_kind='session',target_id='s2',kind='show',required=True)
    r=plan_trip(req.model_copy(update={'goals':req.goals+(g,)}),ctx)
    assert r.status=='infeasible' and not r.itineraries


@pytest.mark.parametrize('mutation',['closed_restaurant','cancelled_show','no_show_end','missing_place','expired_source','no_supply','reservation_unknown','unknown_cost'])
def test_missing_or_unavailable_required_data_never_feasible(sample,mutation):
    req,ctx=sample
    if mutation=='closed_restaurant':ctx=change_fact(ctx,'open_rule','open:restaurant',opens_at=req.starts_at.replace(hour=19),closes_at=req.starts_at.replace(hour=22))
    elif mutation=='cancelled_show':ctx=change_fact(ctx,'show_session','s1',business_status='cancelled')
    elif mutation=='no_show_end':ctx=change_fact(ctx,'show_session','s1',ends_at=None)
    elif mutation=='missing_place':ctx=change_fact(ctx,'place','museum',longitude=None,latitude=None)
    elif mutation=='expired_source':ctx=change_fact(ctx,'source','fixture:source',effective_to='2021-01-01T00:00:00+08:00')
    elif mutation=='no_supply':ctx=change_fact(ctx,'supply','supply',validation_status='unverified')
    elif mutation=='reservation_unknown':ctx=change_fact(ctx,'open_rule','open:museum',reservation='unknown')
    else:
        req=req.model_copy(update={'budget_fen':10000});ctx=replace(ctx,operations=ctx.operations.model_copy(update={'costs':()}))
    r=plan_trip(req,ctx)
    assert r.status in ('needs_data','infeasible') and not r.itineraries


def test_return_time_and_total_time_budget_are_hard(sample):
    req,ctx=sample
    req=req.model_copy(update={'goals':(goal('museum','museum',required=True,duration_seconds=1800),),'ends_at':req.starts_at+timedelta(seconds=2100)})
    assert plan_trip(req,ctx).status=='infeasible'


def test_hard_budget_counts_dining_and_transport(sample):
    req,ctx=sample
    req=req.model_copy(update={'budget_fen':1000})
    assert plan_trip(req,ctx).status=='infeasible'
    assert plan_trip(req.model_copy(update={'budget_fen':2000}),ctx).status=='feasible'


def test_unknown_cost_without_hard_budget_is_explicit(sample):
    req,ctx=sample;ctx=replace(ctx,operations=ctx.operations.model_copy(update={'costs':()}))
    r=plan_trip(req,ctx);assert r.status=='feasible' and r.itineraries[0].cost_unknown


def test_missing_traffic_never_zero_fills(sample):
    req,ctx=sample;ctx=replace(ctx,operations=ctx.operations.model_copy(update={'journeys':()}))
    r=plan_trip(req,ctx);assert r.status=='needs_data' and r.issues[0].code=='TRAVEL_DATA_INCOMPLETE'


def test_less_walking_selects_other_mode_and_budget_can_disallow_it(sample):
    req,ctx=sample;req=req.model_copy(update={'modes':('walking','driving')})
    a=plan_trip(req.model_copy(update={'objective':'less_walking'}),ctx).itineraries[0]
    b=plan_trip(req.model_copy(update={'objective':'lower_cost'}),ctx).itineraries[0]
    assert a.walking_distance_m<b.walking_distance_m
    assert a.known_cost_upper_fen>b.known_cost_upper_fen


def three_objective_context(req,ctx,choices=None):
    records=dict(ctx.facts)
    for pid in ('museum','garden'):
        records['place',pid]=records['place',pid].model_copy(update={'place_type':'restaurant'})
        for kind,patch in [('vendor',{'place_id':pid}),('supply',{'vendor_id':'vendor:'+pid})]:
            record=records[kind,kind].model_copy(update={'fact_id':kind+':'+pid,**patch})
            records[kind,record.fact_id]=record
    # Equal dining rewards: fastest, shortest walking, and cheapest are different vendors.
    choices=choices or {'restaurant':(60,200,1500),'museum':(300,10,3000),'garden':(500,500,0)}
    journeys=[]
    for j in ctx.operations.journeys:
        left=j.travel.origin_place_id;right=j.travel.destination_place_id
        pid=right if left=='origin' else left if right=='hotel' else None
        if j.travel.mode!='walking' or pid not in choices:continue
        seconds,metres,_=choices[pid]
        journeys.append(j.model_copy(update={'distance_m':metres,'travel':j.travel.model_copy(update={'lower_seconds':seconds,'upper_seconds':seconds})}))
    costs=tuple(c.model_copy(update={'lower_fen':choices[c.place_id][2],'upper_fen':choices[c.place_id][2]})
                for c in ctx.operations.costs if c.place_id in choices)
    req=req.model_copy(update={'goals':(Goal(goal_id='meal',target_kind='food',target_id='food',kind='dining',required=True,duration_seconds=60),),
                               'pace':'compact'})
    return req,replace(ctx,facts=records,operations=OperationalBundle(journeys=tuple(journeys),costs=costs))


@pytest.mark.parametrize('objective',['balanced','less_walking','lower_cost'])
def test_three_objectives_choose_different_plans_and_explicit_preference_is_first(sample,objective):
    req,ctx=three_objective_context(*sample);req=req.model_copy(update={'objective':objective})
    result=plan_trip(req,ctx)
    assert result.status=='feasible' and len(result.itineraries)==3
    assert result.itineraries[0].objectives==(objective,)
    plans={p.objectives[0]:p for p in result.itineraries}
    assert {o:p.activities[0].place_id for o,p in plans.items()}=={'balanced':'restaurant','less_walking':'museum','lower_cost':'garden'}
    assert plans['less_walking'].walking_distance_m<plans['balanced'].walking_distance_m
    assert plans['lower_cost'].known_cost_upper_fen<plans['balanced'].known_cost_upper_fen
    assert all(not validate_itinerary(prepare(req,ctx),p) for p in result.itineraries)


def test_identical_plans_merge_then_add_validated_backups(sample,monkeypatch):
    from app.city.engine import validation
    req,ctx=three_objective_context(*sample,choices={'restaurant':(60,200,0),'museum':(300,300,3000),'garden':(500,500,5000)})
    checked=[];original=validation.validate_itinerary
    def check(problem,plan):
        checked.append((problem,plan.objectives));return original(problem,plan)
    monkeypatch.setattr(validation,'validate_itinerary',check)
    result=plan_trip(req,ctx)
    assert result.status=='feasible' and len(result.itineraries)==3
    assert result.itineraries[0].objectives==('balanced','less_walking','lower_cost')
    assert all(p.objectives==() for p in result.itineraries[1:])
    assert [p.activities[0].place_id for p in result.itineraries]==['restaurant','museum','garden']
    assert [o for _,o in checked]==[('balanced',),('less_walking',),('lower_cost',),(),()]
    assert all(p is checked[0][0] and p.context is ctx for p,_ in checked)


def test_replan_objectives_merge_with_requested_preference_first_and_unknown_cost(sample):
    req,ctx=sample;req=req.model_copy(update={'goals':(req.goals[0],),'objective':'less_walking'})
    ctx=replace(ctx,operations=ctx.operations.model_copy(update={'costs':()}))
    result=replan_trip(req,ctx)
    assert result.status=='feasible' and len(result.itineraries)==1
    assert result.itineraries[0].objectives==('less_walking','balanced','lower_cost')
    assert result.itineraries[0].cost_unknown and result.itineraries[0].known_cost_upper_fen==0


def test_objective_solves_share_total_time_budget(sample,monkeypatch):
    from types import SimpleNamespace
    from ortools.sat.python import cp_model
    from app.city.engine import solver
    req,ctx=sample;elapsed=0;limits=[];original=cp_model.CpSolver.Solve
    monkeypatch.setattr(solver,'time',SimpleNamespace(monotonic=lambda:elapsed))
    def timed(instance,model):
        nonlocal elapsed
        limits.append(instance.parameters.max_time_in_seconds)
        result=original(instance,model);elapsed+=limits[-1]
        return result
    monkeypatch.setattr(cp_model.CpSolver,'Solve',timed)
    result=plan_trip(req,ctx)
    assert result.status=='feasible' and len(limits)==5
    assert sum(limits)==pytest.approx(req.solve_seconds)
    assert limits==pytest.approx([req.solve_seconds/5]*5)


def test_alternatives_one_returns_only_one_solution(sample):
    req,ctx=sample;req=req.model_copy(update={'alternatives':1})
    result=plan_trip(req,ctx)
    assert len(result.itineraries)==1 and result.itineraries[0].objectives==('balanced',)


def test_existing_less_waiting_preference_stays_main_with_walking_and_cost_alternatives(sample):
    req,ctx=sample;req=req.model_copy(update={'objective':'less_waiting'})
    result=plan_trip(req,ctx)
    assert result.status=='feasible' and result.itineraries[0].objectives[0]=='less_waiting'
    assert {o for p in result.itineraries for o in p.objectives}=={'less_waiting','less_walking','lower_cost'}


def test_admission_deadline_and_buffer_use_earlier_cutoff(sample):
    req,ctx=sample;ctx=change_fact(ctx,'show_session','s1',admission_deadline=req.starts_at.replace(hour=10,minute=40))
    req=req.model_copy(update={'starts_at':req.starts_at.replace(hour=10,minute=42),'goals':(req.goals[1],)})
    assert plan_trip(req,ctx).status=='infeasible'


def test_demo_context_cannot_be_used_as_verified_production(sample):
    req,ctx=sample
    assert plan_trip(req,replace(ctx,synthetic=False)).status=='needs_data'


def test_validator_rejects_tampered_times_costs_required_and_return(sample):
    req,ctx=sample;p=plan_trip(req,ctx).itineraries[0];problem=prepare(req,ctx)
    invalid=p.model_copy(update={'returns_at':req.ends_at+timedelta(seconds=1),'cost_unknown':True})
    codes={i.code for i in validate_itinerary(problem,invalid)}
    assert 'RETURN_DEADLINE' in codes and 'COST_TOTAL' in codes
    invalid=p.model_copy(update={'activities':p.activities[1:]})
    assert validate_itinerary(problem,invalid)


def test_replanning_uses_same_solver_preserves_completed_and_ongoing(sample):
    req,ctx=sample
    prefix=Stop(stop_id='done',place_id='garden',kind='visit',progress='completed',actual_start=req.starts_at,actual_end=req.starts_at+timedelta(minutes=10))
    remaining=req.model_copy(update={'starts_at':req.starts_at+timedelta(minutes=20),'origin_place_id':'garden'})
    r=replan_trip(remaining,ctx,completed_prefix=(prefix,),ongoing={'stop_id':'rest','remaining_basis':'user_reported'})
    assert r.status=='feasible' and r.itineraries[0].completed_prefix==(prefix,)
    assert r.itineraries[0].ongoing['stop_id']=='rest'
    assert not validate_itinerary(prepare(remaining,ctx),r.itineraries[0])
    assert r.metrics['algorithm']==plan_trip(req,ctx).metrics['algorithm']


def test_compare_exposes_added_removed_reordered_and_times(sample):
    req,ctx=sample;old=plan_trip(req,ctx).itineraries[0]
    g=goal('new','garden',duration_seconds=1200)
    newreq=req.model_copy(update={'goals':req.goals[1:]+(g,)})
    new=plan_trip(newreq,ctx).itineraries[0];diff=compare_itineraries(old,new)
    assert diff['removed']==['visit'] and diff['added']==['new'] and diff['must_confirm']


def test_preference_order_changes_with_explicit_target_windows(sample):
    req,ctx=sample
    gs=(goal('a','museum',required=True,duration_seconds=600),goal('b','garden',required=True,duration_seconds=600))
    a=plan_trip(req.model_copy(update={'goals':gs}),ctx).itineraries[0]
    first=a.activities[0].goal_id
    later=tuple(g.model_copy(update={'window_start':req.starts_at.replace(hour=15)}) if g.goal_id==first else g for g in gs)
    b=plan_trip(req.model_copy(update={'goals':later}),ctx).itineraries[0]
    assert a.activities[0].goal_id!=b.activities[0].goal_id


def test_small_hand_check_optimal_walking_order(sample):
    req,ctx=sample
    req=req.model_copy(update={'goals':(goal('a','museum',required=True,duration_seconds=60),goal('b','garden',required=True,duration_seconds=60)), 'pace':'compact','alternatives':1})
    r=plan_trip(req,ctx);assert r.status=='feasible'
    from itertools import permutations
    distances={ (j.travel.origin_place_id,j.travel.destination_place_id):j.distance_m for j in ctx.operations.journeys if j.travel.mode=='walking'}
    exact=min(sum(distances[x,y] for x,y in zip(('origin',)+p,p+('hotel',))) for p in permutations(('museum','garden')))
    assert r.itineraries[0].walking_distance_m==exact


def park_context(ctx,reentry='forbidden'):
    meta=ctx.facts['place','museum'].model_dump(mode='json');base={k:meta[k] for k in ('version','source_ids','validation_status','verified_at','verification_method','reviewer','effective_from','effective_to','rights_status','is_demo')}
    records=dict(ctx.facts)
    park=facts.Place.model_validate(base|{'kind':'place','fact_id':'park','name':'[TEST] park','place_type':'scenic','longitude':114.3,'latitude':34.8,'coordinate_system':'GCJ02'})
    gate=facts.Place.model_validate(base|{'kind':'place','fact_id':'gate','name':'[TEST] gate','place_type':'entrance','parent_place_id':'park','longitude':114.3,'latitude':34.8,'coordinate_system':'GCJ02'})
    entrance=facts.Entrance.model_validate(base|{'kind':'entrance','fact_id':'entrance','place_id':'park','entrance_place_id':'gate','direction':'both'})
    rule=facts.OpenRule.model_validate(ctx.facts['open_rule','open:museum'].model_dump()|{'fact_id':'open:park','place_id':'park','reentry':reentry})
    for r in [park,gate,entrance,rule]:records[r.kind,r.fact_id]=r
    for pid in ['museum','stage1']:records['place',pid]=records['place',pid].model_copy(update={'parent_place_id':'park'})
    ctx=replace(ctx,facts=records);journeys=[]
    from app.city.engine.models import Boundary
    for j in ctx.operations.journeys:
        origin=set(ctx.parks(j.travel.origin_place_id));dest=set(ctx.parks(j.travel.destination_place_id))
        transitions=[Boundary(park_id=p,gate_place_id='gate',direction=direction,seconds=120,cost_upper_fen=0,evidence=j.travel.evidence)
                     for group,direction in [(origin-dest,'exit'),(dest-origin,'entry')] for p in group]
        journeys.append(j.model_copy(update={'boundaries':tuple(transitions)}))
    return replace(ctx,operations=ctx.operations.model_copy(update={'journeys':tuple(journeys)}))


def test_exit_reentry_rule_is_hard_and_boundary_time_is_counted(sample):
    req,ctx=sample;ctx=park_context(ctx)
    req=req.model_copy(update={'goals':tuple(g.model_copy(update={'window_start':req.starts_at.replace(hour=14)}) if g.goal_id=='visit' else g for g in req.goals)})
    assert plan_trip(req,ctx).status=='infeasible'
    ctx=park_context(sample[1],reentry='allowed');r=plan_trip(req,ctx)
    assert r.status=='feasible'
    p=r.itineraries[0]
    assert any(l.journey.boundaries for l in p.legs)
    assert p.travel_seconds==sum(l.journey.travel.upper_seconds+sum(b.seconds for b in l.journey.boundaries) for l in p.legs)


def test_unknown_park_boundary_never_defaults_to_zero(sample):
    req,ctx=sample;ctx=park_context(ctx,reentry='allowed')
    ctx=replace(ctx,operations=ctx.operations.model_copy(update={'journeys':tuple(j.model_copy(update={'boundaries':()}) for j in ctx.operations.journeys)}))
    assert plan_trip(req,ctx).status=='needs_data'


def test_entrance_last_entry_and_boundary_cost_unknown_are_not_ignored(sample):
    req,ctx=sample;ctx=park_context(ctx,reentry='allowed')
    req=req.model_copy(update={'goals':(req.goals[0],),'starts_at':req.starts_at.replace(hour=18)})
    ctx=change_fact(ctx,'open_rule','open:park',last_entry_at=req.starts_at.replace(hour=17))
    assert plan_trip(req,ctx).status=='infeasible'
    req=sample[0].model_copy(update={'budget_fen':10000});ctx=park_context(sample[1],reentry='allowed')
    ctx=replace(ctx,operations=ctx.operations.model_copy(update={'journeys':tuple(j.model_copy(update={'boundaries':tuple(b.model_copy(update={'cost_upper_fen':None}) for b in j.boundaries)}) for j in ctx.operations.journeys)}))
    assert plan_trip(req,ctx).status=='needs_data'


def test_return_only_replan_is_supported_without_revisiting_completed_goals(sample):
    req,ctx=sample;req=req.model_copy(update={'goals':()})
    r=replan_trip(req,ctx)
    assert r.status=='feasible' and not r.itineraries[0].activities and len(r.itineraries[0].legs)==1


def test_solver_timeout_and_unavailable_are_not_proven_infeasible(sample,monkeypatch):
    from ortools.sat.python import cp_model
    req,ctx=sample
    monkeypatch.setattr(cp_model.CpSolver,'Solve',lambda *a:cp_model.UNKNOWN)
    assert plan_trip(req,ctx).status=='timeout'
    from app.city.engine import solver
    def unavailable(*args):raise RuntimeError('injected')
    monkeypatch.setattr(solver,'_solve',unavailable)
    r=plan_trip(req,ctx);assert r.status=='unavailable' and not r.itineraries


def multi_sample(sample,count=2,goals=None,**patch):
    from multi_day_helpers import requirements
    from app.city.engine.models import MultiDayRequirements
    single,ctx=sample;data=requirements(count)
    for d in data['days']:d['ends_at']=d['business_date']+'T20:00:00+08:00'
    dates=[single.business_date+timedelta(days=i) for i in range(count)]
    data['goals']=goals if goals is not None else [dict(g.model_dump(mode='json'),allowed_dates=[v.isoformat() for v in dates])
        for g in single.goals]
    records=dict(ctx.facts)
    # Explicit synthetic daily records, never a runtime fallback for missing dates.
    for offset in range(1,count):
        for r in ctx.facts.values():
            if not isinstance(r,facts.OpenRule):continue
            changed=r.model_dump()|{'fact_id':r.fact_id+f':day{offset}','business_date':r.business_date+timedelta(days=offset)}
            for key in ('opens_at','closes_at','last_entry_at'):
                if changed[key]:changed[key]+=timedelta(days=offset)
            copy=type(r).model_validate(changed);records[copy.kind,copy.fact_id]=copy
    return MultiDayRequirements.model_validate(data|patch),replace(ctx,facts=records)


@pytest.mark.parametrize('count',[2,3])
def test_multi_day_synthetic_whole_trip_required_once(sample,count):
    from app.city.engine.multi_day_validation import validate_multi_day
    req,ctx=multi_sample(sample,count);result=plan_trip(req,ctx)
    assert result.status=='feasible',result
    assert len(result.itineraries)<=3
    for plan in result.itineraries:
        assert len(plan.days)==count and not validate_multi_day(req,ctx,plan)
        ids=[a.goal_id for d in plan.days for a in d.itinerary.activities]
        sessions=[a.session_id for d in plan.days for a in d.itinerary.activities if a.session_id]
        assert sorted(ids)==['lunch','show','visit'] and len(sessions)==len(set(sessions))
        assert plan.active_seconds==sum(d.itinerary.total_seconds for d in plan.days)
        assert plan.elapsed_seconds>plan.active_seconds and plan.cost_unknown
        assert all(len(d.itinerary.legs)==len(d.itinerary.activities)+1 for d in plan.days)


def test_multi_day_required_only_open_second_day(sample):
    req,ctx=multi_sample(sample,goals=[dict(goal('must','museum',required=True).model_dump(mode='json'),allowed_dates=['2030-05-01','2030-05-02'])])
    records={k:v for k,v in ctx.facts.items() if k!=('open_rule','open:museum')};ctx=replace(ctx,facts=records)
    result=plan_trip(req,ctx);assert result.status=='feasible',result
    for plan in result.itineraries:
        assert not plan.days[0].itinerary.activities
        assert [a.goal_id for a in plan.days[1].itinerary.activities]==['must']


def test_multi_day_fixed_first_day_show_keeps_flexible_required_for_second(sample):
    goals=[dict(goal('flex','museum',required=True,duration_seconds=7200).model_dump(mode='json'),allowed_dates=['2030-05-01','2030-05-02']),
        {'goal_id':'fixed-show','target_kind':'session','target_id':'s1','kind':'show','required':True,'allowed_dates':['2030-05-01','2030-05-02']}]
    req,ctx=multi_sample(sample,goals=goals)
    req=req.model_copy(update={'days':tuple(d.model_copy(update={'ends_at':d.starts_at.replace(hour=12)}) for d in req.days)})
    result=plan_trip(req,ctx);assert result.status=='feasible',result
    for plan in result.itineraries:
        assert [a.goal_id for a in plan.days[0].itinerary.activities]==['fixed-show']
        assert [a.goal_id for a in plan.days[1].itinerary.activities]==['flex']


@pytest.mark.parametrize('kind',['opening','session'])
def test_multi_day_missing_daily_data_never_reuses_first_day(sample,kind):
    if kind=='opening':g=dict(goal('only-second','museum',required=True).model_dump(mode='json'))
    else:g={'goal_id':'only-second','target_kind':'program','target_id':'program','kind':'show','required':True}
    req,ctx=multi_sample(sample,goals=[g|{'allowed_dates':['2030-05-02']}])
    if kind=='opening':ctx=replace(ctx,facts={k:v for k,v in ctx.facts.items() if k!=('open_rule','open:museum:day1')})
    result=plan_trip(req,ctx)
    assert result.status=='needs_data' and not result.itineraries
    assert 'day:2030-05-02' in result.issues[0].affected_ids


def test_multi_day_same_session_two_required_instances_cannot_repeat(sample):
    req,ctx=multi_sample(sample,goals=[{'goal_id':gid,'target_kind':'session','target_id':'s1','kind':'show',
        'required':True,'allowed_dates':['2030-05-01','2030-05-02']} for gid in ('one','two')])
    result=plan_trip(req,ctx)
    assert result.status=='infeasible' and not result.itineraries


def test_multi_day_empty_target_days_require_explicit_transfer_and_stationary_edge(sample):
    req,ctx=multi_sample(sample,3,goals=[])
    result=plan_trip(req,ctx);assert result.status=='feasible',result
    p=result.itineraries[0]
    assert all(not d.itinerary.activities and len(d.itinerary.legs)==1 for d in p.days)
    assert p.days[1].itinerary.legs[0].journey.travel.origin_place_id=='hotel'
    ctx=replace(ctx,operations=ctx.operations.model_copy(update={'journeys':tuple(j for j in ctx.operations.journeys
        if (j.travel.origin_place_id,j.travel.destination_place_id)!=('hotel','hotel'))}))
    assert plan_trip(req,ctx).status=='needs_data'


def test_multi_day_three_objectives_deduplicate_entire_trip_and_respect_less_waiting(sample):
    from app.city.engine.multi_day import signature
    for objective in ('balanced','less_waiting'):
        req,ctx=multi_sample(sample,goals=[],objective=objective)
        result=plan_trip(req,ctx);assert result.status=='feasible',result
        assert len(result.itineraries)==1
        assert result.itineraries[0].objectives==(objective,'less_walking','lower_cost')
        assert len({repr(signature(p)) for p in result.itineraries})==len(result.itineraries)


def test_multi_day_whole_objectives_choose_different_dates_by_whole_cost_and_walk(sample):
    req,ctx=multi_sample(sample,goals=[dict(goal('flex','museum',required=True,duration_seconds=60).model_dump(mode='json'),
        allowed_dates=['2030-05-01','2030-05-02'])],pace='compact')
    journeys=[]
    for j in ctx.operations.journeys:
        pair=(j.travel.origin_place_id,j.travel.destination_place_id)
        seconds,metres,cost=(100,500,0) if pair==('origin','museum') else (1000,1,500) if pair==('hotel','museum') else (0,0,0)
        journeys.append(j.model_copy(update={'travel':j.travel.model_copy(update={'lower_seconds':seconds,'upper_seconds':seconds}),
            'distance_m':metres,'cost_lower_fen':cost,'cost_upper_fen':cost}))
    ctx=replace(ctx,operations=ctx.operations.model_copy(update={'journeys':tuple(journeys)}))
    result=plan_trip(req,ctx);assert result.status=='feasible',result
    assignments={o:next(i for i,d in enumerate(p.days) if d.itinerary.activities)
        for p in result.itineraries for o in p.objectives}
    assert assignments=={'balanced':0,'less_walking':1,'lower_cost':0}
    assert any('仅按已知费用' in w for w in result.warnings)


def test_multi_day_validator_uses_original_required_overnights_dates_and_costs(sample):
    from app.city.engine.multi_day_validation import validate_multi_day
    req,ctx=multi_sample(sample);plan=plan_trip(req,ctx).itineraries[0]
    assert not validate_multi_day(req,ctx,plan)
    changed=plan.model_copy(update={'overnights':(plan.overnights[0].model_copy(update={'place_id':'garden'}),)})
    assert {'OVERNIGHT_CHANGED','OVERNIGHT_CONTINUITY'}<={i.code for i in validate_multi_day(req,ctx,changed)}
    days=[]
    for d in plan.days:
        req2=d.requirements.model_copy(update={'goals':tuple(g.model_copy(update={'required':False}) for g in d.requirements.goals)})
        acts=tuple(a.model_copy(update={'required':False,'adjustable':True}) for a in d.itinerary.activities)
        days.append(d.model_copy(update={'requirements':req2,'itinerary':d.itinerary.model_copy(update={'activities':acts})}))
    assert {'GOAL_REQUIREMENTS_CHANGED','REQUIRED_CHANGED'}<={i.code for i in validate_multi_day(req,ctx,plan.model_copy(update={'days':tuple(days)}))}
    removed=plan.model_copy(update={'days':tuple(d.model_copy(update={'itinerary':d.itinerary.model_copy(update={'activities':()})}) for d in plan.days)})
    assert 'MISSING_REQUIRED' in {i.code for i in validate_multi_day(req,ctx,removed)}
    for field,value,code in [('known_cost_upper_fen',0,'COST_TOTAL'),('cost_unknown',False,'COST_UNKNOWN'),
                             ('elapsed_seconds',plan.active_seconds,'ELAPSED_TOTAL')]:
        assert code in {i.code for i in validate_multi_day(req,ctx,plan.model_copy(update={field:value}))}
    duplicate=plan.days[1].model_copy(update={'itinerary':plan.days[0].itinerary})
    codes={i.code for i in validate_multi_day(req,ctx,plan.model_copy(update={'days':(plan.days[0],duplicate)}))}
    assert 'DUPLICATE_GOAL' in codes and 'DUPLICATE_SESSION' in codes


def test_single_day_never_enters_multi_day_module(sample,monkeypatch):
    from app.city.engine import multi_day
    monkeypatch.setattr(multi_day,'plan_multi_day',lambda *a,**kw:pytest.fail('single day dispatched to multi day'))
    assert plan_trip(*sample).status=='feasible'


@pytest.mark.parametrize('count',[1,2,3])
def test_multi_day_budget_shared_and_no_round_up_of_subminimum_remaining(sample,monkeypatch,count):
    from app.city.engine import multi_day
    req,ctx=multi_sample(sample,count,goals=[],trip_solve_seconds=0.2,alternatives=1)
    ticks=[0.0];allocations=[];original=multi_day.solve
    def limited(problem,*,deadline,clock):
        allocations.append(problem.requirements.solve_seconds)
        assert deadline<=req.trip_solve_seconds and clock()+problem.requirements.solve_seconds<=deadline+1e-8
        result=original(problem)
        ticks[0]+=problem.requirements.solve_seconds
        return result
    monkeypatch.setattr(multi_day,'solve',limited)
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:ticks[0])
    assert result.status=='feasible' and len(allocations)==count
    assert sum(allocations)<=0.2+1e-8 and ticks[0]<=0.2+1e-8
    allocations.clear();ticks[0]=0.16
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:ticks[0],deadline=0.2)
    assert result.status=='timeout' and not allocations


def test_multi_day_bounded_assignment_not_exhausted_never_claims_infeasible(sample,monkeypatch):
    from app.city.engine import multi_day
    from app.city.engine.models import SolveResult
    req,ctx=multi_sample(sample,goals=[dict(goal(f'g{i}','museum',required=True,duration_seconds=60).model_dump(mode='json'),
        allowed_dates=['2030-05-01','2030-05-02']) for i in range(5)])
    monkeypatch.setattr(multi_day,'solve',lambda *a,**kw:SolveResult(status='infeasible',computed_at=ctx.assessed_at))
    result=multi_day.plan_multi_day(req,ctx)
    assert result.status=='timeout' and result.metrics['search_truncated']
    assert result.metrics['assignment_attempts']<=8 and '未穷尽' in result.issues[0].message


@pytest.mark.parametrize('count',[1,2,3])
@pytest.mark.parametrize('size',[5,15,30])
def test_multi_day_synthetic_benchmark(sample,count,size):
    import time
    from scripts.benchmark_city_engine import case
    from app.city.engine.multi_day_validation import validate_multi_day
    single,ctx=case(size)
    req,ctx=multi_sample((single,ctx),count)
    start=time.perf_counter();result=plan_trip(req,ctx);elapsed=(time.perf_counter()-start)*1000
    selected=sum(len(d.itinerary.activities) for d in result.itineraries[0].days) if result.itineraries else 0
    print(f'B2.2 benchmark days={count} goals={size} status={result.status} wall_ms={elapsed:.3f} selected={selected}')
    assert result.status in ('feasible','timeout'),result
    assert result.metrics['assignment_attempts']<=8
    for plan in result.itineraries:assert not validate_multi_day(req,ctx,plan)


def test_multi_day_fixed_date_and_dated_access_are_not_silently_relaxed(sample):
    req,ctx=multi_sample(sample,goals=[dict(goal('must','museum',required=True).model_dump(mode='json'),
        allowed_dates=['2030-05-01','2030-05-02'],fixed_date='2030-05-02')])
    ctx=change_fact(ctx,'open_rule','open:museum:day1',reservation='required')
    result=plan_trip(req,ctx);assert result.status=='needs_data' and not result.itineraries
    from app.city.engine.models import DatedAccessConfirmation
    access=DatedAccessConfirmation(place_id='museum',allowed_dates=(req.start_date,))
    assert plan_trip(req.model_copy(update={'access_confirmations':(access,)}),ctx).status=='needs_data'
    access=access.model_copy(update={'allowed_dates':(req.end_date,)})
    result=plan_trip(req.model_copy(update={'access_confirmations':(access,)}),ctx)
    assert result.status=='feasible',result
    assert all([a.goal_id for a in p.days[1].itinerary.activities]==['must'] for p in result.itineraries)


def test_multi_day_workflow_deadline_includes_final_validation(sample,monkeypatch):
    from app.city.engine import multi_day,multi_day_validation
    req,ctx=multi_sample(sample,goals=[]);ticks=[0.0];original=multi_day_validation.validate_multi_day
    def validate(*args):
        result=original(*args);ticks[0]=0.5;return result
    monkeypatch.setattr(multi_day_validation,'validate_multi_day',validate)
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:ticks[0],deadline=0.5)
    assert result.status=='timeout' and not result.itineraries and result.metrics['search_truncated']


def test_multi_day_preparation_consumes_whole_trip_time_before_any_solver(sample,monkeypatch):
    from app.city.engine import multi_day
    req,ctx=multi_sample(sample,goals=[],trip_solve_seconds=1);ticks=[0.0];original=multi_day.prepare;prepared=[]
    def prepare(*args,**kwargs):
        prepared.append(args[0].business_date);result=original(*args,**kwargs);ticks[0]+=0.6;return result
    monkeypatch.setattr(multi_day,'prepare',prepare)
    monkeypatch.setattr(multi_day,'solve',lambda *a,**kw:pytest.fail('budget exhausted by preparation'))
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:ticks[0])
    assert result.status=='timeout' and not result.itineraries and len(prepared)==2


def test_multi_day_feasible_incumbent_is_still_explicitly_unexhausted(sample,monkeypatch):
    from app.city.engine import multi_day
    req,ctx=multi_sample(sample,goals=[],alternatives=1);original=multi_day.solve
    def incumbent(*args,**kwargs):
        result=original(*args,**kwargs)
        return result.model_copy(update={'metrics':result.metrics|{'solver_status':'FEASIBLE'}})
    monkeypatch.setattr(multi_day,'solve',incumbent)
    result=multi_day.plan_multi_day(req,ctx)
    assert result.status=='feasible' and result.metrics['search_truncated']
    assert result.metrics['timeout_scope']=='day_solver'
    assert any('未穷尽' in warning for warning in result.warnings)


def test_multi_day_small_total_budget_prioritizes_requested_objective(sample):
    req,ctx=multi_sample(sample,1,goals=[],trip_solve_seconds=0.12)
    result=plan_trip(req,ctx)
    assert result.status=='feasible',result
    assert result.itineraries[0].objectives[0]=='balanced'
