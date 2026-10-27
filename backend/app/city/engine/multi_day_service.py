"""One candidate and one transaction for a whole trip; only today's suffix is mutable."""
import json
from datetime import datetime,timedelta
from uuid import uuid4
from fastapi import HTTPException
from pydantic import ValidationError
from ...db import connect
from ...memory.storage import encoded,stamp
from .. import state
from ..schema import TZ,Stop,PositionAnchor
from ..state_schema import CityDraft,CityDraftV2,DayDraft
from ..versions import read_draft,read_requirements,read_itinerary
from .models import (MultiDayItinerary,MultiDayRequirements,SolveResultV2,DayItinerary,Requirements,
                     ReplanRequest,OngoingInput,Issue)
from .multi_day import assemble,day_requirements,applicable
from .multi_day_validation import validate_multi_day
from .prepare import prepare
from .validation import validate_itinerary,compare_itineraries
from .tools import plan_trip,replan_trip
from . import live_travel


def unavailable(message):
    raise HTTPException(503,detail={'status':'unavailable','code':'MULTI_DAY_UNAVAILABLE','message':message})


def day_view(day):
    return CityDraft.model_validate(day.model_dump(include=set(CityDraft.model_fields)))


def compare(previous,plan):
    old={d.day_id:d for d in previous.days} if previous else {}
    return {'days':[{'day_id':d.day_id,'business_date':d.business_date.isoformat(),
            **compare_itineraries(old[d.day_id].itinerary if d.day_id in old else None,d.itinerary)} for d in plan.days],
            'must_confirm':True}


def current_day(draft,moment,day_id=None):
    day=next((d for d in draft.days if d.business_date==moment.astimezone(TZ).date()),None)
    if not day or day.status=='closed' or not day.starts_at<=moment<day.ends_at:
        raise HTTPException(422,detail='当前不在今天的游玩时窗内；请保留原路线，不能把其他天作为今天重排。')
    if day_id and day.day_id!=day_id:raise HTTPException(422,detail='只能修改上海日期对应的今天剩余，不能修改所选的其他日期。')
    return day


def overnight_evidence(req,ctx):
    boundaries=[];days={d.day_id:d for d in req.days}
    for o in req.overnights:
        if o.source_kind!='user_place' or any(getattr(o,k) is not None for k in ('service_selection_id','service_version','binding_revision','check_in','check_out')):
            unavailable('酒店过夜绑定尚未开放，请手选已有地点作为过夜点；不能把酒店候选当作已绑定地点。')
        place=ctx.facts.get(('place',o.place_id))
        ready=ctx.ready(place,days[o.from_day_id].ends_at,days[o.to_day_id].starts_at)
        mode='synthetic' if ready and ctx.synthetic else 'reference' if ready and place.planning_status=='reference' else 'verified' if ready else 'unknown'
        boundaries.append(o.model_copy(update={'place_evidence':mode,'stay_evidence':'unknown',
            'evidence_refs':('place:'+o.place_id,) if place else ()}))
    return req.model_copy(update={'overnights':tuple(boundaries)})


def lock_scope(original,req,previous,today,draft):
    if req.model_dump(exclude={'goals'})!=original.model_dump(exclude={'goals'}):
        raise HTTPException(409,detail='今天重排不能改写日期范围、原始每日条件、交通偏好或过夜边界。')
    current=next(d for d in previous.days if d.day_id==today.day_id)
    pending={s.stop_id for s in today.stops if s.progress=='pending'}
    mutable={g.goal_id for g in current.requirements.goals}&pending
    if current.itinerary.ongoing and current.itinerary.ongoing['stop_id'] in pending:
        mutable.add(current.itinerary.ongoing['stop_id'])
    old={g.goal_id:g for g in original.goals};new={g.goal_id:g for g in req.goals}
    for gid,g in old.items():
        if gid not in mutable and new.get(gid)!=g:
            raise HTTPException(409,detail='历史和后续天的目标已锁定，不能删除、换日或改写。')
    for gid,g in new.items():
        if gid not in old and (g.allowed_dates!=(today.business_date,) or g.fixed_date not in (None,today.business_date)):
            raise HTTPException(409,detail='新增目标必须明确只属于今天。')
        if gid in mutable or gid not in old:
            if not applicable(g,current.original_requirements):raise HTTPException(422,detail='今天目标不在允许日期或时间窗。')
    for d in draft.days:
        if d.business_date<today.business_date or d.status=='closed':
            history=next(p for p in previous.days if p.day_id==d.day_id)
            reservation=history.itinerary.ongoing
            reported=next((s for s in d.stops if reservation and s.stop_id==reservation['stop_id']),None)
            if reservation and (not reported or reported.progress=='pending'):
                raise HTTPException(422,detail='历史日仍有正在进行项，请先核对历史状态；不能把跨日活动当作今天的新活动。')
            if any(s.locked and s.progress=='pending' for s in d.stops):
                raise HTTPException(422,detail={'code':'HISTORY_PENDING','status':'needs_clarification',
                    'message':'历史日仍有未核对的必选项目；历史补录尚未开放，已保留整套原路线，不能自动完成、删除或移到今天。','day_id':d.day_id})
    return current,tuple(g for g in req.goals if g.goal_id in mutable or g.goal_id not in old)


def remaining(row,body,ctx,previous,original):
    from . import service
    draft=read_draft(row['draft_json'])
    if not isinstance(draft,CityDraftV2) or not isinstance(previous,MultiDayItinerary):
        unavailable('今天剩余重排需要已确认的多天路线，不能将单天历史自动扩为多天。')
    if body.scope!='current_day':unavailable('多天首轮仅支持今天剩余；连后续天调整和指定未来日调整尚未开放，未降为第一天。')
    if body.spent_cost_fen is not None or body.spend_as_of is not None:
        unavailable('多天全程费用报告与预算口径尚未开放；请保留原路线。')
    today=current_day(draft,ctx.assessed_at,body.day_id)
    current,goals=lock_scope(original,body.requirements,previous,today,draft)
    req=day_requirements(body.requirements,current.original_requirements,goals,
                         current.requirements.objective,body.requirements.solve_seconds)
    anchor=PositionAnchor.model_validate_json(row['anchor_json']) if row['anchor_json'] else None
    if not anchor:raise HTTPException(422,detail='请先确认当前已知地点。')
    done={s.stop_id for s in today.stops if s.progress!='pending'}
    ongoing=body.ongoing
    if ongoing is None:
        active=next((s for s in current.itinerary.activities if s.starts_at<=ctx.assessed_at<s.departs_at
              and s.goal_id not in done|set(body.skipped_goal_ids) and s.place_id==anchor.place_id),None)
        reserved=current.itinerary.ongoing
        if active:ongoing=OngoingInput(stop_id=active.goal_id,remaining_seconds=max(1,int((active.departs_at-ctx.assessed_at).total_seconds())),basis='prior_plan_estimate')
        elif reserved and reserved['stop_id'] not in done|set(body.skipped_goal_ids):
            seconds=int((datetime.fromisoformat(reserved['ends_at'])-ctx.assessed_at).total_seconds())
            if seconds<=0:raise HTTPException(422,detail='当前活动预计结束时刻已过，请报告完成或明确剩余时长。')
            ongoing=OngoingInput(stop_id=reserved['stop_id'],remaining_seconds=seconds,basis='prior_plan_estimate')
    if body.end_ongoing and not ongoing:raise HTTPException(422,detail='请指定要结束的当前活动。')
    if ongoing:req=req.model_copy(update={'goals':tuple(g for g in req.goals if g.goal_id!=ongoing.stop_id)})
    try:req=Requirements.model_validate(req.model_dump()|{'starts_at':ctx.assessed_at+timedelta(seconds=120),'origin_place_id':anchor.place_id})
    except ValidationError:raise HTTPException(422,detail='今天剩余时间不足，不能跨夜或改动后续天。') from None
    local=ReplanRequest.model_validate(body.model_dump(exclude={'scope','day_id','requirements'})|{'requirements':req,'ongoing':ongoing})
    view=dict(row);view['draft_json']=day_view(today).model_dump_json()
    req,prefix,ongoing,fixed=service.protect_requirements(view,req,True,current.itinerary,local,ctx)
    resolved=list(prefix)
    skipped=set(body.skipped_goal_ids)
    if local.end_ongoing:skipped.add(local.ongoing.stop_id)
    resolved.extend(s.model_copy(update={'locked':False,'progress':'skipped','progress_source':'user_reported'})
                    for s in today.stops if s.stop_id in skipped and s.progress=='pending')
    return req,tuple(resolved),ongoing,fixed,current,today


def restore_today(db,row,body,ctx,previous,original):
    from . import service
    historical=service.route_row(db,row['id'],body.restore_route_version)
    if not historical:raise HTTPException(404,detail='历史路线版本不存在。')
    old_req=read_requirements(historical['requirements_json']);old=read_itinerary(historical['itinerary_json'])
    if not isinstance(old,MultiDayItinerary) or not isinstance(old_req,MultiDayRequirements):
        unavailable('不能把单天历史路线恢复为多天；完整原路线已保留。')
    draft=read_draft(row['draft_json']);today=current_day(draft,ctx.assessed_at,body.day_id)
    if (old.start_date,old.end_date)!=(original.start_date,original.end_date):
        raise HTTPException(409,detail='历史路线日期范围不同，不能按今天剩余恢复。')
    day=next(d for d in old.days if d.day_id==today.day_id)
    pending={s.stop_id for s in today.stops if s.progress=='pending'}
    done={s.stop_id for d in draft.days for s in d.stops if s.progress!='pending'}
    ids={a.goal_id for a in day.itinerary.activities}
    if day.itinerary.ongoing:ids.add(day.itinerary.ongoing['stop_id'])
    locked_elsewhere={a.goal_id for d in previous.days if d.day_id!=today.day_id for a in d.itinerary.activities}
    if ids&locked_elsewhere:raise HTTPException(409,detail='历史目标现已属于锁定的其他日期，不能通过今天恢复跨日移动。')
    goals=[g for g in original.goals if g.goal_id not in pending]
    goals.extend(g.model_copy(update={'allowed_dates':(today.business_date,),'fixed_date':today.business_date})
                 for g in old_req.goals if g.goal_id in ids-done)
    return MultiDayRequirements.model_validate(original.model_dump()|{'goals':goals})


def runtime_errors(req,ctx,plan,previous,day_id,prefix,fixed=(),ongoing=None,expected=None):
    errors=[]
    def fail(code,message,*ids):errors.append(Issue(code=code,message=message,affected_ids=ids))
    try:MultiDayItinerary.model_validate(plan.model_dump())
    except ValueError:fail('TRIP_SHAPE','整套路线结构、跨夜或汇总无效')
    if (plan.start_date,plan.end_date,plan.overnights)!=(req.start_date,req.end_date,req.overnights):
        fail('OVERNIGHT_CHANGED','不能改变全程范围或过夜边界')
    old={d.day_id:d for d in previous.days};selected=[]
    for d in plan.days:
        if d.day_id!=day_id:
            previous_day=old.get(d.day_id)
            allowed_closed=(previous_day and d.business_date<ctx.assessed_at.astimezone(TZ).date()
                and d.status=='closed' and previous_day.status!='closed'
                and d.model_copy(update={'status':previous_day.status})==previous_day)
            if d!=previous_day and not allowed_closed:fail('FROZEN_DAY_CHANGED','历史与后续日必须逐字段保持原快照',d.day_id)
            if d.business_date>=ctx.assessed_at.astimezone(TZ).date() and d.status!='closed':
                problem=prepare(d.requirements,ctx)
                errors.extend(problem.issues);errors.extend(validate_itinerary(problem,d.itinerary))
        else:
            if d.original_requirements!=old[day_id].original_requirements:fail('DAY_REQUIREMENTS_CHANGED','不能改写原始每日需求',day_id)
            if expected is not None and d.requirements!=expected:fail('GOAL_REQUIREMENTS_CHANGED','今天剩余需求与服务端预览基线不一致',day_id)
            if d.itinerary.completed_prefix!=tuple(prefix) or d.itinerary.ongoing!=ongoing:
                fail('HISTORY_REWRITE','已处理前缀与正在进行项必须保留服务端基线',day_id)
            problem=prepare(d.requirements,ctx,fixed_order=tuple(fixed))
            problem.history_cost_fen=d.itinerary.history_cost_fen
            problem.history_cost_unknown=d.itinerary.history_cost_unknown
            problem.history_cost_basis=d.itinerary.history_cost_basis
            errors.extend(problem.issues);errors.extend(validate_itinerary(problem,d.itinerary))
        selected.extend(a.goal_id for a in d.itinerary.activities)
        selected.extend(s.stop_id for s in d.itinerary.completed_prefix)
        if d.itinerary.ongoing:selected.append(d.itinerary.ongoing['stop_id'])
    for g in req.goals:
        if g.required and selected.count(g.goal_id)!=1:fail('MISSING_REQUIRED','全程必选必须保留一次',g.goal_id)
    return tuple(errors)


def preview_multi_day(session,trip_id,body,replanning=False,planning_version=None):
    from . import service
    from .. import basket,exploration_state
    data=body.model_dump(mode='json');scope=('engine_replan:' if replanning else 'engine_preview:')+str(trip_id)
    if replanning and body.scope!='current_day':unavailable('多天首轮仅支持今天剩余；连后续天和指定未来日调整尚未开放，未降为第一天。')
    with connect() as db:
        row=state.owned(db,session,trip_id)
        cached=state.receipt(db,session,body.request_id,scope,data)
        if cached is not None:return cached
        state.check_version(row,body.expected_version);service.check_open(row)
        ctx=service.context(db);req=body.requirements;draft=read_draft(row['draft_json'])
        req=overnight_evidence(req,ctx);body=body.model_copy(update={'requirements':req})
        oldrow=service.route_row(db,trip_id);previous=read_itinerary(oldrow['itinerary_json']) if oldrow else None
        original=read_requirements(oldrow['requirements_json']) if oldrow else None
        if previous and not isinstance(previous,MultiDayItinerary):
            if row['status']!='DRAFT' or any(s.progress!='pending' for s in draft.stops):
                unavailable('已执行的单天路线暂不支持扩为多天；原历史与路线已保留。')
            previous=None
        daily=None;prefix=();ongoing=None;fixed=();today=None
        if replanning:
            if body.restore_route_version:
                if not isinstance(previous,MultiDayItinerary):unavailable('今天恢复需要已确认的多天路线。')
                req=restore_today(db,row,body,ctx,previous,original)
                body=body.model_copy(update={'requirements':req,'restore_route_version':None})
            daily,prefix,ongoing,fixed,current,today=remaining(row,body,ctx,previous,original)
        else:
            days=draft.days if isinstance(draft,CityDraftV2) else (draft,)
            if row['status']!='DRAFT' or any(s.progress!='pending' for d in days for s in d.stops):
                raise HTTPException(422,detail='已有执行状态，请使用今天剩余重排。')
            if req.days[0].starts_at<ctx.assessed_at:raise HTTPException(422,detail='首次多天规划不能从过去出发。')
        base=row['version'];baseline=service.progress(row);fingerprint=ctx.fingerprint()
        basket_baseline=basket.version(db,trip_id);planning_baseline=exploration_state.read(db,trip_id)['version']
        if planning_version is not None:exploration_state.guard(db,trip_id,planning_version)
    if replanning:
        ctx=live_travel.restore_snapshot(ctx,previous)
        ctx=live_travel._enrich_day(ctx,daily,dated=True)
        solved=replan_trip(daily,ctx,completed_prefix=tuple(s for s in prefix if s.progress=='completed'),
                prior_itinerary=current.itinerary,ongoing=ongoing,fixed_order=fixed,resolved_count=len(prefix),history_cost_unknown=True)
        plans=[]
        for p in solved.itineraries:
            p=p.model_copy(update={'completed_prefix':prefix})
            day=current.model_copy(update={'requirements':daily,'itinerary':p})
            frozen_days=[]
            for d in previous.days:
                if d.day_id==today.day_id:frozen_days.append(day)
                elif d.itinerary.ongoing and d.business_date<ctx.assessed_at.astimezone(TZ).date():
                    # Retire the reservation as history without rewriting its plan or reported evidence.
                    frozen_days.append(d.model_copy(update={'status':'closed'}))
                else:frozen_days.append(d)
            candidate=assemble(req,frozen_days,daily.objective)
            if not runtime_errors(req,ctx,candidate,previous,today.day_id,prefix,fixed,ongoing,daily):plans.append(candidate)
        result=SolveResultV2.model_validate(solved.model_dump(exclude={'itineraries'})|{'schema_version':'city-engine-result/2','itineraries':plans})
        if solved.status=='feasible' and not plans:result=result.model_copy(update={'status':'invalid','issues':(Issue(code='VALIDATION_FAILED',message='整套重排复核未通过，原路线已保留'),)})
    else:
        ctx=live_travel.enrich(ctx,req)
        result=plan_trip(req,ctx)
    candidate_id=str(uuid4()) if result.status=='feasible' and result.itineraries else None
    expiry=stamp(service.now()+timedelta(minutes=15))
    if candidate_id:
        quotes=[l.journey.quote_expires_at for p in result.itineraries for d in p.days for l in d.itinerary.legs
                if l.journey.time_basis=='provider_estimate' and l.journey.quote_expires_at and (not today or d.day_id==today.day_id)]
        if quotes:expiry=min(expiry,stamp(min(quotes)))
    changes=[compare(previous,p) for p in result.itineraries]
    response={'status':'preview' if candidate_id else result.status,'candidate_id':candidate_id,'trip_id':str(trip_id),
        'base_version':base,'expires_at':expiry if candidate_id else None,'result':result.model_dump(mode='json'),
        'changes':changes[0] if changes else None,'alternative_changes':changes,'must_confirm':bool(candidate_id)}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached=state.receipt(db,session,body.request_id,scope,data)
        if cached is not None:return cached
        row=state.owned(db,session,trip_id);state.check_version(row,base);service.check_open(row)
        basket.guard(db,trip_id,basket_baseline);exploration_state.guard(db,trip_id,planning_baseline)
        if service.context(db).fingerprint()!=fingerprint:raise HTTPException(409,detail='计算期间事实已变化，请重新预览。')
        if service.progress(row)!=baseline:raise HTTPException(409,detail='计算期间进度已变化，请重新预览。')
        if candidate_id:
            extra={'baseline':baseline,'replanning':replanning,'day_id':today.day_id if today else None,
                'prefix':[s.model_dump(mode='json') for s in prefix],'ongoing':ongoing,'fixed_order':fixed,
                'daily_requirements':daily.model_dump(mode='json') if daily else None,
                'basket_version':basket_baseline,'planning_version':planning_baseline,
                'previous_route_version':oldrow['route_version'] if oldrow else None}
            db.execute('INSERT INTO city_engine_candidates VALUES (?,?,?,?,?,?,?,?,0)',
                (candidate_id,str(trip_id),base,fingerprint,req.model_dump_json(),result.model_dump_json(),encoded(extra),expiry))
        state.remember(db,session,body.request_id,trip_id,scope,data,response)
    return response


def new_draft(req,plan,previous,replanning,day_id):
    old={d.day_id:d for d in previous.days} if isinstance(previous,CityDraftV2) else {}
    days=[]
    for d in plan.days:
        if replanning and d.day_id!=day_id:days.append(old[d.day_id]);continue
        p=d.itinerary
        pending=[Stop(stop_id=a.goal_id,place_id=a.place_id,kind=a.kind,session_id=a.session_id,
                locked=a.required,duration_seconds=a.duration_seconds) for a in p.activities]
        if p.ongoing:
            stop=next((s for s in old[d.day_id].stops if s.stop_id==p.ongoing['stop_id']),None)
            if stop:pending.insert(0,stop)
        data=old[d.day_id].model_dump() if d.day_id in old else {}
        data.update(d.original_requirements.model_dump(exclude_none=True))
        data.update(modes=d.requirements.modes,pace=d.requirements.pace,objective=d.requirements.objective,
                    stops=p.completed_prefix+tuple(pending),status=d.status)
        days.append(DayDraft.model_validate(data))
    return CityDraftV2(schema_version='city-draft/2',start_date=req.start_date,end_date=req.end_date,days=tuple(days),overnights=req.overnights)


def confirm_multi_day(db,session,trip_id,body,row,c,req,result,data,scope):
    from . import service
    from .. import basket,exploration_state
    if not isinstance(result,SolveResultV2):raise HTTPException(422,detail='候选需求和结果的 schema 不一致。')
    if c['base_version']!=row['version']:raise HTTPException(409,detail='候选基线版本已变化。')
    ctx=service.context(db);extra=json.loads(c['progress_json'])
    if ctx.fingerprint()!=c['fingerprint']:raise HTTPException(409,detail='关键事实已变化，请重新计算。')
    if not {'baseline','basket_version','planning_version','replanning'}<=extra.keys():
        unavailable('此多天历史候选缺少可确认基线，请重新生成整套预览。')
    basket.guard(db,trip_id,extra['basket_version']);exploration_state.guard(db,trip_id,extra['planning_version'])
    if service.progress(row)!=extra['baseline']:raise HTTPException(409,detail='行程进度已变化。')
    if body.alternative_index>=len(result.itineraries):raise HTTPException(422,detail='备选方案编号不存在。')
    plan=result.itineraries[body.alternative_index];day_id=extra.get('day_id')
    draft=read_draft(row['draft_json']);previous_row=service.route_row(db,trip_id)
    if (previous_row['route_version'] if previous_row else None)!=extra['previous_route_version']:
        raise HTTPException(409,detail='正式路线版本已变化。')
    if extra['replanning']:
        current_day(draft,ctx.assessed_at,day_id)
        anchor=PositionAnchor.model_validate_json(row['anchor_json']) if row['anchor_json'] else None
        if not anchor or not 0<=(ctx.assessed_at-anchor.confirmed_at).total_seconds()<=900:
            raise HTTPException(409,detail='当前位置已过期，请重新确认并规划。')
    active=[d for d in plan.days if not day_id or d.day_id==day_id]
    for d in active:
        if not d.itinerary.legs or d.itinerary.legs[0].departs_at<service.now():raise HTTPException(409,detail='候选出发时刻已过去，请重新预览。')
        if any(l.journey.time_basis=='provider_estimate' and (not l.journey.quote_expires_at or l.journey.quote_expires_at<=ctx.assessed_at) for l in d.itinerary.legs):
            raise HTTPException(409,detail='交通估时已过期，请重新计算。')
    ctx=live_travel.restore_snapshot(ctx,plan)
    if extra['replanning']:
        previous=read_itinerary(previous_row['itinerary_json'])
        errors=runtime_errors(req,ctx,plan,previous,day_id,tuple(Stop.model_validate(s) for s in extra['prefix']),extra['fixed_order'],extra['ongoing'],
                              Requirements.model_validate(extra['daily_requirements']))
    else:errors=validate_multi_day(req,ctx,plan)
    if errors:raise HTTPException(409,detail={'code':'VALIDATION_FAILED','issues':[e.model_dump(mode='json') for e in errors]})
    updated_draft=new_draft(req,plan,draft,extra['replanning'],day_id)
    version=row['route_version']+1
    db.execute('INSERT INTO city_engine_routes VALUES (?,?,?,?,?,?,?)',
        (str(trip_id),version,row['version']+1,c['requirements_json'],plan.model_dump_json(),c['fingerprint'],stamp()))
    updated=db.execute('UPDATE city_trip_states SET draft_json=?,version=version+1,route_version=?,updated_at=? WHERE id=? AND version=?',
        (updated_draft.model_dump_json(),version,stamp(),str(trip_id),row['version']))
    if updated.rowcount!=1:raise HTTPException(409,detail='并发版本冲突。')
    state.event(db,trip_id,body.request_id,'engine_route_confirmed',row['version'],row['version']+1)
    db.execute('UPDATE city_engine_candidates SET consumed=1 WHERE id=?',(str(body.candidate_id),))
    response={'status':'committed','trip_id':str(trip_id),'version':row['version']+1,'route_version':version,'itinerary':plan.model_dump(mode='json')}
    state.remember(db,session,body.request_id,trip_id,scope,data,response)
    return response


def read_multi_snapshot(db,row,saved,req,plan):
    from . import service
    ctx=service.context(db);fingerprint=ctx.fingerprint()
    ctx=live_travel.restore_snapshot(ctx,plan);today=ctx.assessed_at.astimezone(TZ).date()
    draft=read_draft(row['draft_json']);states={d.day_id:d for d in draft.days} if isinstance(draft,CityDraftV2) else {}
    errors=[];days=[];refresh=False
    for d in plan.days:
        status=states[d.day_id].status if d.day_id in states else d.status
        frozen=d.business_date<today or status=='closed'
        if not frozen and d.business_date==today and row['status'] in ('ACTIVE','PAUSED'):status='active'
        days.append({'day_id':d.day_id,'business_date':d.business_date.isoformat(),'status':status,
                     'frozen':frozen,'locked':d.business_date>today})
        if frozen:continue
        problem=prepare(d.requirements,ctx)
        problem.history_cost_fen=d.itinerary.history_cost_fen;problem.history_cost_unknown=d.itinerary.history_cost_unknown
        problem.history_cost_basis=d.itinerary.history_cost_basis
        errors.extend(i.model_copy(update={'affected_ids':(d.day_id,)+i.affected_ids}) for i in problem.issues)
        errors.extend(i.model_copy(update={'affected_ids':(d.day_id,)+i.affected_ids}) for i in validate_itinerary(problem,d.itinerary))
        refresh|=any(l.journey.quote_expires_at and l.journey.quote_expires_at<=ctx.assessed_at for l in d.itinerary.legs)
    stale=saved['state_version']!=row['version'] or saved['fingerprint']!=fingerprint or bool(errors)
    return {'trip_id':row['id'],'version':row['version'],'route_version':saved['route_version'],
        'itinerary':plan.model_dump(mode='json'),'stale':stale,'issues':[i.model_dump(mode='json') for i in errors],
        'days':days,'traffic_refresh_recommended':bool(refresh),
        'notice':'冻结日保留历史快照；今天及后续日依据当前资料复核，实际交通仍有不确定性。' if not stale else '进度或有效资料已变化；完整历史保留，请核对今天及后续日。'}
