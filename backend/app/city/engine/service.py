"""Authorized tools. Solve outside write locks, then recheck all inputs at commit."""
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4
from fastapi import HTTPException
from pydantic import ValidationError
from ...db import connect
from ...memory.storage import now, stamp, encoded
from .. import facts, state
from ..schema import Stop, PositionAnchor
from ..state_schema import CityDraft
from .context import PlanningContext
from .models import Requirements, Itinerary, SolveResult, OngoingInput
from .storage import load_operations
from .tools import plan_trip, replan_trip
from .prepare import prepare
from .validation import validate_itinerary, compare_itineraries
from . import live_travel


def context(db):
    enabled=os.getenv('KAIFENG_DEMO_MODE','').strip().lower() in {'1','true','yes','on'}
    return PlanningContext(facts.records(db),load_operations(db),now(),synthetic=False,
                           demo_reference=enabled)


def route_row(db,trip_id,version=None):
    if version is None:
        return db.execute('SELECT * FROM city_engine_routes WHERE trip_id=? ORDER BY route_version DESC LIMIT 1',(str(trip_id),)).fetchone()
    return db.execute('SELECT * FROM city_engine_routes WHERE trip_id=? AND route_version=?',(str(trip_id),version)).fetchone()


def progress(row):
    draft=CityDraft.model_validate_json(row['draft_json'])
    return {'prefix':[s.model_dump(mode='json') for s in draft.stops if s.progress!='pending'],
            'anchor':json.loads(row['anchor_json']) if row['anchor_json'] else None}


def check_open(row):
    if row['status'] in state.TERMINAL:raise HTTPException(422,detail='已结束行程不能提交新路线。')


def protect_requirements(row,req,replanning,previous,body,ctx):
    draft=CityDraft.model_validate_json(row['draft_json'])
    prefix=tuple(s for s in draft.stops if s.progress!='pending')
    if prefix and not replanning:raise HTTPException(422,detail='已有进度，请调用剩余行程重规划。')
    goals={g.goal_id:g for g in req.goals}
    if any(s.stop_id in goals for s in prefix):raise HTTPException(422,detail='不能重新安排已完成或跳过的历史活动。')
    ongoing_id=body.ongoing.stop_id if replanning and body.ongoing else None
    if ongoing_id:
        next_pending=next((s for s in draft.stops if s.progress=='pending'),None)
        if not next_pending or next_pending.stop_id!=ongoing_id:raise HTTPException(422,detail='只能结束或保留当前下一活动。')
        anchor=json.loads(row['anchor_json']) if row['anchor_json'] else None
        if not anchor or anchor['place_id']!=next_pending.place_id:raise HTTPException(422,detail='当前活动与手动位置不符。')
    skipped=set(getattr(body,'skipped_goal_ids',()))
    pending={s.stop_id for s in draft.stops if s.progress=='pending' and s.kind!='return'}
    if skipped-pending or skipped & set(goals) or ongoing_id in skipped:raise HTTPException(422,detail='跳过目标必须来自今天剩余行程且不能再次安排。')
    locked=[s for s in draft.stops if s.locked and s.progress=='pending' and s.stop_id!=ongoing_id and s.stop_id not in skipped]
    for stop in locked:
        g=goals.get(stop.stop_id)
        valid=g and g.required and ((g.target_kind=='session' and g.target_id==stop.session_id)
              if stop.session_id else (g.target_kind=='place' and g.target_id==stop.place_id))
        # A food/program goal may retain its exact previous realization without changing the original goal identity.
        old_goal=next((s for s in previous.activities if s.goal_id==stop.stop_id),None) if previous else None
        if not valid and g and g.required and old_goal:
            valid=(g.target_kind=='program' and stop.session_id and ctx.facts.get(('show_session',stop.session_id))
                   and ctx.facts['show_session',stop.session_id].program_id==g.target_id)
            if g.target_kind=='food' and stop.kind=='dining':
                valid=any(isinstance(s,facts.Supply) and s.food_id==g.target_id
                          and ctx.facts.get(('vendor',s.vendor_id)) and ctx.facts['vendor',s.vendor_id].place_id==stop.place_id
                          and ctx.ready(s,req.starts_at,req.ends_at) for s in ctx.facts.values())
        if not valid:raise HTTPException(409,detail={'code':'LOCKED_GOAL_CHANGED','stop_id':stop.stop_id,
              'message':'锁定未来目标必须保留。请先通过现有候选确认流程明确解除或更换，再重新规划。'})
    fixed=set(getattr(body,'fixed_goal_ids',()))|{s.stop_id for s in locked}
    if fixed and not previous:
        fixed=set()  # First plan: locks protect identity; no previous schedule exists to freeze.
    replacements={};fixed_order=[]
    if previous:
        old={s.goal_id:s for s in previous.activities}
        if fixed-set(old):raise HTTPException(422,detail='固定活动必须来自当前正式行程。')
        for gid in fixed:
            if gid not in goals:raise HTTPException(409,detail='不可删除固定未来活动。')
            s=old[gid];g=goals[gid]
            if g.kind!=s.kind:raise HTTPException(409,detail='固定活动的类型不能改变。')
            if g.target_kind=='program':
                session=ctx.facts.get(('show_session',s.session_id))
                if not session or session.program_id!=g.target_id:raise HTTPException(409,detail='固定节目目标不能替换为其他节目。')
            if g.target_kind=='food':
                supplied=any(isinstance(x,facts.Supply) and x.food_id==g.target_id
                    and ctx.facts.get(('vendor',x.vendor_id)) and ctx.facts['vendor',x.vendor_id].place_id==s.place_id
                    and ctx.ready(x,req.starts_at,req.ends_at) for x in ctx.facts.values())
                if not supplied:raise HTTPException(409,detail='固定门店没有该菜品的有效供应证据。')
            if (g.target_kind=='place' and g.target_id!=s.place_id) or (g.target_kind=='session' and g.target_id!=s.session_id):
                raise HTTPException(409,detail='固定活动地点或场次不能改变。')
            replacements[gid]=g.model_copy(update={'required':True,'fixed_place_id':s.place_id,'fixed_session_id':s.session_id,'duration_seconds':s.duration_seconds,
                'window_start':s.starts_at,'window_end':s.departs_at})
        fixed_order=[s.goal_id for s in previous.activities if s.goal_id in fixed]
    req=req.model_copy(update={'goals':tuple(replacements.get(g.goal_id,g) for g in req.goals)})
    ongoing=None
    if replanning:
        if row['status'] not in ('ACTIVE','PAUSED'):raise HTTPException(422,detail='途中重规划需要游玩中或暂停状态。')
        if not row['anchor_json']:raise HTTPException(422,detail='请先确认当前已知地点。')
        anchor=PositionAnchor.model_validate_json(row['anchor_json'])
        moment=ctx.assessed_at;age=(moment-anchor.confirmed_at).total_seconds()
        if not 0<=age<=900 or not ctx.ready(ctx.facts.get(('place',anchor.place_id)),moment):
            raise HTTPException(422,detail='当前位置过期、来自未来或缺少有效核验资料。')
        if req.business_date!=draft.business_date or req.ends_at<=moment or req.starts_at!=moment+timedelta(seconds=120) or req.origin_place_id!=anchor.place_id:
            # The service owns 'now' and origin, never client clock or a stale hotel/gate.
            raise HTTPException(422,detail='重规划起点必须为当前手动地点，时间由服务端提供。')
        completed=[s for s in prefix if s.progress=='completed']
        if completed and completed[-1].actual_end>anchor.confirmed_at:raise HTTPException(422,detail='当前位置早于已完成记录。')
        candidate_ongoing=getattr(body,'ongoing',None)
        if candidate_ongoing and not body.end_ongoing:
            old=next((s for s in previous.activities if s.goal_id==candidate_ongoing.stop_id),None) if previous else None
            if old is None and previous and previous.ongoing and previous.ongoing['stop_id']==candidate_ongoing.stop_id:
                from .models import ScheduledActivity
                old=ScheduledActivity.model_validate(previous.ongoing['original_activity'])
            next_pending=next((s for s in draft.stops if s.progress=='pending'),None)
            if not next_pending or next_pending.stop_id!=candidate_ongoing.stop_id:raise HTTPException(422,detail='只能把当前下一活动声明为正在进行。')
            if not old or old.place_id!=anchor.place_id or old.goal_id in {s.stop_id for s in prefix}:
                raise HTTPException(422,detail='正在进行的活动必须来自未完成正式行程且位于当前地点。')
            if old.goal_id in goals:raise HTTPException(422,detail='正在进行的活动不能同时作为可重排目标。')
            ongoing={'stop_id':old.goal_id,'place_id':old.place_id,'starts_at':moment.isoformat(),
                     'ends_at':stamp(moment+timedelta(seconds=candidate_ongoing.remaining_seconds)),
                     'remaining_basis':candidate_ongoing.basis,'original_activity':old.model_dump(mode='json')}
            req=req.model_copy(update={'starts_at':moment+timedelta(seconds=candidate_ongoing.remaining_seconds)})
            if req.starts_at>=req.ends_at:raise HTTPException(422,detail='当前活动后已无剩余时间。')
    return req,prefix,ongoing,tuple(fixed_order)


def preview(session,trip_id,body,replanning=False,planning_version=None):
    data=body.model_dump(mode='json');scope=('engine_replan:' if replanning else 'engine_preview:')+str(trip_id)
    with connect() as db:
        cached=state.receipt(db,session,body.request_id,scope,data)
        if cached is not None:return cached
        row=state.owned(db,session,trip_id);state.check_version(row,body.expected_version);check_open(row)
        ctx=context(db);oldrow=route_row(db,trip_id);previous=Itinerary.model_validate_json(oldrow['itinerary_json']) if oldrow else None
        req=body.requirements
        if not replanning and req.starts_at<ctx.assessed_at:raise HTTPException(422,detail='首次规划不能从已过去的时刻出发。')
        if replanning:
            anchor=PositionAnchor.model_validate_json(row['anchor_json']) if row['anchor_json'] else None
            if not anchor:raise HTTPException(422,detail='请先确认当前位置。')
            done={s.stop_id for s in CityDraft.model_validate_json(row['draft_json']).stops if s.progress!='pending'}
            if body.ongoing is None and previous:
                current=next((s for s in previous.activities if s.starts_at<=ctx.assessed_at<s.departs_at
                              and s.goal_id not in done|set(body.skipped_goal_ids) and s.place_id==anchor.place_id),None)
                reserved=previous.ongoing if previous.ongoing and previous.ongoing['stop_id'] not in done|set(body.skipped_goal_ids) else None
                if current:
                    body=body.model_copy(update={'ongoing':OngoingInput(stop_id=current.goal_id,
                         remaining_seconds=max(1,int((current.departs_at-ctx.assessed_at).total_seconds())),basis='prior_plan_estimate')})
                elif reserved:
                    remaining=int((datetime.fromisoformat(reserved['ends_at'])-ctx.assessed_at).total_seconds())
                    if remaining<=0:raise HTTPException(422,detail='当前活动预计结束时刻已过，请报告完成或明确剩余时长。')
                    body=body.model_copy(update={'ongoing':OngoingInput(stop_id=reserved['stop_id'],remaining_seconds=remaining,basis='prior_plan_estimate')})
            if body.end_ongoing and not body.ongoing:raise HTTPException(422,detail='请指定要结束的当前活动。')
            if body.restore_route_version:
                historical=route_row(db,trip_id,body.restore_route_version)
                if not historical:raise HTTPException(404,detail='历史路线版本不存在。')
                req=Requirements.model_validate_json(historical['requirements_json'])
                done={s.stop_id for s in CityDraft.model_validate_json(row['draft_json']).stops if s.progress!='pending'}
                req=req.model_copy(update={'goals':tuple(g for g in req.goals if g.goal_id not in done)})
                if not req.goals:raise HTTPException(422,detail='历史目标均已处理，无需恢复。')
            if body.ongoing:
                req=req.model_copy(update={'goals':tuple(g for g in req.goals if g.goal_id!=body.ongoing.stop_id)})
            try:req=Requirements.model_validate(req.model_dump()|{'starts_at':ctx.assessed_at+timedelta(seconds=120),'origin_place_id':anchor.place_id})
            except ValidationError:raise HTTPException(422,detail='当前日期或剩余时间不足，请调整结束时间或另建行程。') from None
        req,prefix,ongoing,fixed=protect_requirements(row,req,replanning,previous,body,ctx)
        spending={'history_cost_fen':0,'history_cost_unknown':False,'history_cost_basis':'none'}
        if replanning:
            spending['history_cost_unknown']=True
            if body.spent_cost_fen is not None:
                if body.spend_as_of is None or not 0<=(ctx.assessed_at-body.spend_as_of).total_seconds()<=900:
                    raise HTTPException(422,detail='已支出费用需附当前15分钟内的明确报告时刻。')
                actual_ends=[s.actual_end for s in prefix if s.progress=='completed']
                if actual_ends and body.spend_as_of<max(actual_ends):raise HTTPException(422,detail='费用报告不能早于已完成活动。')
                spending={'history_cost_fen':body.spent_cost_fen,'history_cost_unknown':False,'history_cost_basis':'user_reported'}
                # Preserve room for the current activity's remaining charge; unknown is never free.
                if ongoing:
                    charges=[c for c in ctx.operations.costs if c.place_id==ongoing['place_id'] and ctx.evidence_ready(c.evidence,req.starts_at,req.ends_at)]
                    if len(charges)!=1:spending['history_cost_unknown']=True
                    else:spending['history_cost_fen']+=charges[0].upper_fen
            elif body.spend_as_of is not None:raise HTTPException(422,detail='费用报告时刻需要对应金额。')
        from ..basket import version as basket_version
        basket_baseline=basket_version(db,trip_id)
        from .. import exploration_state
        planning_baseline=exploration_state.read(db,trip_id)['version']
        if planning_version is not None:exploration_state.guard(db,trip_id,planning_version)
        baseline=progress(row);fingerprint=ctx.fingerprint();base_version=row['version']
    ctx=live_travel.enrich(ctx,req)
    result=(replan_trip(req,ctx,completed_prefix=tuple(s for s in prefix if s.progress=='completed'),
                        prior_itinerary=previous,ongoing=ongoing,fixed_order=fixed,resolved_count=len(prefix),**spending) if replanning else plan_trip(req,ctx))
    comparison=compare_itineraries(previous,result.itineraries[0]) if result.itineraries else None
    candidate_id=str(uuid4()) if result.status=='feasible' else None
    expiry=stamp(now()+timedelta(minutes=15))
    response={'status':'preview' if candidate_id else result.status,'candidate_id':candidate_id,'trip_id':str(trip_id),
              'base_version':base_version,'expires_at':expiry if candidate_id else None,
              'result':result.model_dump(mode='json'),'changes':comparison,
              'alternative_changes':[compare_itineraries(previous,i) for i in result.itineraries],'must_confirm':bool(candidate_id)}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached=state.receipt(db,session,body.request_id,scope,data)
        if cached is not None:return cached
        current=state.owned(db,session,trip_id);state.check_version(current,base_version);check_open(current)
        from ..basket import guard as basket_guard
        basket_guard(db,trip_id,basket_baseline)
        exploration_state.guard(db,trip_id,planning_baseline)
        if context(db).fingerprint()!=fingerprint:raise HTTPException(409,detail='计算期间事实已变化，请重新预览。')
        if candidate_id:
            extra={'baseline':baseline,'fixed_order':fixed,'ongoing':ongoing,'replanning':replanning,'basket_version':basket_baseline,
                   'planning_version':planning_baseline,
                   'ended_ongoing_id':body.ongoing.stop_id if replanning and body.ongoing and body.end_ongoing else None,
                   'skipped_goal_ids':list(body.skipped_goal_ids) if replanning else [],
                   'spending':spending,
                   'previous_route_version':oldrow['route_version'] if oldrow else None}
            db.execute('INSERT INTO city_engine_candidates VALUES (?,?,?,?,?,?,?,?,0)',
                       (candidate_id,str(trip_id),base_version,fingerprint,req.model_dump_json(),result.model_dump_json(),encoded(extra),expiry))
        state.remember(db,session,body.request_id,trip_id,scope,data,response)
    return response


def confirm_itinerary(session,trip_id,body):
    data=body.model_dump(mode='json');scope='engine_confirm:'+str(trip_id)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached=state.receipt(db,session,body.request_id,scope,data)
        if cached is not None:return cached
        row=state.owned(db,session,trip_id);state.check_version(row,body.expected_version);check_open(row)
        c=db.execute('SELECT * FROM city_engine_candidates WHERE id=? AND trip_id=?',(str(body.candidate_id),str(trip_id))).fetchone()
        if not c or c['consumed'] or c['expires_at']<=stamp(now()):raise HTTPException(409,detail='候选已过期、清除或被确认。')
        if c['base_version']!=row['version']:raise HTTPException(409,detail='候选基线版本已变化。')
        ctx=context(db)
        if ctx.fingerprint()!=c['fingerprint']:raise HTTPException(409,detail='关键事实已变化，请重新计算。')
        extra=json.loads(c['progress_json'])
        if 'basket_version' in extra:
            from ..basket import guard as basket_guard
            basket_guard(db,trip_id,extra['basket_version'])
        if 'planning_version' in extra:
            from ..exploration_state import guard as planning_guard
            planning_guard(db,trip_id,extra['planning_version'])
        if extra['baseline']!=progress(row):raise HTTPException(409,detail='行程进度已变化。')
        req=Requirements.model_validate_json(c['requirements_json']);result=SolveResult.model_validate_json(c['result_json'])
        if body.alternative_index>=len(result.itineraries):raise HTTPException(422,detail='备选方案编号不存在。')
        itinerary=result.itineraries[body.alternative_index]
        if any(l.journey.time_basis=='provider_estimate' and
               (not l.journey.quote_expires_at or l.journey.quote_expires_at<=ctx.assessed_at)
               for l in itinerary.legs):
            raise HTTPException(409,detail='高德估时已过期，请重新计算。')
        ctx=live_travel.restore_snapshot(ctx,itinerary)
        if itinerary.legs[0].departs_at<now():raise HTTPException(409,detail='候选出发时刻已过去，请重新预览。')
        if extra['replanning']:
            anchor=PositionAnchor.model_validate_json(row['anchor_json'])
            if not 0<=(now()-anchor.confirmed_at).total_seconds()<=900:raise HTTPException(409,detail='当前位置已过期，请重新确认并规划。')
            # A preview is not allowed to spend the user's elapsed time twice.
            first=itinerary.legs[0]
            if first.departs_at<now():raise HTTPException(409,detail='候选出发时刻已过去，请按当前时间重新预览。')
        problem=prepare(req,ctx,fixed_order=tuple(extra['fixed_order']))
        for key,value in extra['spending'].items():setattr(problem,key,value)
        errors=validate_itinerary(problem,itinerary)
        if errors:raise HTTPException(409,detail={'code':'VALIDATION_FAILED','issues':[e.model_dump() for e in errors]})
        draft=CityDraft.model_validate_json(row['draft_json']);prefix=tuple(s for s in draft.stops if s.progress!='pending')
        if extra.get('ended_ongoing_id'):
            ended=next((s for s in draft.stops if s.stop_id==extra['ended_ongoing_id']),None)
            if ended:prefix=prefix+(ended.model_copy(update={'locked':False,'progress':'skipped','progress_source':'user_reported'}),)
        prefix+=tuple(s.model_copy(update={'locked':False,'progress':'skipped','progress_source':'user_reported'})
                      for s in draft.stops if s.stop_id in extra.get('skipped_goal_ids',()) and s.progress=='pending')
        pending=[Stop(stop_id=s.goal_id,place_id=s.place_id,kind=s.kind,session_id=s.session_id,
                      locked=s.required,duration_seconds=s.duration_seconds) for s in itinerary.activities]
        if extra['ongoing']:
            ongoing_stop=next((s for s in draft.stops if s.stop_id==extra['ongoing']['stop_id']),None)
            if ongoing_stop:pending.insert(0,ongoing_stop)
        newdraft=CityDraft.model_validate(draft.model_dump()|{'business_date':req.business_date,
            'starts_at':draft.starts_at if prefix or extra['replanning'] else req.starts_at,'ends_at':req.ends_at,
            'origin_place_id':draft.origin_place_id if prefix or extra['replanning'] else req.origin_place_id,
            'destination_place_id':req.destination_place_id,'modes':req.modes,'pace':req.pace,'objective':req.objective,'stops':prefix+tuple(pending)})
        new_route=row['route_version']+1
        db.execute('INSERT INTO city_engine_routes VALUES (?,?,?,?,?,?,?)',
                   (str(trip_id),new_route,row['version']+1,c['requirements_json'],itinerary.model_dump_json(),c['fingerprint'],stamp()))
        updated=db.execute('UPDATE city_trip_states SET draft_json=?,version=version+1,route_version=?,updated_at=? WHERE id=? AND version=?',
                          (newdraft.model_dump_json(),new_route,stamp(),str(trip_id),row['version']))
        if updated.rowcount!=1:raise HTTPException(409,detail='并发版本冲突。')
        state.event(db,trip_id,body.request_id,'engine_route_confirmed',row['version'],row['version']+1)
        db.execute('UPDATE city_engine_candidates SET consumed=1 WHERE id=?',(str(body.candidate_id),))
        response={'status':'committed','trip_id':str(trip_id),'version':row['version']+1,'route_version':new_route,
                  'itinerary':itinerary.model_dump(mode='json')}
        state.remember(db,session,body.request_id,trip_id,scope,data,response)
        return response


def read_itinerary(session,trip_id,version=None):
    with connect() as db:
        return read_snapshot(db,session,trip_id,version)


def read_snapshot(db,session,trip_id,version=None):
    """Authorized read in caller's transaction, so a context can share one SQLite snapshot."""
    row=state.owned(db,session,trip_id);saved=route_row(db,trip_id,version)
    if version is not None and not saved:raise HTTPException(404,detail='历史路线版本不存在。')
    if not saved:return {'trip_id':str(trip_id),'itinerary':None,'route_version':0,'version':row['version']}
    itinerary=Itinerary.model_validate_json(saved['itinerary_json']);req=Requirements.model_validate_json(saved['requirements_json']);ctx=context(db)
    fingerprint=ctx.fingerprint()
    ctx=live_travel.restore_snapshot(ctx,itinerary)
    problem=prepare(req,ctx)
    problem.history_cost_fen=itinerary.history_cost_fen;problem.history_cost_unknown=itinerary.history_cost_unknown
    problem.history_cost_basis=itinerary.history_cost_basis
    errors=validate_itinerary(problem,itinerary)
    stale=saved['state_version']!=row['version'] or saved['fingerprint']!=fingerprint or bool(errors)
    return {'trip_id':str(trip_id),'version':row['version'],'route_version':saved['route_version'],
            'itinerary':itinerary.model_dump(mode='json'),'stale':stale,
            'issues':[e.model_dump(mode='json') for e in errors],
            'traffic_refresh_recommended':any(l.journey.quote_expires_at and l.journey.quote_expires_at<=ctx.assessed_at for l in itinerary.legs),
            'notice':'历史结果；需要按当前事实重新核验/规划。' if stale else '依据当前资料满足模型约束；实际交通仍有不确定性。'}
