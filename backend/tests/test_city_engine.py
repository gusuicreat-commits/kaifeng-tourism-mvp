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
