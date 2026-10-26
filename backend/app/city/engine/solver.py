"""One CP-SAT engine for initial and remaining itineraries. No generic solver reimplementation."""
import time
from datetime import timedelta
from .models import Itinerary, ScheduledActivity, ScheduledLeg, SolveResult, Issue
from .travel import travel_seconds, walking_metres, entry_deadline, entry_opening, journey_cost_upper


def solve(problem):
    started=time.monotonic()
    try:return _solve(problem)
    except Exception:
        return SolveResult(status='unavailable',computed_at=problem.context.assessed_at,
            issues=(Issue(code='SOLVER_UNAVAILABLE',message='规划计算不可用，当前正式行程未修改'),),
            metrics={'candidate_count':len(problem.activities),'algorithm':'OR-Tools CP-SAT','global_optimum_claimed':False,'elapsed_ms':round((time.monotonic()-started)*1000,3)})


def _solve(problem):
    from ortools.sat.python import cp_model
    started=time.monotonic();req=problem.requirements;acts=problem.activities;ctx=problem.context
    meta={'algorithm':'OR-Tools CP-SAT circuit/time-windows','candidate_count':len(acts),
          'blocked_candidates':problem.blocked,'defaults':problem.defaults,'global_optimum_claimed':False}
    def response(status,issues=(),plans=(),solver_status=None):
        reference = any(r.planning_status == 'reference' for r in ctx.facts.values())
        return SolveResult(status=status,itineraries=tuple(plans),issues=tuple(issues),computed_at=ctx.assessed_at,
                           warnings=(ctx.demo_warnings() if ctx.demo_reference else
                                     (('采用有来源的参考资料；营业及供应以现场为准。',) if reference else ()))+
                           ('交通为预计耗时；额外缓冲和默认停留是规划参数。',),
                           metrics=meta|{'elapsed_ms':round((time.monotonic()-started)*1000,3),'solver_status':solver_status})
    if problem.issues:return response('needs_data',problem.issues)
    if req.budget_fen is not None and problem.history_cost_unknown and not ctx.demo_reference:
        return response('needs_data',[Issue(code='SPENT_COST_REQUIRED',message='有途中进度时需明确已支出费用，不能把预算重新从零计算')])
    if not acts and req.goals:return response('needs_data',[Issue(code='NO_CANDIDATES',message='没有可用候选，无法生成行程')])
    n=len(acts)+2;last=n-1;budget=int((req.ends_at-req.starts_at).total_seconds())
    pid=lambda i:req.origin_place_id if i==0 else req.destination_place_id if i==last else acts[i-1].place_id
    duration=lambda i:0 if i in (0,last) else acts[i-1].duration
    model=cp_model.CpModel();active=[model.NewBoolVar(f'active_{i}') for i in range(n)]
    times=[model.NewIntVar(0,budget,f't_{i}') for i in range(n)]
    model.Add(active[0]==1);model.Add(active[last]==1);model.Add(times[0]==0)
    objectives=['balanced','less_walking','lower_cost']
    if req.objective=='less_waiting':objectives[0]='less_waiting'
    objectives=([req.objective]+[o for o in objectives if o!=req.objective])[:req.alternatives]
    circuit=[(last,0,model.NewConstant(1))];arcs={};reentries={};groups={}
    penalties={o:[] for o in objectives};soft_upper={o:1 for o in objectives};rewards=[]
    for i,a in enumerate(acts,1):
        model.Add(times[i]>=a.earliest).OnlyEnforceIf(active[i])
        model.Add(times[i]<=a.latest).OnlyEnforceIf(active[i])
        model.Add(times[i]+a.duration<=budget).OnlyEnforceIf(active[i])
        circuit.append((i,i,active[i].Not()));groups.setdefault(a.goal_id,[]).append(active[i])
        rewards.append(a.reward*active[i])
        if 'lower_cost' in penalties and a.cost is not None:
            penalties['lower_cost'].append(a.cost*2*active[i]);soft_upper['lower_cost']+=a.cost*2
    for g in req.goals:
        choices=groups.get(g.goal_id,[])
        model.Add(sum(choices)==1 if g.required else sum(choices)<=1)
    sessions={}
    for i,a in enumerate(acts,1):
        if a.session_id:sessions.setdefault(a.session_id,[]).append(active[i])
    for choices in sessions.values():model.Add(sum(choices)<=1)
    for i in range(n-1):
        for j in range(1,n):
            if i==j:continue
            for k,journey in enumerate(problem.matrix.get((pid(i),pid(j)),())):
                var=model.NewBoolVar(f'arc_{i}_{j}_{k}');arcs[i,j,k]=(var,journey);circuit.append((i,j,var))
                elapsed=travel_seconds(journey)
                model.Add(times[j]>=times[i]+duration(i)+elapsed).OnlyEnforceIf(var)
                if j!=last and acts[j-1].arrival_deadline is not None:
                    model.Add(times[i]+duration(i)+elapsed<=acts[j-1].arrival_deadline).OnlyEnforceIf(var)
                opening=entry_opening(journey,req,ctx)
                if opening:model.Add(times[i]+duration(i)+elapsed>=int((opening-req.starts_at).total_seconds())).OnlyEnforceIf(var)
                cutoff=entry_deadline(journey,req,ctx)
                if cutoff:model.Add(times[i]+duration(i)+elapsed<=int((cutoff-req.starts_at).total_seconds())).OnlyEnforceIf(var)
                for b in journey.boundaries:
                    if b.direction=='entry':reentries.setdefault(b.park_id,[]).append(var)
                metres=walking_metres(journey);change_cost=0
                if problem.prior_order:
                    old=problem.prior_order
                    left=acts[i-1].goal_id if i else None;right=acts[j-1].goal_id if j!=last else None
                    pairs=set(zip((None,)+old,old+(None,)))
                    if (left,right) not in pairs:change_cost=120
                for objective in objectives:
                    cost=elapsed+change_cost
                    if objective=='less_walking':cost+=round(metres*4)
                    elif objective=='lower_cost':cost+=(journey_cost_upper(journey) or 0)*2
                    else:cost+=round(metres/2)
                    penalties[objective].append(cost*var);soft_upper[objective]+=cost
                gap={'relaxed':600,'standard':180,'compact':0}[req.pace]
                if gap and j!=last:
                    deficit=model.NewIntVar(0,gap,f'pace_{i}_{j}_{k}')
                    model.Add(deficit>=gap-(times[j]-times[i]-duration(i)-elapsed)).OnlyEnforceIf(var)
                    model.Add(deficit==0).OnlyEnforceIf(var.Not())
                    for objective in objectives:
                        penalties[objective].append(deficit);soft_upper[objective]+=gap
    if len(arcs)>12000:return response('invalid',[Issue(code='ARC_LIMIT',message='交通/活动组合过多，请缩小候选范围')])
    model.Add(sum(active[1:-1])>=(1 if req.goals else 0))
    model.Add(sum(active[1:-1])<=problem.max_selected)
    model.AddCircuit(circuit)
    if req.max_walking_seconds is not None:
        model.Add(sum(travel_seconds(journey)*var for var,journey in arcs.values()
                      if journey.travel.mode=='walking')<=req.max_walking_seconds)
    for park,entries in reentries.items():
        rules=ctx.planning_rules(park,req.business_date,req.starts_at,req.ends_at)
        if not any(r.reentry=='allowed' and ctx.ready(r,req.starts_at,req.ends_at) for r in rules):
            model.Add(sum(entries)+(1 if park in ctx.parks(req.origin_place_id) else 0)<=1)
    # Optional fixed future activities keep relative order; exact time windows are supplied by the service.
    for left,right in zip(problem.fixed_order,problem.fixed_order[1:]):
        for i,a in enumerate(acts,1):
            for j,b in enumerate(acts,1):
                if a.goal_id==left and b.goal_id==right:
                    model.Add(times[j]>=times[i]+a.duration).OnlyEnforceIf([active[i],active[j]])
    if req.budget_fen is not None:
        model.Add(problem.history_cost_fen+sum((a.cost or 0)*active[i] for i,a in enumerate(acts,1))+
                  sum((journey_cost_upper(journey) or 0)*var for var,journey in arcs.values())<=req.budget_fen)
    # All user-goal rewards dominate the bounded secondary penalties. This is explicit lexicographic weighting.
    def materialize(solver, objectives):
        indices=[0];chosen=[]
        while indices[-1]!=last:
            outgoing=[(j,var,journey) for (i,j,k),(var,journey) in arcs.items() if i==indices[-1] and solver.Value(var)]
            if len(outgoing)!=1:raise RuntimeError('Invalid circuit output')
            j,var,journey=outgoing[0];chosen.append((var,journey));indices.append(j)
            if len(indices)>n:raise RuntimeError('Cycle output')
        t=req.starts_at;stops=[];legs=[];known_cost=problem.history_cost_fen;unknown_cost=problem.history_cost_unknown
        for index,(_,journey) in zip(indices[1:],chosen):
            arrives=t+timedelta(seconds=travel_seconds(journey))
            legs.append(ScheduledLeg(journey=journey,departs_at=t,arrives_at=arrives))
            known_cost+=journey_cost_upper(journey) or 0;unknown_cost|=journey_cost_upper(journey) is None
            if index!=last:
                a=acts[index-1];start=req.starts_at+timedelta(seconds=solver.Value(times[index]));end=start+timedelta(seconds=a.duration)
                stops.append(ScheduledActivity(activity_id=a.id,goal_id=a.goal_id,place_id=a.place_id,kind=a.kind,
                    session_id=a.session_id,arrives_at=arrives,starts_at=start,departs_at=end,duration_seconds=a.duration,
                    wait_seconds=int((start-arrives).total_seconds()),required=a.required,adjustable=not a.required,
                    data_status='synthetic' if ctx.synthetic else ('reference' if ctx.facts['place',a.place_id].planning_status=='reference' else 'verified'),evidence_refs=a.evidence_refs))
                t=end;known_cost+=a.cost or 0;unknown_cost|=a.cost is None
        returned=legs[-1].arrives_at
        return Itinerary(objectives=objectives,activities=tuple(stops),legs=tuple(legs),returns_at=returned,total_seconds=int((returned-req.starts_at).total_seconds()),
                         travel_seconds=sum(travel_seconds(l.journey) for l in legs),walking_distance_m=sum(walking_metres(l.journey) for l in legs),
                         known_cost_upper_fen=known_cost,cost_unknown=unknown_cost,
                         history_cost_fen=problem.history_cost_fen,history_cost_unknown=problem.history_cost_unknown,history_cost_basis=problem.history_cost_basis,
                         score={'goal_reward':sum(acts[i-1].reward for i in indices[1:-1]),
                                'waiting_seconds':sum(s.wait_seconds for s in stops),
                                'change_penalty_seconds_per_broken_adjacency':120 if problem.prior_order else 0},
                         evidence_mode='synthetic' if ctx.synthetic else ('reference_with_estimates' if any(s.data_status=='reference' for s in stops) else 'verified_with_estimates')),chosen
    plans=[];statuses=[];selected_arcs=[];solves_done=0
    max_solves=len(objectives)+max(0,req.alternatives-1)
    for attempt,objective in enumerate(objectives):
        remaining=req.solve_seconds-(time.monotonic()-started)
        if remaining<=0:break
        finish_weight=4 if objective=='less_waiting' else 1
        upper=soft_upper[objective]+budget*(finish_weight+n)
        model.Minimize(-(upper+1)*sum(rewards)+sum(penalties[objective])+finish_weight*times[last]+sum(times[1:-1]))
        # Reserve time for each preference; all solves reuse this model and travel matrix.
        remaining=req.solve_seconds-(time.monotonic()-started)
        if remaining<=0:break
        solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=remaining/max(1,max_solves-solves_done)
        solves_done+=1
        solver.parameters.num_search_workers=1;solver.parameters.random_seed=42
        status=solver.Solve(model);statuses.append(solver.StatusName(status))
        if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            if not plans or status==cp_model.INFEASIBLE:break
            continue
        plan,chosen=materialize(solver,(objective,))
        # A second implementation rechecks all hard constraints before ANY feasible result is exposed.
        from .validation import validate_itinerary
        checked=validate_itinerary(problem,plan)
        if checked:return response('invalid',checked,solver_status=','.join(statuses))
        same=next((i for i,p in enumerate(plans) if p.model_dump(exclude={'objectives'})==plan.model_dump(exclude={'objectives'})),None)
        if same is None:
            plans.append(plan);selected_arcs.append(chosen)
        else:plans[same]=plans[same].model_copy(update={'objectives':plans[same].objectives+(objective,)})
    # If preference-specific solves collapse to too few routes, search again with
    # the primary objective while excluding every selected arc combination. These
    # backups remain explicitly unlabeled so the UI cannot imply a preference.
    for chosen in selected_arcs if len(plans)<req.alternatives else ():
        model.Add(sum(var for var,_ in chosen)<=len(chosen)-1)
    while plans and len(plans)<req.alternatives:
        remaining=req.solve_seconds-(time.monotonic()-started)
        if remaining<=0:break
        objective=objectives[0]
        finish_weight=4 if objective=='less_waiting' else 1
        upper=soft_upper[objective]+budget*(finish_weight+n)
        model.Minimize(-(upper+1)*sum(rewards)+sum(penalties[objective])+finish_weight*times[last]+sum(times[1:-1]))
        remaining=req.solve_seconds-(time.monotonic()-started)
        if remaining<=0:break
        solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=remaining/max(1,max_solves-solves_done)
        solves_done+=1
        solver.parameters.num_search_workers=1;solver.parameters.random_seed=42
        status=solver.Solve(model);statuses.append(solver.StatusName(status))
        if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):break
        plan,chosen=materialize(solver,())
        from .validation import validate_itinerary
        checked=validate_itinerary(problem,plan)
        if checked:return response('invalid',checked,solver_status=','.join(statuses))
        plans.append(plan);model.Add(sum(var for var,_ in chosen)<=len(chosen)-1)
    if plans:return response('feasible',plans=plans,solver_status=','.join(statuses))
    incomplete=any(not problem.matrix.get((pid(i),pid(j))) for i in range(n-1) for j in range(1,n) if i!=j)
    if statuses and statuses[-1]=='INFEASIBLE':
        return response('needs_data' if incomplete else 'infeasible',[Issue(
            code='TRAVEL_DATA_INCOMPLETE' if incomplete else 'HARD_CONSTRAINT_CONFLICT',
            message='缺少适用方向、方式、出入口或费用的交通依据' if incomplete else '必选活动、时间窗、返程、预算或再入园约束冲突',
            affected_ids=tuple(g.goal_id for g in req.goals if g.required),
            suggestions=('增加可用时间、改选场次或由用户明确调整必选目标；不会自动放宽。',))],solver_status=','.join(statuses))
    return response('timeout',[Issue(code='SEARCH_TIMEOUT',message='限时内未找到可行方案；这不证明行程不可能')],solver_status=','.join(statuses))
