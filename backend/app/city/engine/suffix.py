"""Bounded replanning of today's suffix and unstarted future days as one transaction."""
import time
from collections import Counter
from dataclasses import replace
from fastapi import HTTPException
from .models import MultiDayRequirements,MultiDayItinerary,Requirements,TripGoal,DayItinerary,SolveResultV2,Issue
from .multi_day import applicable,day_requirements,assignments,objectives,assemble,signature,rank_plan,MAX_ASSIGNMENTS,MIN_SOLVE_SECONDS
from .prepare import prepare
from .solver import solve
from .tools import replan_trip
from .validation import validate_itinerary
from ..schema import TZ
from ..versions import read_draft
from .future_day import overnight_identity,target_day


def prepare_scope(row,body,ctx,previous,original):
    from .multi_day_service import remaining
    if not isinstance(previous,MultiDayItinerary) or not isinstance(original,MultiDayRequirements):
        raise HTTPException(422,detail='请先确认多日行程，再调整今天及后续天。')
    if row['status'] not in ('ACTIVE','PAUSED'):raise HTTPException(422,detail='请先开始今天的行程；出发前请指定未来单日调整。')
    if body.restore_route_version or body.spent_cost_fen is not None or body.spend_as_of is not None:
        raise HTTPException(422,detail='连续重排不同时恢复历史或更新费用报告，请分别确认。')
    from .multi_day_service import current_day
    draft=read_draft(row['draft_json']);today=current_day(draft,ctx.assessed_at,body.day_id)
    req=body.requirements
    if req.model_dump(exclude={'days','goals','solve_seconds','alternatives'})!=original.model_dump(exclude={'days','goals','solve_seconds','alternatives'}):
        raise HTTPException(409,detail='全程范围、预算、已支付记录、全局偏好及酒店边界保持锁定。')
    if overnight_identity(req.overnights)!=overnight_identity(original.overnights):raise HTTPException(409,detail='请保留已确认酒店衔接。')
    old_days={d.day_id:d for d in original.days};new_days={d.day_id:d for d in req.days}
    active={d.day_id for d in draft.days if d.business_date>=today.business_date and d.status!='closed'}
    if set(old_days)!=set(new_days) or any(new_days[k]!=v for k,v in old_days.items() if k not in active):
        raise HTTPException(409,detail='历史或已关闭日期必须逐字段保持原样。')
    if any(getattr(new_days[today.day_id],k)!=getattr(old_days[today.day_id],k) for k in ('starts_at','origin_place_id')):
        raise HTTPException(409,detail='今天的原始出发条件保持不变，剩余起点与时间由服务器当前位置提供。')
    for d in draft.days:
        if d.business_date>today.business_date and d.day_id in active:target_day(draft,previous,ctx.assessed_at,d.day_id)
    old={g.goal_id:g for g in original.goals};new={g.goal_id:g for g in req.goals}
    current=next(d for d in previous.days if d.day_id==today.day_id)
    pending={s.stop_id for s in today.stops if s.progress=='pending'}
    today_ids={g.goal_id for g in current.requirements.goals}&pending
    if current.itinerary.ongoing:today_ids.add(current.itinerary.ongoing['stop_id'])
    frozen={g.goal_id for d in previous.days if d.day_id not in active for g in d.requirements.goals}
    frozen|={s.stop_id for d in draft.days for s in d.stops if s.progress!='pending'}
    for d in previous.days:
        if d.day_id not in active:
            frozen.update(s.stop_id for s in d.itinerary.completed_prefix)
            if d.itinerary.ongoing:frozen.add(d.itinerary.ongoing['stop_id'])
    skipped=set(body.skipped_goal_ids)
    if not skipped<=today_ids:raise HTTPException(422,detail='此范围仅允许明确跳过今天未完成目标；未来必去目标请逐日核对。')
    for gid,g in old.items():
        after=new.get(gid)
        if gid in frozen and after!=g:raise HTTPException(409,detail='已处理目标和冻结日期的需求不能改写。')
        if g.required and gid not in skipped and (not after or not after.required or any(getattr(g,k)!=getattr(after,k) for k in ('target_kind','target_id','fixed_place_id','fixed_session_id'))):
            raise HTTPException(409,detail='必去目标必须保留，不能静默删除、换目标或降低优先级。')
        if after and g.fixed_date!=after.fixed_date:raise HTTPException(409,detail='固定日期不能在连续重排中放宽。')
        if after and not set(after.allowed_dates)<=set(g.allowed_dates):raise HTTPException(409,detail='跨日仅使用原目标允许日期，不自动放宽。')
    for gid,g in new.items():
        if gid not in old and (not set(g.allowed_dates)<={d.business_date for d in req.days if d.day_id in active}):
            raise HTTPException(409,detail='新增目标只能属于今天及后续未冻结日。')
    # Keep future required activities on their confirmed date, time and realization.
    pins={}
    for d in previous.days:
        if d.business_date<=today.business_date or d.day_id not in active:continue
        for a in d.itinerary.activities:
            if not a.required:continue
            if new.get(a.goal_id)!=old.get(a.goal_id):raise HTTPException(409,detail='未来必去活动已锁定，请使用该日期单独调整后确认。')
            g=new[a.goal_id]
            pins[a.goal_id]=g.model_copy(update={'allowed_dates':(d.business_date,),'fixed_date':d.business_date,
                'fixed_place_id':a.place_id,'fixed_session_id':a.session_id,'window_start':a.starts_at,'window_end':a.departs_at,'duration_seconds':a.duration_seconds})
    # Reuse the existing progress protection against its original daily baseline.
    today_goals=[new[g.goal_id] if g.goal_id in today_ids else g for g in original.goals if g.goal_id not in today_ids or g.goal_id in new]
    today_goals.extend(g for g in req.goals if g.goal_id not in old and g.allowed_dates==(today.business_date,))
    local_req=original.model_copy(update={'goals':tuple(today_goals)})
    local=body.model_copy(update={'scope':'current_day','requirements':local_req})
    daily,prefix,ongoing,fixed,current,today=remaining(row,local,ctx,previous,original)
    target=new_days[today.day_id]
    changes={k:getattr(target,k) for k in ('ends_at','destination_place_id')}
    for key in ('modes','pace','objective','max_walking_seconds','access_confirmations'):
        value=getattr(target,key)
        if value is not None:changes[key]=value
    try:daily=Requirements.model_validate(daily.model_dump()|changes)
    except ValueError:raise HTTPException(422,detail='今天的剩余条件无效或时间不足。') from None
    if ongoing and new.get(ongoing['stop_id'])!=old.get(ongoing['stop_id']):raise HTTPException(409,detail='正在进行的目标需求保持原样；只报告实际剩余时长。')
    resolved=frozen|{s.stop_id for s in prefix}|({ongoing['stop_id']} if ongoing else set())|skipped
    search_days=[d.model_copy(update={'starts_at':daily.starts_at,'origin_place_id':daily.origin_place_id}) if d.day_id==today.day_id else d for d in req.days if d.day_id in active]
    dates={d.business_date for d in search_days}
    goals=[]
    for g in req.goals:
        if g.goal_id in resolved:continue
        g=pins.get(g.goal_id,g)
        allowed=tuple(d for d in g.allowed_dates if d in dates)
        if not allowed:
            if g.required:raise HTTPException(409,detail='未完成必去目标没有适用的剩余日期。')
            continue
        candidate=g.model_copy(update={'allowed_dates':allowed})
        if candidate.fixed_date and candidate.fixed_date not in dates or not any(applicable(candidate,d) for d in search_days):
            if candidate.required:raise HTTPException(409,detail='必去目标的固定日期或时间窗已不在剩余范围内，请核对历史记录。')
            continue
        goals.append(candidate)
    if not set(fixed)<={g.goal_id for g in goals if today.business_date in g.allowed_dates}:
        raise HTTPException(422,detail='指定顺序只能包含今天保留的目标。')
    # Fixed order applies today; do not permit its members to disappear onto another date.
    protected={g.goal_id:g for g in daily.goals}
    goals=[TripGoal.model_validate(protected[g.goal_id].model_dump()|{'allowed_dates':(today.business_date,),'fixed_date':today.business_date}) if g.goal_id in fixed else g for g in goals]
    search=MultiDayRequirements.model_validate(req.model_dump()|{'start_date':today.business_date,'day_count':len(search_days),'days':search_days,'goals':goals,
        'overnights':[o for o in req.overnights if o.from_day_id in active and o.to_day_id in active],
        'access_confirmations':[a.model_copy(update={'allowed_dates':tuple(d for d in a.allowed_dates if d in dates)}) for a in req.access_confirmations if set(a.allowed_dates)&dates]})
    return {'search':search,'daily':daily,'prefix':prefix,'ongoing':ongoing,'fixed':fixed,'current':current,'today':today}


def daily_for(scope,day,goals,objective,seconds):
    req=day_requirements(scope['search'],day,goals,day.objective or objective,seconds)
    if day.day_id==scope['today'].day_id:
        req=scope['daily'].model_copy(update={'goals':req.goals,'objective':req.objective,'solve_seconds':seconds,'alternatives':1})
    return req


def validate(req,ctx,plan,previous,scope):
    errors=[];search=scope['search'];mutable={d.day_id:d for d in search.days};goals={g.goal_id:g for g in search.goals}
    previous_days={d.day_id:d for d in previous.days};picked=[];sessions=[]
    def fail(code,message,*ids):errors.append(Issue(code=code,message=message,affected_ids=ids))
    try:MultiDayItinerary.model_validate(plan.model_dump())
    except ValueError:fail('TRIP_SHAPE','整套路线结构无效')
    for d in plan.days:
        if d.day_id not in mutable:
            if d!=previous_days.get(d.day_id):fail('FROZEN_DAY_CHANGED','冻结日期必须逐字段保留',d.day_id)
        else:
            assigned=[]
            for g in d.requirements.goals:
                source=goals.get(g.goal_id)
                if not source or not applicable(source,mutable[d.day_id]):fail('GOAL_DATE','目标不属于该日期',d.day_id,g.goal_id)
                else:assigned.append(source)
            expected=daily_for(scope,mutable[d.day_id],assigned,plan.objectives[0] if plan.objectives else req.objective,d.requirements.solve_seconds)
            if d.requirements!=expected or d.requirements.solve_seconds>req.solve_seconds:fail('GOAL_REQUIREMENTS_CHANGED','每日需求与服务端保护条件不一致',d.day_id)
            if d.original_requirements!=next(v for v in req.days if v.day_id==d.day_id):fail('DAY_REQUIREMENTS_CHANGED','每日原始条件被篡改',d.day_id)
            is_today=d.day_id==scope['today'].day_id
            if d.status!=(scope['current'].status if is_today else 'pending'):fail('DAY_STATUS_CHANGED','日状态被篡改',d.day_id)
            prefix=scope['prefix'] if is_today else ();ongoing=scope['ongoing'] if is_today else None
            if d.itinerary.completed_prefix!=prefix or d.itinerary.ongoing!=ongoing:fail('HISTORY_REWRITE','实际进度和正在进行内容必须保留',d.day_id)
            if d.itinerary.history_cost_fen or d.itinerary.history_cost_unknown!=(req.pricing is None if is_today else False) or d.itinerary.history_cost_basis!='none':
                fail('HISTORY_COST_CHANGED','历史费用口径被篡改',d.day_id)
            problem=prepare(expected,ctx,fixed_order=scope['fixed'] if is_today else ())
            problem.history_cost_unknown=d.itinerary.history_cost_unknown
            errors.extend(problem.issues);errors.extend(validate_itinerary(problem,d.itinerary))
        picked.extend(a.goal_id for a in d.itinerary.activities)
        picked.extend(s.stop_id for s in d.itinerary.completed_prefix)
        sessions.extend(a.session_id for a in d.itinerary.activities if a.session_id)
        if d.itinerary.ongoing:
            picked.append(d.itinerary.ongoing['stop_id'])
            if d.itinerary.ongoing['original_activity'].get('session_id'):sessions.append(d.itinerary.ongoing['original_activity']['session_id'])
    if any(n>1 for n in Counter(picked).values()):fail('DUPLICATE_GOAL','全程目标只能出现一次')
    if len(sessions)!=len(set(sessions)):fail('DUPLICATE_SESSION','同场演出不能重复安排')
    for g in req.goals:
        if g.required and picked.count(g.goal_id)!=1:fail('MISSING_REQUIRED','必去目标必须全程保留一次',g.goal_id)
    expected=assemble(req,plan.days,plan.objectives[0] if plan.objectives else req.objective,ctx)
    if plan.model_dump(exclude={'days','objectives','metrics'})!=expected.model_dump(exclude={'days','objectives','metrics'}):fail('TRIP_TOTAL','全程范围、衔接、费用或汇总被篡改')
    if expected.budget and expected.budget.hard_limit and expected.budget.status!='within_budget':fail('BUDGET','全程费用未知或超过硬预算')
    return tuple(errors)


def plan(req,ctx,previous,scope):
    search=scope['search'];started=time.monotonic();deadline=started+req.solve_seconds
    expired=lambda:time.monotonic()>=deadline
    problems=[];available={g.goal_id:[] for g in search.goals};plans=[];issues=[];attempts=0;truncated=False;warnings=[]
    for i,day in enumerate(search.days):
        goals=[g for g in search.goals if applicable(g,day)]
        daily=daily_for(scope,day,goals,search.objective,req.solve_seconds)
        p=prepare(daily.model_copy(update={'goals':tuple(g.model_copy(update={'required':False}) for g in daily.goals)}),ctx)
        if p.issues:return SolveResultV2(schema_version='city-engine-result/2',status='needs_data',computed_at=ctx.assessed_at,issues=tuple(p.issues))
        p=replace(p,requirements=daily,activities=[replace(a,required=next(g.required for g in goals if g.goal_id==a.goal_id)) for a in p.activities]);problems.append(p)
        for g in goals:
            if any(a.goal_id==g.goal_id for a in p.activities):available[g.goal_id].append(i)
    missing=[Issue(code='REQUIRED_GOAL_UNAVAILABLE',message='必去目标在剩余允许日期内没有适用资料',affected_ids=(g.goal_id,)) for g in search.goals if g.required and not available[g.goal_id]]
    if missing:return SolveResultV2(schema_version='city-engine-result/2',status='needs_data',computed_at=ctx.assessed_at,issues=tuple(missing))
    stages=objectives(search)
    for stage,objective in enumerate(stages):
        states,pruned=assignments(search,problems,available,objective,expired);truncated|=pruned
        limit=max(1,(MAX_ASSIGNMENTS-attempts)//(len(stages)-stage));best=None
        truncated|=len(states)>limit
        for allocation in states[:limit]:
            if attempts>=MAX_ASSIGNMENTS or expired():truncated=True;break
            attempts+=1;days=[]
            for i,(day,ids) in enumerate(zip(search.days,allocation)):
                seconds=min(req.solve_seconds,(deadline-time.monotonic())/max(1,len(search.days)-i))
                if seconds<MIN_SOLVE_SECONDS:truncated=True;break
                daily=daily_for(scope,day,[g for g in search.goals if g.goal_id in ids],objective,seconds)
                if day.day_id==scope['today'].day_id:
                    result=replan_trip(daily,ctx,completed_prefix=tuple(s for s in scope['prefix'] if s.progress=='completed'),
                        prior_itinerary=scope['current'].itinerary,ongoing=scope['ongoing'],fixed_order=scope['fixed'],
                        resolved_count=len(scope['prefix']),history_cost_unknown=req.pricing is None)
                else:result=solve(prepare(daily,ctx),deadline=deadline)
                warnings.extend(result.warnings)
                if result.status!='feasible':issues.extend(result.issues);break
                is_today=day.day_id==scope['today'].day_id
                itinerary=result.itineraries[0].model_copy(update={'completed_prefix':scope['prefix'] if is_today else ()})
                days.append(DayItinerary(business_date=day.business_date,original_requirements=next(d for d in req.days if d.day_id==day.day_id),
                    requirements=daily,itinerary=itinerary,status=scope['current'].status if is_today else 'pending'))
            if len(days)!=len(search.days):continue
            updated={d.day_id:d for d in days};candidate=assemble(req,[updated.get(d.day_id,d) for d in previous.days],objective,ctx)
            checked=validate(req,ctx,candidate,previous,scope)
            if checked:issues.extend(checked);continue
            if best is None or rank_plan(req,candidate,objective)<rank_plan(req,best,objective):best=candidate
        if best:
            same=next((i for i,p in enumerate(plans) if signature(p)==signature(best)),None)
            if same is None:plans.append(best)
            else:plans[same]=plans[same].model_copy(update={'objectives':(*plans[same].objectives,objective)})
        if expired():truncated=True;break
    if truncated:warnings.append('剩余多日搜索未穷尽，不证明全局最优或无解。')
    if any(p.cost_unknown for p in plans):warnings.append('部分费用未知，请核对全程预算中的未计入项。')
    return SolveResultV2(schema_version='city-engine-result/2',status='feasible' if plans else 'timeout' if truncated else 'needs_data',
        computed_at=ctx.assessed_at,itineraries=tuple(plans[:req.alternatives]),issues=() if plans else tuple(issues[:30]) or (Issue(code='SEARCH_TIMEOUT',message='未找到完整可确认方案，请核对剩余时段和目标条件'),),
        warnings=tuple(dict.fromkeys(warnings)),metrics={'algorithm':'bounded suffix assignment + CP-SAT','assignment_attempts':attempts,'search_truncated':truncated,'global_optimum_claimed':False,'elapsed_ms':round((time.monotonic()-started)*1000,3)})
