"""M7-owned city draft/progress aggregate. No solver or unverified route is committed."""
import json
from datetime import datetime, timedelta
from uuid import uuid4
from fastapi import HTTPException
from pydantic import ValidationError

from ..db import connect
from ..memory.session import recheck
from ..memory.storage import digest, encoded, now, stamp
from . import facts
from .state_schema import (CityDraft, MutateCityTrip, CityDraftV2, CreateCityTripV2,
                            MutateCityTripV2, SaveDraftV2, MarkCompletedV2, SkipStopV2,
                            SetAnchorV2, CloseDay)
from .schema import TZ
from .versions import read_draft, single_day, guard_single_day

TERMINAL = {'COMPLETED', 'CANCELLED', 'ARCHIVED'}
TRANSITIONS = {'DRAFT': {'ACTIVE', 'CANCELLED'}, 'ACTIVE': {'PAUSED', 'COMPLETED', 'CANCELLED'},
               'PAUSED': {'ACTIVE', 'CANCELLED'}, 'COMPLETED': {'ARCHIVED'}, 'CANCELLED': {'ARCHIVED'}, 'ARCHIVED': set()}


def owned(db, session, identity):
    recheck(db,session)
    row = db.execute('SELECT * FROM city_trip_states WHERE id=? AND session_id=?', (str(identity),session.id)).fetchone()
    if not row or (row['ended_at'] and datetime.fromisoformat(row['ended_at']) + timedelta(hours=72) <= now()):
        raise HTTPException(404,detail='城市行程不存在、已到期或不属于本会话。')
    return row


def check_version(row, expected):
    if row['version'] != expected:
        raise HTTPException(409,detail={'code':'VERSION_CONFLICT','message':'城市行程已被另一请求修改，请重新加载。',
                                       'current_version':row['version']})


def catalog_revision(items):
    return digest(encoded(sorted((k[0],k[1],r.version) for k,r in items.items())))


def validate_refs(draft, items):
    for identity in (draft.origin_place_id, draft.destination_place_id):
        if identity: facts.require(items,'place',identity)
    for identity in draft.dish_interest_ids: facts.require(items,'food',identity)
    for stop in draft.stops:
        place = facts.require(items,'place',stop.place_id)
        if stop.kind == 'dining' and place.place_type != 'restaurant':
            raise HTTPException(422,detail='餐饮停留必须选择具体餐饮地点，菜品不能直接作为门店。')
        if stop.session_id:
            session = facts.require(items,'show_session',stop.session_id)
            if session.stage_place_id != stop.place_id: raise HTTPException(422,detail='场次与舞台不匹配。')
        if stop.evidence_refs: raise HTTPException(422,detail='游客操作不能提供核验事实引用。')


def validate_refs_v2(draft, items):
    """Validate every day independently; V2 has no top-level place/stop fields."""
    for day in draft.days:
        validate_refs(day,items)
    for overnight in draft.overnights:
        if overnight.place_id: facts.require(items, 'place', overnight.place_id)
        if overnight.source_kind == 'selected_hotel':
            # This is the visitor create path, before a trip has an owned
            # service workspace. Even complete-looking metadata is not proof
            # of a confirmed selection. The engine builds bindings server-side.
            raise HTTPException(422, detail={'code':'INVALID_HOTEL_BINDING',
                'message':'请先创建行程，再确认酒店候选并预览路线；不能在新建草稿中自行提供酒店绑定。'})
        elif overnight.source_kind != 'user_place':
            raise HTTPException(422, detail={'code':'INVALID_OVERNIGHT_SOURCE',
                                              'message':'过夜点来源无效。'})


def _v2_day(draft, day_id):
    return next((d for d in draft.days if d.day_id == day_id), None)


def _v2_today(draft, day):
    today = now().astimezone(TZ).date()
    if day.business_date < today or day.status == 'closed':
        raise HTTPException(409, detail={'code': 'DAY_FROZEN', 'message': '历史日期已冻结，首轮不支持历史补录。', 'day_id': day.day_id})
    if day.business_date > today:
        raise HTTPException(409, detail={'code': 'FUTURE_DAY_LOCKED', 'message': '后续日期已锁定，只能查看，不能直接修改。', 'day_id': day.day_id})
    return day


def _v2_mutate(row, action, items):
    if isinstance(action, SaveDraftV2):
        raise HTTPException(503, detail={'status': 'unavailable', 'code': 'MULTI_DAY_DRAFT_WRITE_UNAVAILABLE',
                                         'message': '多天草稿整体写入尚未接通；请使用多天预览、整套确认流程。'})
    draft = read_draft(row['draft_json'])
    if not isinstance(draft, CityDraftV2):
        raise HTTPException(409, detail={'code': 'SCHEMA_MISMATCH', 'message': '请求为多天状态，但当前行程不是多天草稿。'})
    status, ended, anchor = row['status'], row['ended_at'], json.loads(row['anchor_json']) if row['anchor_json'] else None
    if status in TERMINAL and action.operation != 'transition':
        raise HTTPException(422, detail='已结束行程不能继续修改。')
    if isinstance(action, (SetAnchorV2, MarkCompletedV2, SkipStopV2, CloseDay)):
        day = _v2_day(draft, action.day_id)
        if day is None:
            raise HTTPException(404, detail={'code': 'DAY_NOT_FOUND', 'day_id': action.day_id})
        day = _v2_today(draft, day)
    else:
        day = None
    important = False
    if isinstance(action, SetAnchorV2):
        place = facts.require(items, 'place', action.anchor.place_id)
        if not facts.status(place, items)['ready']:
            raise HTTPException(422, detail='当前位置需选择当前有效、已核验的真实地点。')
        age = (now() - action.anchor.confirmed_at).total_seconds()
        if not 0 <= age <= 900:
            raise HTTPException(422, detail='当前位置时刻过期或来自未来，请重新确认。')
        if not day.starts_at or not day.ends_at or not day.starts_at <= action.anchor.confirmed_at <= day.ends_at:
            raise HTTPException(422, detail='当前位置时刻不在当日行程范围。')
        completed = [s for s in day.stops if s.progress == 'completed']
        if completed and action.anchor.confirmed_at < completed[-1].actual_end:
            raise HTTPException(422, detail='当前位置不能早于完成记录。')
        anchor = action.anchor.model_dump(mode='json')
    elif isinstance(action, (MarkCompletedV2, SkipStopV2)):
        if status != 'ACTIVE':
            raise HTTPException(422, detail='仅记录游玩中的行程可以更新进度。')
        next_stop = next((s for s in day.stops if s.progress == 'pending'), None)
        if not next_stop or next_stop.stop_id != action.stop_id:
            raise HTTPException(422, detail='请按当日顺序处理下一活动，不能越过前缀改历史。')
        if isinstance(action, MarkCompletedV2):
            if action.actual_end > now():
                raise HTTPException(422, detail='实际完成时间不能来自未来。')
            if not day.starts_at or not day.ends_at or action.actual_start < day.starts_at or action.actual_end > day.ends_at or action.actual_end <= action.actual_start:
                raise HTTPException(422, detail='实际活动时间必须在当日时间窗内。')
            if anchor and datetime.fromisoformat(anchor['confirmed_at'])>action.actual_start:
                raise HTTPException(422, detail='实际活动开始不能早于最后确认的当前位置。')
            patch = {'progress': 'completed', 'actual_start': action.actual_start,
                     'actual_end': action.actual_end, 'progress_source': 'user_reported'}
        else:
            patch = {'progress': 'skipped', 'locked': False, 'progress_source': 'user_reported'}
            important = next_stop.locked
        changed = type(next_stop).model_validate(next_stop.model_dump() | patch)
        new_day = day.model_copy(update={'stops': tuple(changed if s.stop_id == changed.stop_id else s for s in day.stops)})
        draft = draft.model_copy(update={'days': tuple(new_day if d.day_id == day.day_id else d for d in draft.days)})
    elif isinstance(action, CloseDay):
        if status not in ('ACTIVE', 'PAUSED'):
            raise HTTPException(422, detail='仅游玩中或暂停的行程可以关闭当天。')
        if any(s.progress == 'pending' for s in day.stops):
            raise HTTPException(409, detail={'code': 'DAY_HAS_PENDING', 'message': '当天仍有未核对活动，不能直接关闭。', 'day_id': day.day_id})
        new_day = day.model_copy(update={'status': 'closed'})
        draft = draft.model_copy(update={'days': tuple(new_day if d.day_id == day.day_id else d for d in draft.days)})
        important = True
    else:
        if action.status not in TRANSITIONS[status]:
            raise HTTPException(422, detail='不允许该城市行程状态转换。')
        if action.status == 'ACTIVE' and any(not d.origin_place_id or not d.destination_place_id for d in draft.days):
            raise HTTPException(422, detail='请先补齐每一天的起终点。')
        status = action.status
        important = status in ('COMPLETED', 'CANCELLED') and any(s.locked and s.progress == 'pending' for d in draft.days for s in d.stops)
        ended = ended or (stamp() if status in TERMINAL else None)
    draft = CityDraftV2.model_validate(draft.model_dump())
    return draft, anchor, status, ended, important


def readiness(draft, items):
    if isinstance(draft,CityDraftV2):
        today=now().astimezone(TZ).date()
        return {'days':[{'day_id':d.day_id,**readiness(d,items),
                         'frozen':d.business_date<today or d.status=='closed',
                         'future_locked':d.business_date>today} for d in draft.days],
                'planning_available':False,'notice':'多天需求与进度按日保存；路线须经整套预览、确认后读取。'}
    missing = [key for key in ('business_date','starts_at','ends_at','origin_place_id','destination_place_id')
               if getattr(draft,key) is None]
    if not draft.stops: missing.append('stops')
    issues = []
    moment = draft.starts_at or now()
    used = {('place', i) for i in (draft.origin_place_id,draft.destination_place_id) if i}
    used |= {('food', i) for i in draft.dish_interest_ids}
    for stop in draft.stops:
        used.add(('place', stop.place_id))
        if stop.session_id:
            used.add(('show_session',stop.session_id))
            s = items.get(('show_session',stop.session_id))
            if s and s.business_date != draft.business_date:
                issues.append({'code':'SESSION_DATE_MISMATCH','stop_id':stop.stop_id})
        if stop.kind in ('visit','dining','rest','return') and stop.duration_seconds is None:
            issues.append({'code':'DURATION_UNKNOWN','stop_id':stop.stop_id})
        if stop.kind in ('visit','dining','show'):
            rules = [r for r in items.values() if isinstance(r,facts.OpenRule) and r.place_id == stop.place_id
                     and r.business_date == draft.business_date and facts.status(r,items,moment)['ready']]
            if not rules: issues.append({'code':'OPENING_OR_ACCESS_RULE_UNKNOWN','stop_id':stop.stop_id})
        if stop.kind == 'dining':
            vendors = {r.fact_id for r in items.values() if isinstance(r,facts.Vendor) and r.place_id == stop.place_id}
            for food in draft.dish_interest_ids:
                supply = [r for r in items.values() if isinstance(r,facts.Supply) and r.vendor_id in vendors and r.food_id == food
                          and facts.status(r,items,moment)['ready']]
                if not supply: issues.append({'code':'SUPPLY_NOT_VERIFIED','stop_id':stop.stop_id,'food_id':food})
    for key in sorted(used):
        record = items.get(key)
        check = facts.status(record,items,moment) if record else {'ready':False,'issues':['FACT_MISSING']}
        if not check['ready']: issues.append({'code':'FACT_NOT_READY','kind':key[0],'id':key[1],'issues':check['issues']})
    return {'missing_fields':missing,'issues':issues,'planning_available':False,
            'notice':'已保存的是需求与进度，尚未计算城市路线；待核验信息不代表可以实际赶上。'}


def describe(row, items):
    draft = read_draft(row['draft_json'])
    return {'id':row['id'],'schema_version':'city-state/2' if isinstance(draft,CityDraftV2) else 'city-state/1','status':row['status'],
            'draft':draft.model_dump(mode='json'),'anchor':json.loads(row['anchor_json']) if row['anchor_json'] else None,
            'version':row['version'],'route_version':row['route_version'],'updated_at':row['updated_at'],
            'readiness':readiness(draft,items)|({'notice':'已保存城市规划版本，请通过规划工具读取并核对其时效；此编辑页暂不提供导航。'} if row['route_version'] else {})}


def receipt(db, session, request_id, scope, body):
    recheck(db,session)
    row = db.execute('SELECT * FROM city_receipts WHERE session_id=? AND request_id=?',(session.id,str(request_id))).fetchone()
    if row is None:return None
    if row['scope'] != scope or row['body_hash'] != digest(encoded(body)):
        raise HTTPException(409,detail={'code':'IDEMPOTENCY_CONFLICT','message':'该请求编号已用于其他城市操作或内容。'})
    owned(db,session,row['trip_id'])
    return json.loads(row['result_json'])


def remember(db,session,request_id,identity,scope,body,result):
    db.execute('INSERT INTO city_receipts VALUES (?,?,?,?,?,?,?)',
               (session.id,str(request_id),str(identity),scope,digest(encoded(body)),encoded(result),stamp()))


def event(db,identity,request_id,operation,before,after):
    db.execute('INSERT INTO city_trip_events VALUES (?,?,?,?,?,?,?)',
               (str(uuid4()),str(identity),str(request_id),operation,before,after,stamp()))


def create(session,body):
    if isinstance(body, CreateCityTripV2):
        return create_v2(session, body)
    single_day(body.draft)
    data=body.model_dump(mode='json');scope='create'
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached=receipt(db,session,body.request_id,scope,data)
        if cached is not None:return cached
        items=facts.records(db);validate_refs(body.draft,items)
        if any(s.progress!='pending' for s in body.draft.stops):raise HTTPException(422,detail='新行程不能伪造完成或跳过记录。')
        identity=str(uuid4())
        db.execute('INSERT INTO city_trip_states(id,session_id,status,draft_json,updated_at) VALUES (?,?,?,?,?)',
                   (identity,session.id,'DRAFT',encoded(data['draft']),stamp()))
        db.execute('UPDATE conversation_sessions SET active_city_trip_id=? WHERE id=?',(identity,session.id))
        result=describe(owned(db,session,identity),items)
        event(db,identity,body.request_id,'created',0,1)
        remember(db,session,body.request_id,identity,scope,data,result)
        return result


def create_v2(session, body):
    draft = body.draft
    data = body.model_dump(mode='json'); scope = 'create'
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached = receipt(db, session, body.request_id, scope, data)
        if cached is not None: return cached
        items = facts.records(db); validate_refs_v2(draft, items)
        if any(s.progress != 'pending' for d in draft.days for s in d.stops):
            raise HTTPException(422, detail='新行程不能伪造完成或跳过记录。')
        if any(d.status != 'pending' for d in draft.days):
            raise HTTPException(422, detail='新行程不能伪造活动日或闭日状态。')
        identity = str(uuid4())
        db.execute('INSERT INTO city_trip_states(id,session_id,status,draft_json,updated_at) VALUES (?,?,?,?,?)',
                   (identity, session.id, 'DRAFT', encoded(data['draft']), stamp()))
        db.execute('UPDATE conversation_sessions SET active_city_trip_id=? WHERE id=?', (identity, session.id))
        result = describe(owned(db, session, identity), items)
        event(db, identity, body.request_id, 'created', 0, 1)
        remember(db, session, body.request_id, identity, scope, data, result)
        return result


def prepare(row,action,items):
    if row['status'] in TERMINAL and action.operation!='transition':raise HTTPException(422,detail='已结束行程不能继续修改。')
    previous=read_draft(row['draft_json']);single_day(previous,getattr(action,'draft',None));draft=previous
    anchor=json.loads(row['anchor_json']) if row['anchor_json'] else None
    status=row['status'];ended=row['ended_at'];important=False
    if action.operation=='save_draft':
        draft=action.draft;validate_refs(draft,items)
        previous_resolved=tuple(s for s in previous.stops if s.progress!='pending')
        resolved=tuple(s for s in draft.stops if s.progress!='pending')
        if previous_resolved!=resolved or draft.stops[:len(previous_resolved)]!=previous.stops[:len(previous_resolved)]:
            raise HTTPException(422,detail='不能通过保存草稿改写已完成或跳过的前缀。')
        if previous_resolved and (previous.business_date!=draft.business_date or previous.starts_at!=draft.starts_at):
            raise HTTPException(422,detail='已有进度，不能改写出发日期或开始时间。')
        new_by_id={s.stop_id:s for s in draft.stops}
        important=any(s.locked and s.progress=='pending' and new_by_id.get(s.stop_id)!=s for s in previous.stops)
        important|=any(getattr(previous,key) is not None and getattr(previous,key)!=getattr(draft,key)
                       for key in ('business_date','starts_at','ends_at','origin_place_id','destination_place_id'))
        if anchor:
            confirmed=datetime.fromisoformat(anchor['confirmed_at'])
            if (draft.starts_at and confirmed<draft.starts_at) or (draft.ends_at and confirmed>draft.ends_at):
                raise HTTPException(422,detail='修改后的时间不能排除当前锚点。')
    elif action.operation=='set_anchor':
        place=facts.require(items,'place',action.anchor.place_id)
        if not facts.status(place,items)['ready']:raise HTTPException(422,detail='当前位置需选择当前有效、已核验的真实地点。')
        age=(now()-action.anchor.confirmed_at).total_seconds()
        if not 0<=age<=900:raise HTTPException(422,detail='当前位置时刻过期或来自未来，请重新确认。')
        completed=[s for s in draft.stops if s.progress=='completed']
        if completed and action.anchor.confirmed_at<completed[-1].actual_end:raise HTTPException(422,detail='当前位置不能早于完成记录。')
        if ((draft.starts_at and action.anchor.confirmed_at<draft.starts_at)
                or (draft.ends_at and action.anchor.confirmed_at>draft.ends_at)):
            raise HTTPException(422,detail='当前位置时刻不在行程范围。')
        anchor=action.anchor.model_dump(mode='json')
    elif action.operation in ('mark_completed','skip_stop'):
        if status!='ACTIVE':raise HTTPException(422,detail='仅记录游玩中的行程可以更新进度。')
        next_stop=next((s for s in draft.stops if s.progress=='pending'),None)
        if not next_stop or next_stop.stop_id!=action.stop_id:raise HTTPException(422,detail='请按顺序处理下一活动，不能越过前缀改历史。')
        patch={'progress':'skipped','locked':False,'progress_source':'user_reported'}
        important=next_stop.locked and action.operation=='skip_stop'
        if action.operation=='mark_completed':
            if action.actual_end>now():raise HTTPException(422,detail='实际完成时间不能来自未来。')
            patch={'progress':'completed','actual_start':action.actual_start,'actual_end':action.actual_end,'progress_source':'user_reported'}
            if anchor and datetime.fromisoformat(anchor['confirmed_at'])>action.actual_start:
                raise HTTPException(422,detail='实际活动开始不能早于最后确认的当前位置。')
        changed=type(next_stop).model_validate(next_stop.model_dump()|patch)
        draft=CityDraft.model_validate(draft.model_dump()|{'stops':[changed if s.stop_id==changed.stop_id else s for s in draft.stops]})
    else:
        if action.status not in TRANSITIONS[status]:raise HTTPException(422,detail='不允许该城市行程状态转换。')
        if action.status=='ACTIVE' and readiness(draft,items)['missing_fields']:
            raise HTTPException(422,detail='请先补齐日期、起终点、时间和活动。')
        important=action.status in ('COMPLETED','CANCELLED') and any(s.locked and s.progress=='pending' for s in draft.stops)
        status=action.status
        # Archiving must not restart the 72h retention clock.
        ended=ended or (stamp() if status in TERMINAL else None)
    return draft,anchor,status,ended,bool(important)


def mutate(session,identity,body,propose=False):
    if isinstance(body, MutateCityTripV2):
        return mutate_v2(session, identity, body, propose)
    single_day(body,getattr(body.action,'draft',None))
    data=body.model_dump(mode='json');scope=('propose:' if propose else 'update:')+str(identity)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        guard_single_day(db,owned(db,session,identity))
        cached=receipt(db,session,body.request_id,scope,data)
        if cached is not None:return cached
        row=owned(db,session,identity);check_version(row,body.expected_version)
        items=facts.records(db)
        try: prepared=prepare(row,body.action,items)
        except ValidationError as e: raise HTTPException(422,detail='修改后的行程不合法：'+e.errors()[0]['msg']) from None
        if propose:
            pid=str(uuid4());expiry=stamp(now()+timedelta(minutes=15))
            db.execute('INSERT INTO city_proposals VALUES (?,?,?,?,?,?,?,0)',
                       (pid,session.id,str(identity),row['version'],encoded(data['action']),catalog_revision(items),expiry))
            previous=read_draft(row['draft_json'])
            updated={s.stop_id:s for s in prepared[0].stops}
            impacts=[]
            for stop in previous.stops:
                if stop.locked and stop.progress=='pending' and (updated.get(stop.stop_id)!=stop or prepared[2] in TERMINAL):
                    place=items.get(('place',stop.place_id))
                    impacts.append('影响未完成的必选活动：'+(place.name if place else stop.stop_id))
            labels={'business_date':'游玩日期','starts_at':'开始时间','ends_at':'结束时间','origin_place_id':'出发地点','destination_place_id':'返程终点'}
            for key,label in labels.items():
                before=getattr(previous,key);after=getattr(prepared[0],key)
                if before is not None and before!=after:impacts.append(label+'将发生变化，请核对上方新安排。')
            result={'status':'needs_confirmation','proposal_id':pid,'base_version':row['version'],
                    'expires_at':expiry,'important_change':prepared[-1],
                    'impacts':impacts,
                    'preview':{'draft':prepared[0].model_dump(mode='json'),'anchor':prepared[1],'status':prepared[2]},
                    'notice':'这是状态修改候选，尚未保存为当前安排，也未计算路线。'}
            remember(db,session,body.request_id,identity,scope,data,result)
            return result
        if prepared[-1]:raise HTTPException(409,detail={'code':'CONFIRMATION_REQUIRED','message':'修改影响锁定目标或重要安排，请先生成候选并确认。'})
        return commit(db,session,row,body.request_id,scope,data,body.action.operation,prepared,items)


def mutate_v2(session, identity, body, propose=False):
    data = body.model_dump(mode='json'); scope = ('propose:' if propose else 'update:') + str(identity)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached = receipt(db, session, body.request_id, scope, data)
        if cached is not None: return cached
        row = owned(db, session, identity); check_version(row, body.expected_version)
        items = facts.records(db)
        try:
            prepared = _v2_mutate(row, body.action, items)
        except ValidationError as e:
            raise HTTPException(422, detail='修改后的行程不合法：' + e.errors()[0]['msg']) from None
        if propose:
            pid = str(uuid4()); expiry = stamp(now() + timedelta(minutes=15))
            db.execute('INSERT INTO city_proposals VALUES (?,?,?,?,?,?,?,0)',
                       (pid,session.id,str(identity),row['version'],encoded(data['action']),catalog_revision(items),expiry))
            result = {'status':'needs_confirmation','proposal_id':pid,'base_version':row['version'],
                      'expires_at':expiry,'important_change':prepared[-1],
                      'impacts':['仅修改今天的状态；历史日期冻结，后续日期锁定。'],
                      'preview':{'draft':prepared[0].model_dump(mode='json'),'anchor':prepared[1],'status':prepared[2]},
                      'notice':'这是状态修改候选，尚未保存，也未重新计算路线。'}
            remember(db,session,body.request_id,identity,scope,data,result)
            return result
        if prepared[-1]:
            raise HTTPException(409, detail={'code':'CONFIRMATION_REQUIRED','message':'修改影响锁定目标或重要安排，请先生成候选并确认。'})
        return commit(db, session, row, body.request_id, scope, data, body.action.operation, prepared, items)


def commit(db,session,row,request_id,scope,data,operation,prepared,items):
    draft,anchor,status,ended,_=prepared
    db.execute('UPDATE city_trip_states SET draft_json=?,anchor_json=?,status=?,ended_at=?,version=version+1,updated_at=? WHERE id=? AND version=?',
               (encoded(draft.model_dump(mode='json')),encoded(anchor) if anchor else None,status,ended,stamp(),row['id'],row['version']))
    result=describe(owned(db,session,row['id']),items)
    event(db,row['id'],request_id,operation,row['version'],result['version'])
    remember(db,session,request_id,row['id'],scope,data,result)
    return result


def confirm(session,identity,body):
    data=body.model_dump(mode='json');scope='confirm:'+str(identity)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row_for_guard = owned(db,session,identity)
        multi = isinstance(read_draft(row_for_guard['draft_json']), CityDraftV2)
        if not multi: guard_single_day(db,row_for_guard)
        cached=receipt(db,session,body.request_id,scope,data)
        if cached is not None:return cached
        row=owned(db,session,identity);check_version(row,body.expected_version)
        candidate=db.execute('SELECT * FROM city_proposals WHERE id=? AND session_id=? AND trip_id=?',
                             (str(body.proposal_id),session.id,str(identity))).fetchone()
        if not candidate or candidate['consumed'] or candidate['expires_at']<=stamp():raise HTTPException(409,detail='候选已过期、清除或已经确认，请重新核对。')
        items=facts.records(db)
        if candidate['trip_version']!=row['version'] or candidate['fact_revision']!=catalog_revision(items):
            raise HTTPException(409,detail='行程或事实版本已变化，候选失效。')
        if json.loads(candidate['action_json']).get('operation') in ('assistant_intent','assistant_v3_intent'):
            raise HTTPException(409,detail='待补充的聊天意愿不是可确认的状态修改，请先生成正式候选。')
        action_data=json.loads(candidate['action_json'])
        if 'draft' in action_data:single_day(read_draft(action_data['draft']))
        model = MutateCityTripV2 if multi else MutateCityTrip
        values = {'request_id':body.request_id,'expected_version':body.expected_version,'action':action_data}
        if multi: values['schema_version'] = 'city-state/2'
        action=model.model_validate(values).action
        try:prepared=_v2_mutate(row,action,items) if multi else prepare(row,action,items)
        except ValidationError as e:raise HTTPException(422,detail=e.errors()[0]['msg']) from None
        result=commit(db,session,row,body.request_id,scope,data,action.operation,prepared,items)
        db.execute('UPDATE city_proposals SET consumed=1 WHERE id=?',(str(body.proposal_id),))
        return result


def export_city(db,session):
    items=facts.records(db);output=[]
    for row in db.execute('SELECT * FROM city_trip_states WHERE session_id=? ORDER BY updated_at DESC',(session.id,)):
        try:owned(db,session,row['id'])
        except HTTPException as exc:
            if exc.status_code==404:continue
            raise
        trip=describe(row,items)
        trip['proposals']=[{'id':p['id'],'action':json.loads(p['action_json']),'expires_at':p['expires_at'],'consumed':bool(p['consumed'])}
                           for p in db.execute('SELECT * FROM city_proposals WHERE trip_id=? AND expires_at>?',(row['id'],stamp()))]
        trip['events']=[{'operation':e['event_type'],'before_version':e['before_version'],'after_version':e['after_version'],'created_at':e['created_at']}
                        for e in db.execute('SELECT * FROM city_trip_events WHERE trip_id=? ORDER BY created_at',(row['id'],))]
        trip['receipts']=[{'request_id':r['request_id'],'scope':r['scope'],'result':json.loads(r['result_json']),'created_at':r['created_at']}
                          for r in db.execute('SELECT * FROM city_receipts WHERE trip_id=? ORDER BY created_at',(row['id'],))]
        from .engine.storage import export_engine
        trip['planning_engine']=export_engine(db,row['id'])
        from .basket import snapshot as basket_snapshot
        trip['basket']=basket_snapshot(db,session,row['id'])
        from .exploration_state import read as exploration_snapshot
        trip['exploration']=exploration_snapshot(db,row['id'])
        from .travel_services import read as service_snapshot
        trip['travel_services']=service_snapshot(db,row['id'])
        output.append(trip)
    return output
