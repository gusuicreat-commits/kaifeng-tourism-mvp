"""Recheck a whole candidate against the original trip, never its embedded goal claims."""
from pydantic import ValidationError
from .models import MultiDayItinerary, Issue
from .prepare import prepare
from .validation import validate_itinerary
from ..schema import validate_days, validate_overnights
from .. import costing


def validate_multi_day(req,ctx,plan):
    from .multi_day import applicable,day_requirements,objectives,evidence_mode
    errors=[];selected=[];sessions=[];goals={g.goal_id:g for g in req.goals}
    def fail(code,message,*ids):errors.append(Issue(code=code,message=message,affected_ids=ids))
    try:MultiDayItinerary.model_validate(plan.model_dump())
    except (ValueError,ValidationError):fail('TRIP_SHAPE','全程日期、唯一性、跨夜或汇总结构无效')
    if (plan.start_date,plan.end_date,plan.timezone,plan.day_count)!=(req.start_date,req.end_date,req.timezone,req.day_count):
        fail('DATE_RANGE','全程日期范围被改写')
    try:validate_days(req.start_date,req.end_date,plan.days)
    except ValueError:fail('DAY_RANGE','每日日期必须与原始日范围一致')
    if plan.overnights!=req.overnights:fail('OVERNIGHT_CHANGED','过夜地点或来源边界被改写')
    try:validate_overnights(req.days,plan.overnights,complete=True)
    except ValueError:fail('OVERNIGHT_CONTINUITY','跨夜必须连接前日终点和次日起点')
    originals={d.day_id:d for d in req.days}
    for d in plan.days:
        original=originals.get(d.day_id)
        if not original:fail('UNKNOWN_DAY','日程不属于原需求',d.day_id);continue
        if d.original_requirements!=original:fail('DAY_REQUIREMENTS_CHANGED','原始每日需求被改写',d.day_id)
        p=d.itinerary
        if d.status!='pending' or p.completed_prefix or p.ongoing or p.history_cost_fen or p.history_cost_unknown or p.history_cost_basis!='none':
            fail('HISTORY_REWRITE','初次规划不能注入历史、日进度或正在进行项',d.day_id)
        assigned=[]
        for g in d.requirements.goals:
            original_goal=goals.get(g.goal_id)
            if not original_goal or not applicable(original_goal,original):
                fail('GOAL_DATE','目标不属于该日或不在允许日期',d.day_id,g.goal_id);continue
            assigned.append(original_goal)
        expected=day_requirements(req,original,assigned,d.requirements.objective,d.requirements.solve_seconds)
        if expected!=d.requirements or d.requirements.solve_seconds>req.solve_seconds:
            fail('GOAL_REQUIREMENTS_CHANGED','每日目标、必选、票务或硬约束与原始需求不一致',d.day_id)
        if d.requirements.objective not in objectives(req):fail('OBJECTIVE_CHANGED','求解目标不属于请求',d.day_id)
        if plan.objectives and d.requirements.objective not in plan.objectives:
            fail('OBJECTIVE_MISMATCH','日内偏好不能混合标成另一全程方案',d.day_id)
        if len(p.activities)>24:fail('DAY_STOP_LIMIT','每日至多24站',d.day_id)
        problem=prepare(expected,ctx)
        for issue in problem.issues:
            errors.append(issue.model_copy(update={'affected_ids':(d.day_id,)+issue.affected_ids}))
        for issue in validate_itinerary(problem,p):
            errors.append(issue.model_copy(update={'affected_ids':(d.day_id,)+issue.affected_ids}))
        for a in p.activities:
            selected.append(a.goal_id)
            if a.session_id:sessions.append(a.session_id)
            g=goals.get(a.goal_id)
            if not g or not applicable(g,original):fail('GOAL_DATE','所选目标不属于该日',d.day_id,a.goal_id)
            elif a.required!=g.required or a.adjustable==g.required:
                fail('REQUIRED_CHANGED','目标必选状态被改写',d.day_id,a.goal_id)
    if len(selected)!=len(set(selected)):fail('DUPLICATE_GOAL','同一全程目标不能安排多次')
    if len(sessions)!=len(set(sessions)):fail('DUPLICATE_SESSION','同一场次不能安排多次')
    for g in req.goals:
        if g.required and selected.count(g.goal_id)!=1:fail('MISSING_REQUIRED','全程必选必须恰好完成一次',g.goal_id)
    if plan.active_seconds!=sum(d.itinerary.total_seconds for d in plan.days):fail('ACTIVE_TOTAL','全程活动时长汇总不一致')
    if not plan.days or plan.elapsed_seconds!=int((plan.days[-1].itinerary.returns_at-req.days[0].starts_at).total_seconds()):
        fail('ELAPSED_TOTAL','全程经过时长汇总不一致')
    if plan.travel_seconds!=sum(d.itinerary.travel_seconds for d in plan.days):fail('TRAVEL_TOTAL','全程交通时长汇总不一致')
    if abs(plan.walking_distance_m-sum(d.itinerary.walking_distance_m for d in plan.days))>.001:fail('WALKING_TOTAL','全程步行汇总不一致')
    expected_budget=costing.summarize(req,plan.days,ctx)
    if expected_budget:
        if plan.budget!=expected_budget or plan.known_cost_upper_fen!=expected_budget.known_total_fen or plan.cost_unknown!=bool(expected_budget.unknown_items):
            fail('COST_TOTAL','全程费用、单位或报价快照与服务端核算不一致')
        if expected_budget.hard_limit and expected_budget.status in ('unknown','over'):fail('BUDGET','全程费用未知或超过硬预算')
    else:
        if plan.budget is not None:fail('COST_TOTAL','旧口径行程不能自行提供全程预算结果')
        if plan.known_cost_upper_fen!=sum(d.itinerary.known_cost_upper_fen for d in plan.days):fail('COST_TOTAL','全程已知费用汇总不一致')
        if plan.cost_unknown!=(bool(req.overnights) or any(d.itinerary.cost_unknown for d in plan.days)):
            fail('COST_UNKNOWN','未知费用不能被覆盖或伪称已知')
    if plan.days and plan.evidence_mode!=evidence_mode(plan.days,req.overnights):fail('EVIDENCE_MODE','全程证据必须取最弱者')
    if plan.navigation_certified:fail('NAVIGATION_CLAIM','不得声称已通过真实导航验收')
    return tuple(errors)
