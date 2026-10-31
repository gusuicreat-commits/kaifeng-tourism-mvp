"""Whole-trip balance and time budgets with synthetic facts and a controlled clock."""
from dataclasses import replace

import pytest
from app.city.budget_models import PricingOptions
from app.city.engine import multi_day
from app.city.engine.models import MultiDayRequirements
from scripts.benchmark_city_engine import case
from test_city_engine import sample,multi_sample


@pytest.fixture
def balanced():
    return multi_sample(case(4))


def test_four_goals_that_fit_one_day_are_spread_without_losing_reward(balanced):
    req,ctx=balanced
    one,one_ctx=multi_sample(case(4),1)
    single=multi_day.plan_multi_day(one,one_ctx,clock=lambda:0)
    assert single.status=='feasible' and len(single.itineraries[0].days[0].itinerary.activities)==4
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:0)
    assert result.status=='feasible',result
    for plan in result.itineraries:
        assert all(d.itinerary.activities for d in plan.days)
        ids=[a.goal_id for d in plan.days for a in d.itinerary.activities]
        assert sorted(ids)==sorted(g.goal_id for g in req.goals)


def test_single_goal_leaves_one_empty_day_without_duplication(balanced):
    req,ctx=balanced;req=req.model_copy(update={'goals':req.goals[:1]})
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:0)
    assert result.status=='feasible',result
    for plan in result.itineraries:
        assert sorted(len(d.itinerary.activities) for d in plan.days)==[0,1]
        assert [a.goal_id for d in plan.days for a in d.itinerary.activities]==[req.goals[0].goal_id]


def test_beam_keeps_nonempty_days_before_secondary_cost(balanced):
    req,ctx=balanced
    journeys=tuple(j.model_copy(update={'travel':j.travel.model_copy(update={'lower_seconds':j.travel.upper_seconds*20,
        'upper_seconds':j.travel.upper_seconds*20})}) if j.travel.origin_place_id=='origin' else j
        for j in ctx.operations.journeys)
    ctx=replace(ctx,operations=ctx.operations.model_copy(update={'journeys':journeys}))
    problems=[multi_day.prepare(multi_day.day_requirements(req,d,req.goals,req.objective,1),ctx) for d in req.days]
    states,_=multi_day.assignments(req,problems,{g.goal_id:[0,1] for g in req.goals},req.objective,lambda:False)
    assert states and all(states[0])
    assert sum(map(len,states[0]))==4


@pytest.mark.parametrize('objective',['balanced','less_walking','less_waiting','lower_cost'])
def test_rank_keeps_reward_before_empty_days_and_counts_ongoing(balanced,objective):
    req,ctx=balanced
    plan=multi_day.plan_multi_day(req,ctx,clock=lambda:0).itineraries[0]
    acts=tuple(a for d in plan.days for a in d.itinerary.activities)
    days=tuple(d.model_copy(update={'itinerary':d.itinerary.model_copy(update={
        'activities':acts if i==0 else ()})}) for i,d in enumerate(plan.days))
    empty=plan.model_copy(update={'days':days})
    ongoing=empty.model_copy(update={'days':(days[0],days[1].model_copy(update={
        'itinerary':days[1].itinerary.model_copy(update={'ongoing':{'stop_id':'reserved'}})}))})
    assert multi_day.rank_plan(req,ongoing,objective)<multi_day.rank_plan(req,empty,objective)
    optional=next(g for g in req.goals if not g.required)
    fewer=ongoing.model_copy(update={'days':(days[0].model_copy(update={'itinerary':days[0].itinerary.model_copy(update={
        'activities':tuple(a for a in acts if a.goal_id!=optional.goal_id)})}),ongoing.days[1])})
    assert multi_day.rank_plan(req,empty,objective)<multi_day.rank_plan(req,fewer,objective)


def test_hard_budget_lower_cost_stays_before_empty_days(balanced):
    req,ctx=balanced
    plan=multi_day.plan_multi_day(req,ctx,clock=lambda:0).itineraries[0]
    req=req.model_copy(update={'pricing':PricingOptions(total_budget_fen=100000,hard_limit=True)})
    acts=tuple(a for d in plan.days for a in d.itinerary.activities)
    cheap=plan.model_copy(update={'known_cost_upper_fen':100,'days':tuple(d.model_copy(update={
        'itinerary':d.itinerary.model_copy(update={'activities':acts if i==0 else ()})}) for i,d in enumerate(plan.days))})
    spread=plan.model_copy(update={'known_cost_upper_fen':200})
    assert multi_day.rank_plan(req,cheap,'lower_cost')<multi_day.rank_plan(req,spread,'lower_cost')
    assert multi_day.rank_plan(req,spread,'balanced')<multi_day.rank_plan(req,cheap,'balanced')
    assert multi_day.rank_plan(req,spread.model_copy(update={'known_cost_upper_fen':100}),'lower_cost')<multi_day.rank_plan(req,cheap,'lower_cost')


@pytest.mark.parametrize('count,explicit,budget',[(1,None,1),(2,None,2),(3,None,3),(3,1,1),(3,0.2,0.2)])
def test_default_budget_scales_by_days_but_explicit_budget_is_preserved(sample,monkeypatch,count,explicit,budget):
    req,ctx=multi_sample(sample,count,goals=[],alternatives=1,**({'trip_solve_seconds':explicit} if explicit is not None else {}))
    ticks=[0.0];deadlines=[];original=multi_day.solve
    def solve(problem,*,deadline,clock):
        deadlines.append(deadline)
        assert problem.requirements.solve_seconds<=req.solve_seconds
        result=original(problem)
        ticks[0]+=problem.requirements.solve_seconds
        return result
    monkeypatch.setattr(multi_day,'solve',solve)
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:ticks[0])
    assert result.status=='feasible',result
    assert len(deadlines)==count and deadlines==pytest.approx([budget]*count)
    assert ticks[0]==pytest.approx(budget)


@pytest.mark.parametrize('trip_seconds',[None,1])
def test_trip_budget_survives_request_roundtrip(sample,monkeypatch,trip_seconds):
    req,ctx=multi_sample(sample,3,goals=[],alternatives=1,**({'trip_solve_seconds':trip_seconds} if trip_seconds is not None else {}))
    req=MultiDayRequirements.model_validate(req.model_dump())
    ticks=[0.0];deadlines=[];original=multi_day.solve
    def solve(problem,*,deadline,clock):
        deadlines.append(deadline);result=original(problem)
        ticks[0]+=problem.requirements.solve_seconds
        return result
    monkeypatch.setattr(multi_day,'solve',solve)
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:ticks[0])
    assert result.status=='feasible',result
    assert deadlines==pytest.approx([trip_seconds if trip_seconds is not None else 3]*3)


def test_daily_limit_is_independent_of_whole_trip_budget(sample,monkeypatch):
    req,ctx=multi_sample(sample,3,goals=[],solve_seconds=0.2,trip_solve_seconds=3,alternatives=1)
    allocations=[];original=multi_day.solve
    def solve(problem,*,deadline,clock):
        allocations.append(problem.requirements.solve_seconds)
        assert deadline==3
        return original(problem)
    monkeypatch.setattr(multi_day,'solve',solve)
    assert multi_day.plan_multi_day(req,ctx,clock=lambda:0).status=='feasible'
    assert allocations==pytest.approx([0.2]*3)


@pytest.mark.parametrize('objective',['balanced','less_walking','lower_cost'])
def test_first_stage_survives_cold_start_using_less_than_forty_percent(sample,monkeypatch,objective):
    req,ctx=multi_sample(sample,3,goals=[],trip_solve_seconds=1,objective=objective)
    ticks=[0.0];calls=[];original=multi_day.solve
    def solve(problem,*,deadline,clock):
        calls.append((problem.requirements.objective,deadline))
        result=original(problem)
        ticks[0]+=0.36 if len(calls)==1 else 0.04
        return result
    monkeypatch.setattr(multi_day,'solve',solve)
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:ticks[0])
    assert result.status=='feasible',result
    assert result.itineraries[0].objectives[0]==req.objective
    assert len(result.itineraries[0].days)==3
    assert [d for o,d in calls if o==req.objective][:3]==pytest.approx([0.6]*3)


@pytest.mark.parametrize('alternatives',[2,3])
def test_later_stages_share_remaining_budget_equally(sample,monkeypatch,alternatives):
    req,ctx=multi_sample(sample,3,goals=[],trip_solve_seconds=1,alternatives=alternatives)
    ticks=[0.0];deadlines=[];original=multi_day.solve
    def solve(problem,*,deadline,clock):
        deadlines.append(deadline)
        result=original(problem)
        ticks[0]+=problem.requirements.solve_seconds
        return result
    monkeypatch.setattr(multi_day,'solve',solve)
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:ticks[0])
    assert result.status=='feasible',result
    assert deadlines==pytest.approx([0.6]*3+([0.8]*3 if alternatives==3 else [])+[1]*3)
    assert ticks[0]==pytest.approx(1)
