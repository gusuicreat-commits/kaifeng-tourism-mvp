"""Resolve stable goals to finite activity choices; missing facts never become zero durations."""
from datetime import timedelta
from .. import facts
from .context import Activity, Problem
from .models import Issue
from .travel import TravelTimeService

DEFAULT_DURATIONS={'visit':1800,'dining':2700,'rest':900}


def issue(code,message,*ids):
    return Issue(code=code,message=message,affected_ids=ids,
                 suggestions=('补齐或重新核验相关数据；也可调整目标、时段或交通方式。',))


def prepare(req,ctx,prior_order=(),fixed_order=()):
    activities=[];issues=[];blocked=[];defaults={}
    seconds=lambda t:int((t-req.starts_at).total_seconds())
    horizon=int((req.ends_at-req.starts_at).total_seconds())
    for pid in (req.origin_place_id,req.destination_place_id):
        if not ctx.ready(ctx.facts.get(('place',pid)),req.starts_at,req.ends_at):
            issues.append(issue('ENDPOINT_NOT_READY','起点或返程地点缺少适用的核验资料',pid))
    for goal in req.goals:
        variants=[]
        if goal.target_kind=='place':variants=[(goal.target_id,None)]
        elif goal.target_kind=='session':
            s=ctx.facts.get(('show_session',goal.target_id))
            if s:variants=[(s.stage_place_id,s)]
        elif goal.target_kind=='program':
            variants=[(s.stage_place_id,s) for s in ctx.facts.values() if isinstance(s,facts.ShowSession)
                      and s.program_id==goal.target_id and s.business_date==req.business_date]
        else:
            for supply in ctx.facts.values():
                if not isinstance(supply,facts.Supply) or supply.food_id!=goal.target_id or not ctx.ready(supply,req.starts_at,req.ends_at):continue
                vendor=ctx.facts.get(('vendor',supply.vendor_id))
                if vendor:variants.append((vendor.place_id,None))
        seen=set();valid=0
        for pid,session in variants:
            if goal.fixed_place_id and pid!=goal.fixed_place_id:continue
            if goal.fixed_session_id and (not session or session.fact_id!=goal.fixed_session_id):continue
            key=(pid,session.fact_id if session else None)
            if key in seen:continue
            seen.add(key);place=ctx.facts.get(('place',pid))
            bad=[]
            if not ctx.ready(place,req.starts_at,req.ends_at):bad.append('PLACE_NOT_READY')
            if goal.kind=='dining' and (not place or place.place_type!='restaurant'):bad.append('DINING_NOT_A_VENDOR_PLACE')
            earliest=0;latest=horizon
            if goal.window_start:earliest=max(earliest,seconds(goal.window_start))
            if goal.window_end:latest=min(latest,seconds(goal.window_end))
            refs=[f'place:{pid}'];parks=ctx.parks(pid)
            # Parent park rules restrict the whole visit. Venue-specific rules narrow them further.
            for rule_pid in (pid,)+parks:
                rules=ctx.rules(rule_pid,req.business_date)
                if not rules:
                    if rule_pid!=pid or not parks:bad.append('OPENING_UNKNOWN')
                    continue
                usable=[r for r in rules if ctx.ready(r,req.starts_at,req.ends_at)
                        and r.opens_at and r.closes_at]
                # Multiple disjoint windows must be resolved explicitly, never merged into an invented opening.
                if len(usable)!=1:bad.append('OPENING_AMBIGUOUS_OR_STALE');continue
                rule=usable[0];refs.append(f'open_rule:{rule.fact_id}')
                earliest=max(earliest,seconds(rule.opens_at));latest=min(latest,seconds(rule.closes_at))
                if rule.reservation=='unknown' or (rule.reservation=='required' and rule_pid not in req.access_confirmations):
                    bad.append('RESERVATION_NOT_CONFIRMED')
                if rule.ticket_scope and rule_pid not in req.access_confirmations:bad.append('TICKET_NOT_CONFIRMED')
            deadline=None
            for rule_pid in set((pid,)+parks):
                for rule in ctx.rules(rule_pid,req.business_date):
                    if rule.last_entry_at:
                        value=seconds(rule.last_entry_at)
                        deadline=value if deadline is None else min(deadline,value)
            if session:
                if not ctx.ready(session,session.starts_at,session.ends_at or session.starts_at):bad.append('SESSION_NOT_READY')
                if session.business_date!=req.business_date or session.ends_at is None:bad.append('SESSION_TIME_UNKNOWN')
                duration=int((session.ends_at-session.starts_at).total_seconds()) if session.ends_at else 1
                showstart=seconds(session.starts_at)
                show_deadline=min(showstart-req.show_buffer_seconds,seconds(session.admission_deadline)) if session.admission_deadline else showstart-req.show_buffer_seconds
                deadline=show_deadline if deadline is None else min(deadline,show_deadline)
                if showstart<earliest or showstart+duration>latest:bad.append('SESSION_OUTSIDE_OPENING_OR_GOAL_WINDOW')
                earliest=latest=showstart
                refs.extend((f'show_session:{session.fact_id}',f'program:{session.program_id}'))
            else:
                duration=goal.duration_seconds or DEFAULT_DURATIONS[goal.kind]
                if goal.duration_seconds is None:defaults[goal.goal_id]={'duration_seconds':duration,'basis':'planning_parameter'}
                latest-=duration
            costs=[c for c in ctx.operations.costs if c.place_id==pid and ctx.evidence_ready(c.evidence,req.starts_at,req.ends_at)]
            cost=costs[0].upper_fen if len(costs)==1 else None
            if req.budget_fen is not None and cost is None:bad.append('COST_UNKNOWN')
            if bad:
                blocked.append({'goal_id':goal.goal_id,'place_id':pid,'reasons':sorted(set(bad))});continue
            if earliest>latest or latest<0 or earliest+duration>horizon:
                blocked.append({'goal_id':goal.goal_id,'place_id':pid,'reasons':['TIME_WINDOW_EMPTY']});continue
            valid+=1
            activities.append(Activity(f'a{len(activities)}',goal.goal_id,pid,goal.kind,session.fact_id if session else None,
                                       duration,max(0,earliest),min(horizon-duration,latest),deadline,goal.required,
                                       1+goal.importance,cost,tuple(refs),parks))
        if not valid:
            blocked.append({'goal_id':goal.goal_id,'reasons':['NO_USABLE_VARIANT']})
            if goal.required:issues.append(issue('REQUIRED_GOAL_UNAVAILABLE','必选目标没有完整、有效且满足时间范围的数据',goal.goal_id))
    if len(activities)>60:
        issues.append(issue('CANDIDATE_LIMIT','展开后的候选活动超过60项，请缩小目标或场次范围'))
        activities=[]
    service=TravelTimeService(ctx)
    matrix=service.matrix(req,activities)
    return Problem(req,ctx,activities,matrix,issues,blocked,defaults,tuple(prior_order),tuple(fixed_order))
