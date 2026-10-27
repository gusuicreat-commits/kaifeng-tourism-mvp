"""Narrow server-owned adapters. The model never supplies ownership, facts or a commit."""
import json
from uuid import uuid5, UUID

from fastapi import HTTPException
from pydantic import ValidationError

from ...db import connect
from ...memory.storage import now, stamp
from .. import state
from ..state_schema import MutateCityTrip, ConfirmCityChange
from ..engine import service as engine
from ..engine.models import Goal, Requirements, PreviewRequest, ReplanRequest, ReplanRequestV2, TripGoal, ConfirmRequest


def require_single_day(snapshot):
    if getattr(snapshot,'multi_draft',None) or getattr(snapshot,'multi_requirements',None):
        raise HTTPException(503,detail={'status':'unavailable','code':'MULTI_DAY_UNAVAILABLE',
            'message':'此多天写入工具尚未接通；可查看整套行程，途中延误、多待、休息和跳站仅支持今天剩余，不会降为第一天。'})


def current_day_scope(snapshot,quote=''):
    if not getattr(snapshot,'multi_draft',None):return
    from ..schema import TZ
    from ..agent.dates import find_date_expressions, resolve_date, _number
    import re
    today=snapshot.planning.assessed_at.astimezone(TZ).date()
    try:
        dates=[resolve_date(e,today) for e in find_date_expressions(quote)]
        spans=re.findall(r'(?<![第0-9一二三四五六七八九十两])([0-9一二三四五六七八九十两]+)\s*天',quote)
        if any(_number(number)>1 for number in spans):dates.append(None)
        for number in re.findall(r'第([0-9一二三四五六七八九十两]+)天',quote):
            from datetime import timedelta
            dates.append(snapshot.multi_draft.start_date+timedelta(days=_number(number)-1))
    except ValueError:
        dates=[None]
    if snapshot.draft.business_date!=today or snapshot.draft.status=='closed' or len(dates)>1 or any(d!=today for d in dates):
        raise HTTPException(503,detail={'status':'unavailable','code':'MULTI_DAY_SCOPE_UNAVAILABLE',
            'message':'仅支持今天剩余的重排；未来单日、连后续天和历史补录尚未接通，整套正式路线保持不变。'})


def adjustment_request(snapshot,req,request_id,**extra):
    full=getattr(snapshot,'multi_requirements',None)
    if full is None:return ReplanRequest(request_id=request_id,expected_version=snapshot.row['version'],requirements=req,**extra)
    from ..schema import TZ
    today=snapshot.planning.assessed_at.astimezone(TZ).date()
    current_day_scope(snapshot)
    pending={s.stop_id for s in snapshot.draft.stops if s.progress=='pending'}
    original={g.goal_id:g for g in full.goals}
    goals=[g for g in full.goals if g.goal_id not in pending]
    for g in req.goals:
        old=original.get(g.goal_id)
        goals.append(TripGoal.model_validate(g.model_dump()|{
            'allowed_dates':old.allowed_dates if old else (today,),
            'fixed_date':old.fixed_date if old else today}))
    full=type(full).model_validate(full.model_dump()|{'goals':goals})
    return ReplanRequestV2(request_id=request_id,expected_version=snapshot.row['version'],requirements=full,
        scope='current_day',day_id=snapshot.day_id,**extra)


def requirements(snapshot):
    """Reconcile editor state with last confirmed goal semantics; never read constraints from chat."""
    draft = snapshot.draft
    original = {g.goal_id:g for g in snapshot.requirements.goals} if snapshot.requirements else {}
    scheduled = {s['goal_id']:s for s in (snapshot.route.get('itinerary') or {}).get('activities',[])}
    goals = []
    for stop in draft.stops:
        if stop.progress != 'pending': continue
        if stop.kind == 'return':
            if stop.place_id != draft.destination_place_id: raise HTTPException(422,detail='返程活动与已保存的最终目的地不一致，请先核对。')
            continue
        old = original.get(stop.stop_id)
        realization = scheduled.get(stop.stop_id)
        if old and realization and realization['place_id'] == stop.place_id and realization['session_id'] == stop.session_id and old.kind == stop.kind:
            goal = old.model_copy(update={'required':stop.locked})
        else:
            goal = Goal(goal_id=stop.stop_id,target_kind='session' if stop.session_id else 'place',
                        target_id=stop.session_id or stop.place_id,kind=stop.kind,required=stop.locked,
                        duration_seconds=stop.duration_seconds,window_start=stop.window_start,window_end=stop.window_end)
        goals.append(goal)
    fields = snapshot.requirements.model_dump() if snapshot.requirements else {}
    fields.update(business_date=draft.business_date,starts_at=draft.starts_at,ends_at=draft.ends_at,
                  origin_place_id=draft.origin_place_id,destination_place_id=draft.destination_place_id,
                  modes=draft.modes,pace=draft.pace,objective=draft.objective,goals=goals)
    try: return Requirements.model_validate(fields)
    except ValidationError:
        raise HTTPException(422,detail={'code':'REQUIREMENTS_MISSING','message':'请先补齐日期、时间、起终点和旅行目标。'}) from None


def preview(session, trip_id, snapshot, intent, request_id):
    require_single_day(snapshot)
    req = requirements(snapshot)
    pending = {s.stop_id:s for s in snapshot.draft.stops if s.progress == 'pending'}
    for identity in intent.preserve_stop_ids:
        if identity not in pending: raise HTTPException(422,detail='要保留的目标不属于当前剩余行程。')
    preserved = set(intent.preserve_stop_ids)
    if intent.preserve_shows:
        preserved |= {s.stop_id for s in pending.values() if s.kind == 'show'}
    if intent.remove_stop_id:
        removed = pending.get(intent.remove_stop_id)
        if not removed: raise HTTPException(422,detail='不能删除已完成或不存在的活动。')
        if removed.locked or removed.stop_id in preserved:
            raise HTTPException(409,detail='该目标需要保留；请先明确解除锁定，不能静默删除。')
    goals = [g for g in req.goals if g.goal_id != intent.remove_stop_id]
    additions = [('place',intent.add_place_id),('food',intent.add_food_id)]
    for kind, identity in additions:
        if not identity: continue
        item = snapshot.planning.facts.get((kind,identity))
        if not item or not snapshot.planning.ready(item,req.starts_at,req.ends_at):
            raise HTTPException(422,detail='新增目标没有适用的核验资料。')
        goal_kind = 'dining' if kind == 'food' or getattr(item,'place_type',None) == 'restaurant' else 'visit'
        goals.append(Goal(goal_id='added:'+uuid5(UUID(str(request_id)),kind+':'+identity).hex,target_kind=kind,target_id=identity,kind=goal_kind,required=True))
    if intent.meal_timing:
        meals = [g for g in goals if g.kind == 'dining']
        if len(meals) != 1: raise HTTPException(422,detail={'code':'MEAL_AMBIGUOUS','message':'请明确要调整哪一次用餐或哪家店。'})
        # Make the user's requested meal mandatory instead of letting optimization silently omit it.
        show_stops = [s for s in pending.values() if s.kind == 'show' and (not preserved or s.stop_id in preserved)]
        if not show_stops: raise HTTPException(422,detail='请明确用餐要安排在哪场演出之前或之后。')
        sessions = [snapshot.planning.facts.get(('show_session',s.session_id)) for s in show_stops]
        if any(not s or not s.ends_at or not snapshot.planning.ready(s,s.starts_at,s.ends_at) for s in sessions):
            raise HTTPException(422,detail='演出日期、结束时间或有效资料不足，不能确定用餐衔接。')
        meal = meals[0]
        if intent.meal_timing == 'before_shows':
            value = min(s.starts_at for s in sessions)
            meal = meal.model_copy(update={'required':True,'window_end':min(meal.window_end,value) if meal.window_end else value})
        else:
            value = max(s.ends_at for s in sessions)
            meal = meal.model_copy(update={'required':True,'window_start':max(meal.window_start,value) if meal.window_start else value})
        goals = [meal if g.goal_id == meal.goal_id else g for g in goals]
    goals = [g.model_copy(update={'required':True}) if g.goal_id in preserved else g for g in goals]
    updates = {'goals':goals}
    for field in ('objective','pace','ends_at'):
        if getattr(intent,field) is not None: updates[field] = getattr(intent,field)
    try: req = Requirements.model_validate(req.model_dump() | updates)
    except ValidationError: raise HTTPException(422,detail='新的时间窗或目标要求互相冲突，请明确取舍。') from None
    if snapshot.row['status'] in ('ACTIVE','PAUSED'):
        body = ReplanRequest(request_id=request_id,expected_version=snapshot.row['version'],requirements=req,
                             fixed_goal_ids=tuple(sorted(preserved)),restore_route_version=intent.restore_route_version)
        result = engine.preview(session,trip_id,body,replanning=True)
        if result.get('changes'): result['changes'] = changes_for_remaining(result['changes'],snapshot.draft)
        result['alternative_changes'] = [changes_for_remaining(c,snapshot.draft) for c in result.get('alternative_changes',[])]
        return result
    if intent.restore_route_version:
        raise HTTPException(422,detail='恢复历史需处于可继续游玩的状态并确认当前位置。')
    body = PreviewRequest(request_id=request_id,expected_version=snapshot.row['version'],requirements=req)
    return engine.preview(session,trip_id,body)


def changes_for_remaining(changes, draft):
    if 'days' in changes:
        days=[d|changes_for_remaining({k:v for k,v in d.items() if k not in ('day_id','business_date')},draft)
              if d['day_id']==getattr(draft,'day_id',None) else d for d in changes['days']]
        current=next((d for d in days if d['day_id']==getattr(draft,'day_id',None)),{})
        return changes|{k:v for k,v in current.items() if k not in ('day_id','business_date')}|{'days':days}
    history = [s.stop_id for s in draft.stops if s.progress != 'pending']
    return changes | {'removed':[identity for identity in changes['removed'] if identity not in history],
                      'history_preserved':history}


def propose_state(session, trip_id, snapshot, intent, request_id):
    require_single_day(snapshot)
    if intent.operation == 'position':
        if not intent.place_id: raise HTTPException(422,detail='请明确你手动报告的当前位置。')
        action = {'operation':'set_anchor','anchor':{'place_id':intent.place_id,'confirmed_at':stamp(),'source':'manual_known_place'}}
    elif intent.operation in ('complete','skip'):
        stop = next((s for s in snapshot.draft.stops if s.progress == 'pending'),None)
        if not stop or stop.stop_id != intent.stop_id: raise HTTPException(422,detail='请明确当前下一活动，不能修改已执行历史。')
        action = {'operation':'skip_stop','stop_id':stop.stop_id}
        if intent.operation == 'complete':
            if intent.actual_start is None: raise HTTPException(422,detail={'code':'ACTUAL_START_REQUIRED','message':'请补充实际开始时间；计划开始时间不能冒充实际记录。'})
            action = {'operation':'mark_completed','stop_id':stop.stop_id,'actual_start':intent.actual_start,
                      'actual_end':intent.actual_end or now()}
    else:
        changes = {k:getattr(intent,k) for k in ('objective','pace','interests') if getattr(intent,k) is not None}
        if not changes: raise HTTPException(422,detail='没有可以确认的本次旅行偏好。')
        draft = type(snapshot.draft).model_validate(snapshot.draft.model_dump() | changes)
        action = {'operation':'save_draft','draft':draft.model_dump(mode='json')}
    body = MutateCityTrip(request_id=request_id,expected_version=snapshot.row['version'],action=action)
    return state.mutate(session,trip_id,body,propose=True)


def confirm(session, trip_id, body):
    # No free-text or model output can invoke this adapter; HTTP requires explicit confirmed:true.
    if body.target_kind == 'route':
        return engine.confirm_itinerary(session,trip_id,ConfirmRequest(request_id=body.request_id,
            expected_version=body.expected_version,candidate_id=body.candidate_id,alternative_index=body.alternative_index))
    with connect() as db:
        from ..versions import guard_single_day
        guard_single_day(db,state.owned(db,session,trip_id))
        record = db.execute('SELECT action_json FROM city_proposals WHERE id=? AND trip_id=?', (str(body.candidate_id),str(trip_id))).fetchone()
        if record and json.loads(record['action_json']).get('operation') in ('assistant_intent','assistant_v3_intent'):
            raise HTTPException(409,detail='这只是待补充意愿，不能当作正式修改确认。请先生成可校验候选。')
    return state.confirm(session,trip_id,ConfirmCityChange(request_id=body.request_id,
                        expected_version=body.expected_version,proposal_id=body.candidate_id))


def discard(session, trip_id, body):
    data = body.model_dump(mode='json'); scope = 'assistant_discard:' + str(trip_id)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        from ..versions import read_result, read_requirements, read_draft
        state.owned(db,session,trip_id)
        cached = state.receipt(db,session,body.request_id,scope,data)
        if cached is not None: return cached
        row = state.owned(db,session,trip_id); state.check_version(row,body.expected_version)
        table = 'city_engine_candidates' if body.target_kind == 'route' else 'city_proposals'
        found = db.execute(f'SELECT * FROM {table} WHERE id=? AND trip_id=?', (str(body.candidate_id),str(trip_id))).fetchone()
        if not found: raise HTTPException(404,detail='找不到本行程的候选。')
        if body.target_kind=='route':
            read_requirements(found['requirements_json']);read_result(found['result_json'])
        else:
            action=json.loads(found['action_json'])
            if 'draft' in action:read_draft(action['draft'])
        db.execute(f'UPDATE {table} SET consumed=1 WHERE id=?', (str(body.candidate_id),))
        result = {'status':'discarded','candidate_id':str(body.candidate_id),'trip_id':str(trip_id),
                  'version':row['version'],'route_version':row['route_version'],'formal_state_changed':False}
        state.remember(db,session,body.request_id,trip_id,scope,data,result)
        return result
