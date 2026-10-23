"""Independent deterministic verification, also used again on confirmation and historical restore."""
from datetime import timedelta
from .models import Issue
from .travel import TravelTimeService, travel_seconds, walking_metres, entry_deadline, entry_opening, journey_cost_upper


def validate_itinerary(problem,itinerary):
    req=problem.requirements;ctx=problem.context;errors=[];acts={a.id:a for a in problem.activities}
    def fail(code,message,*ids):errors.append(Issue(code=code,message=message,affected_ids=ids))
    if (itinerary.history_cost_fen,itinerary.history_cost_unknown)!=(problem.history_cost_fen,problem.history_cost_unknown):
        fail('HISTORY_COST','历史费用基线与服务端不一致')
    stops=itinerary.activities;legs=itinerary.legs
    if len(legs)!=len(stops)+1:
        return (Issue(code='LEG_COUNT',message='每个活动和返程必须有对应交通记录'),)
    if not stops and req.goals:fail('NO_ACTIVITY','至少需安排一个目标')
    if len({s.activity_id for s in stops})!=len(stops):fail('DUPLICATE_ACTIVITY','活动实例重复')
    sessions=[s.session_id for s in stops if s.session_id]
    if len(set(sessions))!=len(sessions):fail('DUPLICATE_SESSION','同一场次不能重复参加')
    selected=[s.goal_id for s in stops]
    if len(selected)!=len(set(selected)):fail('DUPLICATE_GOAL','每个目标只能选一个候选')
    for goal in req.goals:
        if goal.required and goal.goal_id not in selected:fail('MISSING_REQUIRED','必选目标未满足',goal.goal_id)
    for s in stops:
        a=acts.get(s.activity_id)
        if not a or (s.goal_id,s.place_id,s.kind,s.session_id,s.required)!=(a.goal_id,a.place_id,a.kind,a.session_id,a.required):
            fail('ACTIVITY_MISMATCH','活动与服务端事实候选不一致',s.activity_id);continue
        start=(s.starts_at-req.starts_at).total_seconds()
        if start<a.earliest or start>a.latest:fail('WINDOW_CONFLICT','活动开始不在有效时间窗',s.goal_id)
        if s.duration_seconds!=a.duration or s.departs_at-s.starts_at!=timedelta(seconds=a.duration):fail('DURATION','活动时长被改写',s.goal_id)
        if s.arrives_at>s.starts_at or a.arrival_deadline is not None and (s.arrives_at-req.starts_at).total_seconds()>a.arrival_deadline:
            fail('ARRIVAL_CONFLICT','到达晚于活动或最晚入场时间',s.goal_id)
        if s.wait_seconds!=(s.starts_at-s.arrives_at).total_seconds():fail('WAIT_MISMATCH','等待时间不一致',s.goal_id)
        for pid in set((s.place_id,)+ctx.parks(s.place_id)):
            for r in ctx.rules(pid,req.business_date):
                if not ctx.ready(r,s.starts_at,s.departs_at):fail('STALE_OPEN_RULE','营业依据已失效',pid)
                if r.opens_at and (s.starts_at<r.opens_at or s.departs_at>r.closes_at):fail('CLOSED','完整停留必须在营业区间',pid)
                if r.last_entry_at and s.arrives_at>r.last_entry_at:fail('LAST_ENTRY','晚于停止入场时刻',pid)
                if r.reservation=='unknown' or (r.reservation=='required' or r.ticket_scope) and pid not in req.access_confirmations:
                    fail('ACCESS_NOT_CONFIRMED','必要预约或票种条件未确认',pid)
    service=TravelTimeService(ctx);entries={};cost=problem.history_cost_fen;cost_unknown=problem.history_cost_unknown
    before=req.starts_at;origin=req.origin_place_id
    for i,leg in enumerate(legs):
        dest=stops[i].place_id if i<len(stops) else req.destination_place_id
        j=leg.journey;t=j.travel
        if (t.origin_place_id,t.destination_place_id)!=(origin,dest) or t.mode not in req.modes:
            fail('TRAVEL_ENDPOINT','交通端点或方式与活动不一致')
        # Requires exact server snapshot membership as well as source/freshness checks.
        if j not in ctx.operations.journeys or not service.usable(j,req):fail('TRAVEL_UNKNOWN','交通或出入口依据不足')
        if leg.departs_at!=before or leg.arrives_at!=before+timedelta(seconds=travel_seconds(j)):
            fail('TRAVEL_TIME','交通时间不连续或未计边界耗时')
        opening=entry_opening(j,req,ctx)
        if opening and leg.arrives_at<opening:fail('PARK_NOT_OPEN','到达早于景区开放时刻')
        cutoff=entry_deadline(j,req,ctx)
        if cutoff and leg.arrives_at>cutoff:fail('PARK_LAST_ENTRY','晚于景区停止入园')
        for b in j.boundaries:
            if b.direction=='entry':entries[b.park_id]=entries.get(b.park_id,0)+1
        cost+=journey_cost_upper(j) or 0;cost_unknown|=journey_cost_upper(j) is None
        if i<len(stops):
            s=stops[i]
            if s.arrives_at!=leg.arrives_at:fail('ARRIVAL_MISMATCH','活动与交通到达时间不一致')
            a=acts.get(s.activity_id);cost+=a.cost or 0 if a else 0;cost_unknown|=not a or a.cost is None
            before=s.departs_at;origin=dest
    for park,count in entries.items():
        if count+(1 if park in ctx.parks(req.origin_place_id) else 0)>1:
            if not any(r.reentry=='allowed' and ctx.ready(r,req.starts_at,req.ends_at) for r in ctx.rules(park,req.business_date)):
                fail('REENTRY_NOT_ALLOWED','再入园条件未知或禁止',park)
    if legs[-1].arrives_at>req.ends_at or itinerary.returns_at!=legs[-1].arrives_at:fail('RETURN_DEADLINE','返程未按时到达指定终点')
    if itinerary.total_seconds!=(itinerary.returns_at-req.starts_at).total_seconds():fail('TOTAL_TIME','总时间不一致')
    if itinerary.travel_seconds!=sum(travel_seconds(l.journey) for l in legs):fail('TRAVEL_TOTAL','交通时间汇总不一致')
    if abs(itinerary.walking_distance_m-sum(walking_metres(l.journey) for l in legs))>.001:fail('WALKING_TOTAL','步行距离不一致')
    if itinerary.cost_unknown!=bool(cost_unknown) or itinerary.known_cost_upper_fen!=cost:fail('COST_TOTAL','费用汇总不一致')
    if req.budget_fen is not None and (cost_unknown or cost>req.budget_fen):fail('BUDGET','费用未知或超过硬预算')
    if req.max_walking_seconds is not None and sum(travel_seconds(l.journey) for l in legs if l.journey.travel.mode=='walking')>req.max_walking_seconds:
        fail('WALKING_LIMIT','剩余路线总步行估时超过明确硬上限')
    fixed=[g for g in selected if g in problem.fixed_order]
    if tuple(fixed)!=tuple(g for g in problem.fixed_order if g in selected):fail('FIXED_ORDER','锁定的未来活动顺序被改写')
    return tuple(errors)


def compare_itineraries(original,candidate):
    old=[s.goal_id for s in original.activities] if original else []
    new=[s.goal_id for s in candidate.activities]
    retained=[x for x in new if x in old];old_retained=[x for x in old if x in new]
    moved=[x for x in retained if retained.index(x)!=old_retained.index(x)]
    times={s.goal_id:s.starts_at for s in original.activities} if original else {}
    old_stops={s.goal_id:s for s in original.activities} if original else {}
    return {'retained':retained,'removed':[x for x in old if x not in new],
            'added':[x for x in new if x not in old],'reordered':moved,
            'target_changes':[{'goal_id':s.goal_id,'before_place':old_stops[s.goal_id].place_id,'after_place':s.place_id,
                               'before_session':old_stops[s.goal_id].session_id,'after_session':s.session_id}
                              for s in candidate.activities if s.goal_id in old_stops and
                              (old_stops[s.goal_id].place_id,old_stops[s.goal_id].session_id)!=(s.place_id,s.session_id)],
            'start_changes':[{'goal_id':s.goal_id,'before':times[s.goal_id].isoformat(),'after':s.starts_at.isoformat()}
                             for s in candidate.activities if s.goal_id in times and times[s.goal_id]!=s.starts_at],
            'travel_seconds_delta':candidate.travel_seconds-(original.travel_seconds if original else 0),
            'walking_distance_m_delta':candidate.walking_distance_m-(original.walking_distance_m if original else 0),
            'must_confirm':True}
