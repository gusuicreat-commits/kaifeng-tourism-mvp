"""Bounded assignment search over dated, offline snapshots; no write or provider calls."""
import time
from dataclasses import replace
from .models import Goal, Requirements, DayItinerary, MultiDayItinerary, SolveResultV2, Issue
from .prepare import prepare
from .solver import solve
from .travel import TravelTimeService, travel_seconds, walking_metres, journey_cost_upper
from ..schema import validate_overnights
from .. import costing

MAX_ASSIGNMENTS=8
MIN_SOLVE_SECONDS=0.05


def day_requirements(req,day,goals,objective,seconds):
    return Requirements(business_date=day.business_date,starts_at=day.starts_at,ends_at=day.ends_at,
        origin_place_id=day.origin_place_id,destination_place_id=day.destination_place_id,
        goals=tuple(Goal.model_validate(g.model_dump(exclude={'allowed_dates','fixed_date'})) for g in goals),
        modes=day.modes or req.modes,pace=day.pace or req.pace,objective=objective,
        max_walking_seconds=day.max_walking_seconds,
        pricing=req.pricing,
        access_confirmations=day.access_confirmations if day.access_confirmations is not None else tuple(
            a.place_id for a in req.access_confirmations if day.business_date in a.allowed_dates),
        show_buffer_seconds=req.show_buffer_seconds,solve_seconds=seconds,alternatives=1)


def objectives(req):
    choices=['less_waiting' if req.objective=='less_waiting' else 'balanced','less_walking','lower_cost']
    result=([req.objective]+[o for o in choices if o!=req.objective])[:req.alternatives]
    if req.pricing and req.pricing.hard_limit and 'lower_cost' not in result:result.append('lower_cost')
    return result


def applicable(goal,day):
    return (day.business_date in goal.allowed_dates and (not goal.fixed_date or goal.fixed_date==day.business_date)
        and (not goal.window_start or goal.window_start<day.ends_at)
        and (not goal.window_end or goal.window_end>day.starts_at))


def evidence_mode(days,overnights):
    ranks=('verified_with_estimates','reference_with_estimates','synthetic','unknown')
    evidence=dict(zip(('verified','reference','synthetic','unknown'),ranks))
    modes=[d.itinerary.evidence_mode for d in days]+[evidence[v] for o in overnights for v in (o.place_evidence,o.stay_evidence)]
    return max(modes,key=ranks.index)


def assemble(req,days,objective,ctx=None):
    summary=costing.summarize(req,days,ctx) if req.pricing is not None and ctx is not None else None
    return MultiDayItinerary(schema_version='city-itinerary/2',start_date=req.start_date,end_date=req.end_date,
        days=tuple(days),overnights=req.overnights,objectives=(objective,),
        active_seconds=sum(d.itinerary.total_seconds for d in days),
        elapsed_seconds=int((days[-1].itinerary.returns_at-req.days[0].starts_at).total_seconds()),
        travel_seconds=sum(d.itinerary.travel_seconds for d in days),
        walking_distance_m=sum(d.itinerary.walking_distance_m for d in days),
        known_cost_upper_fen=summary.known_total_fen if summary else sum(d.itinerary.known_cost_upper_fen for d in days),
        cost_unknown=bool(summary.unknown_items) if summary else bool(req.overnights) or any(d.itinerary.cost_unknown for d in days),
        evidence_mode=evidence_mode(days,req.overnights),budget=summary)


def signature(plan):
    return (plan.model_dump(exclude={'objectives','metrics','days'}),tuple(
        (d.model_dump(exclude={'requirements','itinerary'}),d.requirements.model_dump(exclude={'objective','solve_seconds'}),
         d.itinerary.model_dump(exclude={'objectives','score'})) for d in plan.days))


def rank_plan(req,plan,objective):
    selected={a.goal_id for d in plan.days for a in d.itinerary.activities}
    reward=sum(1+g.importance for g in req.goals if not g.required and g.goal_id in selected)
    empty_days=sum(not d.itinerary.activities and not d.itinerary.ongoing for d in plan.days)
    waiting=sum(a.wait_seconds for d in plan.days for a in d.itinerary.activities)
    if objective=='less_walking':cost=(plan.walking_distance_m,plan.travel_seconds,plan.active_seconds)
    elif objective=='lower_cost':cost=((plan.cost_unknown,) if req.pricing else ())+ (plan.known_cost_upper_fen,plan.travel_seconds,plan.active_seconds)
    elif objective=='less_waiting':cost=(waiting,plan.travel_seconds,plan.active_seconds,plan.walking_distance_m)
    else:cost=(2*plan.travel_seconds+waiting+plan.walking_distance_m/2+plan.active_seconds,)
    return cost+(-reward,empty_days) if req.pricing and req.pricing.hard_limit and objective=='lower_cost' else (-reward,empty_days)+cost


def assignments(req,problems,available,objective,expired):
    """Beam siblings retain moves/swaps; constrained required goals enter before flexible goals."""
    goals={g.goal_id:g for g in req.goals}
    variants={(g.goal_id,i):[a for a in p.activities if a.goal_id==g.goal_id]
              for g in req.goals for i,p in enumerate(problems) if i in available[g.goal_id]}
    ordered=sorted((g for g in req.goals if available[g.goal_id]),key=lambda g:(not g.required,
        len(available[g.goal_id]),min(a.latest-a.earliest for i in available[g.goal_id] for a in variants[g.goal_id,i]),
        -g.importance,g.goal_id))
    truncated=False;states=[tuple(() for _ in req.days)]
    def score(state):
        overflow=0;loads=[];missing=0;secondary=0;reward=0
        for i,ids in enumerate(state):
            p=problems[i];load=sum(min(a.duration for a in variants[gid,i]) for gid in ids)
            horizon=(req.days[i].ends_at-req.days[i].starts_at).total_seconds()
            overflow+=max(0,load-horizon);loads.append(load/horizon)
            reward+=sum(1+goals[gid].importance for gid in ids if not goals[gid].required)
            for gid in ids:
                costs=[]
                for a in variants[gid,i]:
                    legs=(p.matrix.get((p.requirements.origin_place_id,a.place_id),()),
                          p.matrix.get((a.place_id,p.requirements.destination_place_id),()))
                    if all(legs):
                        if objective=='less_walking':costs.append(sum(min(walking_metres(j) for j in leg) for leg in legs))
                        elif objective=='lower_cost':
                            if a.cost is not None and all(any(journey_cost_upper(j,req.pricing) is not None for j in leg) for leg in legs):
                                costs.append(a.cost+sum(min(journey_cost_upper(j,req.pricing) for j in leg if journey_cost_upper(j,req.pricing) is not None) for leg in legs))
                        else:costs.append(sum(min(travel_seconds(j) for j in leg) for leg in legs))
                if costs:secondary+=min(costs)
                else:missing+=1
        return (overflow,-reward,missing,sum(not ids for ids in state),secondary,max(loads),sum(v*v for v in loads),state)
    for goal in ordered:
        if expired():return (),True
        expanded=[]
        for state in states:
            for i in available[goal.goal_id]:
                if len(state[i])>=24:continue
                expanded.append(tuple(ids+(goal.goal_id,) if j==i else ids for j,ids in enumerate(state)))
            if not goal.required:expanded.append(state)
        expanded.sort(key=score)
        truncated|=len(expanded)>MAX_ASSIGNMENTS
        states=expanded[:MAX_ASSIGNMENTS]
        if not states:break
    return states,truncated


def plan_multi_day(req,ctx,*,clock=None,deadline=None):
    clock=clock or time.monotonic
    seconds=req.trip_solve_seconds if req.trip_solve_seconds is not None else min(3,len(req.days))
    started=clock();solve_deadline=started+seconds
    if deadline is not None:solve_deadline=min(solve_deadline,deadline)
    metrics={'algorithm':'bounded dated assignment + OR-Tools CP-SAT','global_optimum_claimed':False,
        'assignment_elapsed_ms':0,'solve_elapsed_ms':[],'validation_elapsed_ms':0,'travel_elapsed_ms':0,
        'provider_requests':0,'assignment_attempts':0,'search_truncated':False,'timeout_scope':None,'defaults':{}}
    blocked=[];plans=[];failures=[];warnings=list(ctx.demo_warnings())
    def expired():return clock()>=solve_deadline
    def response(status,issues=()):
        metrics['elapsed_ms']=round((clock()-started)*1000,3)
        if metrics['search_truncated']:
            warnings.append('分天搜索未穷尽，不证明全局最优或无解。')
            if metrics['timeout_scope'] is None:metrics['timeout_scope']='assignment_bound'
        if any(p.cost_unknown for p in plans):warnings.append('费用资料不全，省钱仅按已知费用比较；请核对费用明细中的未计入项。' if req.pricing else '费用资料不全，省钱仅按已知费用比较；住宿费用尚未纳入。')
        return SolveResultV2(schema_version='city-engine-result/2',status=status,itineraries=tuple(plans[:req.alternatives]),
            computed_at=ctx.assessed_at,issues=tuple(issues),warnings=tuple(dict.fromkeys(warnings)),
            metrics=metrics|{'blocked_candidates':blocked})
    def timeout():
        if deadline is not None and clock()>=deadline:plans.clear()
        metrics.update(search_truncated=True,timeout_scope='whole_trip')
        return response('feasible' if plans else 'timeout',() if plans else (
            Issue(code='SEARCH_TIMEOUT',message='全程限时或有界分配搜索未穷尽；未找到完整方案不证明无解'),))
    try:validate_overnights(req.days,req.overnights,complete=True)
    except ValueError as exc:return response('needs_data',(Issue(code='OVERNIGHT_PENDING',message=str(exc)),))
    problems=[];available={g.goal_id:[] for g in req.goals};travel_service=TravelTimeService(ctx)
    assignment_started=clock()
    for i,day in enumerate(req.days):
        if expired():return timeout()
        goals=[g for g in req.goals if applicable(g,day)]
        dayreq=day_requirements(req,day,goals,req.objective,req.solve_seconds)
        # Availability is per date; a required goal missing here may be available on another day.
        p=prepare(dayreq.model_copy(update={'goals':tuple(g.model_copy(update={'required':False}) for g in dayreq.goals)}),ctx,
            travel_service=travel_service,timings=metrics,clock=clock)
        p=replace(p,requirements=dayreq,activities=[replace(a,required=next(g.required for g in goals if g.goal_id==a.goal_id)) for a in p.activities])
        problems.append(p)
        metrics['defaults'][day.day_id]=p.defaults
        blocked.extend(dict(b,day_id=day.day_id) for b in p.blocked)
        if p.issues:return response('needs_data',tuple(i.model_copy(update={'affected_ids':(day.day_id,)+i.affected_ids}) for i in p.issues))
        for g in goals:
            if any(a.goal_id==g.goal_id for a in p.activities):available[g.goal_id].append(i)
    missing=[Issue(code='REQUIRED_GOAL_UNAVAILABLE',message='必选目标在所有允许日均无适用候选；未借用其他日期资料',
        affected_ids=(g.goal_id,)+tuple(d.day_id for d in req.days if applicable(g,d)))
        for g in req.goals if g.required and not available[g.goal_id]]
    metrics['assignment_elapsed_ms']=round((clock()-assignment_started)*1000,3)
    if missing:return response('needs_data',missing)
    stages=objectives(req);cache={};complete=True
    for stage,objective in enumerate(stages):
        before=clock();states,pruned=assignments(req,problems,available,objective,expired)
        metrics['assignment_elapsed_ms']+=round((clock()-before)*1000,3)
        metrics['search_truncated']|=pruned
        if expired():return timeout()
        remaining_attempts=MAX_ASSIGNMENTS-metrics['assignment_attempts']
        limit=max(1,remaining_attempts//(len(stages)-stage))
        complete&=not pruned and len(states)<=limit
        best=None
        remaining=solve_deadline-clock()
        stages_left=min(len(stages)-stage,max(1,int(remaining/(MIN_SOLVE_SECONDS*len(req.days)))))
        stage_deadline=clock()+remaining*(0.6 if stage==0 and len(stages)>1 else 1/stages_left)
        for state in states[:limit]:
            metrics['assignment_attempts']+=1;days=[]
            for i,(day,ids) in enumerate(zip(req.days,state)):
                key=(objective,i,ids)
                if key in cache:
                    result,dayreq=cache[key]
                else:
                    remaining=min(solve_deadline,stage_deadline)-clock()
                    if remaining<MIN_SOLVE_SECONDS:
                        complete=False;metrics.update(search_truncated=True,timeout_scope='whole_trip');break
                    seconds=min(req.solve_seconds,remaining/max(1,len(req.days)-i))
                    if seconds<MIN_SOLVE_SECONDS:seconds=remaining
                    dayreq=day_requirements(req,day,[g for g in req.goals if g.goal_id in ids],objective,seconds)
                    p=replace(problems[i],requirements=dayreq,activities=[replace(a,id=f'a{index}')
                        for index,a in enumerate(a for a in problems[i].activities if a.goal_id in ids)])
                    before=clock();result=solve(p,deadline=min(solve_deadline,stage_deadline),clock=clock)
                    metrics['solve_elapsed_ms'].append({'day_id':day.day_id,'objective':objective,
                        'elapsed_ms':round((clock()-before)*1000,3),'allocated_seconds':seconds,'status':result.status,
                        'solver_status':result.metrics.get('solver_status')})
                    cache[key]=(result,dayreq)
                warnings.extend(result.warnings)
                if result.status=='timeout' or result.metrics.get('solver_status')=='FEASIBLE':
                    metrics.update(search_truncated=True,timeout_scope='day_solver')
                if result.status!='feasible':
                    failures.append(result.model_copy(update={'issues':tuple(issue.model_copy(update={
                        'affected_ids':(day.day_id,)+issue.affected_ids}) for issue in result.issues)}))
                    if result.status=='timeout':complete=False
                    break
                days.append(DayItinerary(business_date=day.business_date,original_requirements=day,
                    requirements=dayreq,itinerary=result.itineraries[0].model_copy(update={'objectives':()})))
            if len(days)!=len(req.days):continue
            plan=assemble(req,days,objective,ctx)
            if plan.budget and plan.budget.hard_limit and plan.budget.status in ('over','unknown'):
                failures.append(SolveResultV2(schema_version='city-engine-result/2',status='needs_data',computed_at=ctx.assessed_at,
                    issues=(Issue(code='BUDGET_OVER' if plan.budget.status=='over' else 'BUDGET_UNKNOWN',
                        message='当前有界搜索未找到满足全程预算的方案；不证明全局无解。' if plan.budget.status=='over' else '全程费用尚有未知，不能确认满足硬预算。'),)))
                continue
            from .multi_day_validation import validate_multi_day
            before=clock();checked=validate_multi_day(req,ctx,plan)
            metrics['validation_elapsed_ms']+=round((clock()-before)*1000,3)
            if deadline is not None and clock()>=deadline:
                plans.clear();return timeout()
            if checked:return response('invalid',checked)
            if best is None or rank_plan(req,plan,objective)<rank_plan(req,best,objective):best=plan
        if best:
            same=next((i for i,p in enumerate(plans) if signature(p)==signature(best)),None)
            if same is None:plans.append(best)
            else:plans[same]=plans[same].model_copy(update={'objectives':plans[same].objectives+(objective,)})
        if expired():return timeout()
    metrics['search_truncated']|=not complete
    if plans:return response('feasible')
    if not complete:return timeout()
    if failures and all(r.status=='infeasible' for r in failures):
        return response('infeasible',(Issue(code='HARD_CONSTRAINT_CONFLICT',message='所有允许分配均已证实与日内硬约束冲突',
            affected_ids=tuple(g.goal_id for g in req.goals if g.required)),))
    if failures:return response(failures[0].status,failures[0].issues)
    return timeout()
