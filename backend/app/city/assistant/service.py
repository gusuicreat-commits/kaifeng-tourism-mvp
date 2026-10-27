"""City conversation coordinator: read -> typed intention -> preview -> explicit confirm."""
import json
import re
from datetime import datetime, timedelta
from uuid import uuid4, uuid5, UUID

from fastapi import HTTPException

from ...db import connect
from ...memory.context import redact
from ...memory.session import recheck
from ...memory.storage import now, stamp, encoded
from .. import facts, state
from ..engine.models import Requirements
from ..engine.travel import TravelTimeService, travel_seconds
from ..schema import TZ
from . import context, interpret, tools
from .models import Intent, DiscardAssistant, Reference


def _child(request_id, operation):
    return uuid5(UUID(str(request_id)), 'city-assistant:' + operation)


def _merge(snapshot, intent, text):
    active = [p for p in snapshot.pending if p['target_kind'] == 'intent' and p['status'] == 'valid' and p.get('action',{}).get('operation')=='assistant_intent']
    if len(active) != 1 or intent.operation in ('query','cancel','position','skip','restore'): return intent
    previous = Intent.model_validate(active[0]['action']['intent'])
    if previous.operation == 'complete' and intent.operation in ('unknown','complete'):
        match = re.search(r'(\d{1,2})[:点](\d{1,2})?(?:分)?(?:开始|入场|进场)',text)
        if match and snapshot.draft.business_date:
            try:
                start = datetime.combine(snapshot.draft.business_date,datetime.min.time(),TZ).replace(hour=int(match[1]),minute=int(match[2] or 0))
                return previous.model_copy(update={'actual_start':start})
            except ValueError: pass
    if previous.operation not in ('preview','preference') or intent.operation not in ('preview','preference','unknown'): return intent
    fields = intent.model_dump(exclude_none=True,exclude_defaults=True)
    if intent.operation == 'unknown': fields.pop('operation',None)
    fields['execute'] = intent.execute
    # These are unconfirmed intentions, distinct from official preferences.
    return Intent.model_validate(previous.model_dump() | fields)


def _defer(session, trip_id, snapshot, intent, request_id):
    if not snapshot.history_enabled: return None
    identity = str(_child(request_id,'intent'))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = state.owned(db,session,trip_id); state.check_version(row,snapshot.row['version'])
        if not recheck(db,session)['history_enabled']: return None
        if db.execute('SELECT 1 FROM city_proposals WHERE id=? AND trip_id=?',(identity,str(trip_id))).fetchone(): return identity
        for p in snapshot.pending:
            if p['target_kind'] == 'intent' and p['status'] == 'valid':
                db.execute('UPDATE city_proposals SET consumed=1 WHERE id=? AND trip_id=?',(p['candidate_id'],str(trip_id)))
        action = {'operation':'assistant_intent','intent':intent.model_dump(mode='json')}
        db.execute('INSERT OR IGNORE INTO city_proposals VALUES (?,?,?,?,?,?,?,0)',
            (identity,session.id,str(trip_id),row['version'],encoded(action),state.catalog_revision(snapshot.planning.facts),stamp(now()+timedelta(minutes=15))))
    return identity


def _consume_intentions(session, trip_id, snapshot):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = state.owned(db,session,trip_id);state.check_version(row,snapshot.row['version'])
        for p in snapshot.pending:
            if p['target_kind'] == 'intent' and p['status'] == 'valid':
                db.execute('UPDATE city_proposals SET consumed=1 WHERE id=? AND trip_id=?',(p['candidate_id'],str(trip_id)))


def _query(text, snapshot, intent):
    from ..exploration import answer_focus
    focused=answer_focus(text,snapshot)
    if focused:return focused
    env = snapshot.envelope; topic = intent.purpose
    if topic == 'history':
        return {'status':'needs_clarification','reply':'你是要放弃尚未确认的修改，还是恢复某个历史正式版本？恢复请明确版本号，仍需按当前进度重新计算和确认。'}
    if topic in ('shows','dining','reference'):
        kind = 'show_session' if topic == 'shows' or '演出' in text or '那场' in text else 'place'
        references = interpret.resolve_reference(text,snapshot,kind)
        if kind == 'show_session' and len(references) > 1 and any(w in text for w in ('哪些','什么节目','还有什么')) and not any(w in text for w in ('那场','刚才','这场')):
            values = []
            for ref in references:
                item = snapshot.planning.facts.get(('show_session',ref.id))
                if not item or not facts.planning_visible(item): continue
                if '下午' in text and item.starts_at.astimezone(TZ).hour < 12: continue
                values.append(context.fact_view(item,snapshot.planning.facts,now()))
            return {'status':'answered','reply':'以下是当前行程目标对应的场次资料；有缺项或取消的场次不能当作仍可参加。',
                    'facts':values,'references':[{'kind':'show_session','id':v['id']} for v in values[:6]]}
        if len(references) != 1:
            return {'status':'needs_clarification','reply':'有几个可能的对象，你说的是哪一个？' if references else '你想问哪一场演出或哪家店？',
                    'choices':[r.model_dump() for r in references]}
        ref = references[0]; item = snapshot.planning.facts.get((ref.kind,ref.id))
        if not item or not facts.planning_visible(item):
            return {'status':'needs_data','reply':'没有可公开使用的对应资料，不能用旧聊天代替当前事实。'}
        view = context.fact_view(item,snapshot.planning.facts,now())
        answer_ready = view['ready']; used_facts = [view]
        if kind == 'show_session':
            program = snapshot.planning.facts.get(('program',item.program_id))
            name = redact(program.name) if program and facts.planning_visible(program) else '该场演出'
            if not view['ready']:
                reply = name + ('：资料库记录为取消，' if item.business_status == 'cancelled' else '：') + '当前资料未通过时效或完整性核验，请重新核对；旧路线不会被自动覆盖。'
            else:
                delta = int((item.starts_at-now()).total_seconds())
                reply = f'按当前有效场次资料，{name}于{item.starts_at.astimezone(TZ).strftime("%H:%M")}开始。'
                reply += f'距开演约{(delta+59)//60}分钟；这不是现场开演监测。' if delta > 0 else '公告开始时刻已到或已过，不能据此判断现场是否正在演出。'
        else:
            rules = [r for r in snapshot.planning.facts.values() if isinstance(r,facts.OpenRule)
                     and r.place_id == item.fact_id and r.business_date == now().astimezone(TZ).date()]
            valid = [r for r in rules if snapshot.planning.ready(r,now())]
            answer_ready = bool(answer_ready and valid)
            used_facts.extend(context.fact_view(r,snapshot.planning.facts,now()) for r in rules)
            inside = [r for r in valid if r.opens_at and r.opens_at <= now() < r.closes_at]
            reply = context.label(snapshot.planning.facts,item.fact_id) + '：'
            if not view['ready'] or not valid: reply += '缺少当前有效营业资料，不能判断现在是否营业。'
            elif inside: reply += '按已核验营业规则，当前处于营业时段；实际营业情况仍需向商家确认。'
            else: reply += '按当前有效营业规则，此时不在已记录的营业时段。'
        return {'status':'answered' if answer_ready else 'needs_data','reply':reply,
                'references':[ref.model_dump()], 'facts':used_facts}
    if topic == 'travel':
        if env.position['status'] != 'fresh': return {'status':'needs_data','reply':'你现在在哪？在地图上点一下位置，我再帮你看。'}
        next_stop = env.progress['next']
        if not next_stop or not snapshot.requirements: return {'status':'needs_data','reply':'尚无正式下一站和完整交通要求，不能推测步行时间。'}
        if snapshot.requirements.ends_at <= now(): return {'status':'needs_data','reply':'原行程时间已过，请先核对新的旅行时间。'}
        req = Requirements.model_validate(snapshot.requirements.model_dump() | {'starts_at':now(),'origin_place_id':env.position['place_id']})
        provider = TravelTimeService(snapshot.planning)
        choices = [j for j in snapshot.planning.operations.journeys if j.travel.origin_place_id == req.origin_place_id
                   and j.travel.destination_place_id == next_stop['place_id'] and j.travel.mode in req.modes and provider.usable(j,req)]
        if not choices: return {'status':'needs_data','reply':'没有覆盖当前地点到下一站的有效交通依据；不能用零分钟或旧聊天补齐。'}
        chosen = min(choices,key=travel_seconds)
        return {'status':'answered','reply':f'按已有核验路段的保守估时，到{next_stop["name"]}约需{(travel_seconds(chosen)+59)//60}分钟；这是估计，不能保证实际到达时间。',
                'travel':{'mode':chosen.travel.mode,'upper_seconds':travel_seconds(chosen),'source':chosen.travel.evidence.source,
                          'checked_at':chosen.travel.evidence.checked_at.isoformat(),'basis':chosen.time_basis},
                'references':[{'kind':'place','id':next_stop['place_id']}]}
    if env.progress['ended']:
        return {'status':'answered','reply':'这趟旅行已经结束，保存的安排仅供回看，不作为当前导航；再次游玩请新建行程。'}
    if env.official_plan['status'] == 'not_created':
        return {'status':'needs_data','reply':'这趟旅行还没有正式计算并确认的城市路线，已有草稿不能当作可执行计划。请先补齐要求并生成候选。',
                'create_entry':'/city-trip/'+str(env.identity.trip_id)}
    warning = ' 当前路线需要重新核验，以下仅说明已确认的计划，不代表现在可直接执行。' if env.official_plan['status'] == 'stale' else ''
    if topic == 'next':
        next_stop = env.progress['next']
        if not next_stop: return {'status':'answered','reply':'已记录的活动都已处理，请核对返程安排。'+warning}
        reference = {'kind':'show_session','id':next_stop['session_id']} if next_stop['session_id'] else {'kind':'place','id':next_stop['place_id']}
        return {'status':'answered','reply':f'按第{env.identity.route_version}版已确认计划，下一项是{next_stop["name"]}。'+warning,
                'next_stop':next_stop,'references':[reference]}
    return {'status':'answered','reply':'已确认计划中尚未处理的活动：'+(' → '.join(s['name'] for s in env.progress['remaining']) or '无')+'。'+warning,
            'remaining':env.progress['remaining']}


def _dispatch(session, trip_id, body, snapshot, intent):
    if intent.operation == 'query' or intent.purpose == 'history' and intent.operation != 'restore':
        return _query(body.text,snapshot,intent), []
    if intent.operation == 'cancel':
        active = [p for p in snapshot.pending if p['status'] == 'valid']
        if len(active) != 1:
            return {'status':'needs_clarification' if active else 'answered',
                    'reply':'有多份待确认修改，请明确要放弃哪一份。' if active else '当前没有有效待确认修改，正式行程保持不变。',
                    'choices':[{k:p[k] for k in ('candidate_id','target_kind','base_version')} for p in active]}, []
        p = active[0]
        result = tools.discard(session,trip_id,DiscardAssistant(request_id=_child(body.request_id,'discard'),
                               expected_version=snapshot.row['version'],target_kind=p['target_kind'],candidate_id=p['candidate_id']))
        return result | {'reply':'已放弃这份待确认修改，正式行程和路线版本保持不变。'}, ['discard_candidate']
    tools.require_single_day(snapshot)
    if snapshot.row['status'] in state.TERMINAL:
        return {'status':'needs_data','reply':'这趟旅行已经结束，不能继续修改；请新建旅行。'}, []
    if intent.operation in ('complete','skip','position'):
        if intent.operation in ('complete','skip') and not intent.stop_id:
            return {'status':'needs_clarification','reply':'请明确要处理的当前活动；不能猜测或越过已执行前缀。'}, []
        if intent.operation == 'complete' and intent.actual_start is None:
            identity = _defer(session,trip_id,snapshot,intent,body.request_id)
            return {'status':'needs_clarification','reply':'我理解你在报告当前活动已结束。请补充实际开始时间，不能把计划时间当作实际记录；补齐后仍需你确认。',
                    'pending_intent_id':identity,'required_fields':['actual_start'],'stop_id':intent.stop_id}, []
        if intent.operation == 'position' and not intent.place_id:
            return {'status':'needs_clarification','reply':'请明确你现在所在的地点名称；我不会把你的文字当作实时 GPS。'}, []
        result = tools.propose_state(session,trip_id,snapshot,intent,_child(body.request_id,'state'))
        _consume_intentions(session,trip_id,snapshot)
        return {'status':'needs_confirmation','reply':'已整理为你手动报告的进度或地点候选，请确认后保存；这不是独立现场核验。',
                'confirmation':{'target_kind':'state','candidate_id':result['proposal_id'],'expected_version':result['base_version']},
                'state_preview':result,'source':'user_reported'}, ['propose_state']
    if intent.operation in ('preference','preview','restore'):
        if intent.operation == 'preference' and any(w in body.text for w in ('只保存','只改偏好','不改路线')):
            result = tools.propose_state(session,trip_id,snapshot,intent,_child(body.request_id,'preference'))
            _consume_intentions(session,trip_id,snapshot)
            return {'status':'needs_confirmation','reply':'这是仅针对本次旅行的偏好候选，确认后保存；不会建立长期画像。',
                    'confirmation':{'target_kind':'state','candidate_id':result['proposal_id'],'expected_version':result['base_version']},'state_preview':result}, ['propose_preferences']
        if not intent.execute:
            identity = _defer(session,trip_id,snapshot,intent,body.request_id)
            return {'status':'intent_only','reply':'已整理为本次旅行的待确认意愿，正式安排未变。需要我据此计算剩余行程吗？'+('当前不保留对话，请在计算请求中一并说明要求。' if not snapshot.history_enabled else ''),
                    'pending_intent_id':identity,'unconfirmed_intent':intent.model_dump(mode='json')}, []
        if any(w in body.text for w in ('提前回','提前结束')) and intent.ends_at is None:
            _defer(session,trip_id,snapshot,intent,body.request_id)
            return {'status':'needs_clarification','reply':'请明确希望几点前到达已保存的返程终点；我不会猜测提前多久。','required_fields':['ends_at']}, []
        if '少安排一个' in body.text and intent.remove_stop_id is None:
            return {'status':'needs_clarification','reply':'请明确要取消哪一个未完成的可选活动；锁定安排和已完成记录会保留。'}, []
        # New destinations must be explicitly named, not invented from a generic desire to eat.
        explicit = {(r.kind,r.id) for r in interpret.explicit_references(body.text,snapshot)}
        if (intent.add_place_id and ('place',intent.add_place_id) not in explicit) or (intent.add_food_id and ('food',intent.add_food_id) not in explicit):
            return {'status':'needs_clarification','reply':'请明确新增地点或菜品名称，不能由模型任意选择导航目的地。'}, []
        result = tools.preview(session,trip_id,snapshot,intent,_child(body.request_id,'preview'))
        if result['status'] != 'preview':
            return {'status':result['status'],'reply':'没有生成可确认的新路线，正式行程保持原样；请查看缺项或冲突。','planning':result}, ['preview_trip']
        _consume_intentions(session,trip_id,snapshot)
        return {'status':'needs_confirmation','reply':'已计算候选路线，请核对保留、删除、顺序和时间变化；确认前正式路线不变。',
                'confirmation':{'target_kind':'route','candidate_id':result['candidate_id'],'expected_version':result['base_version']},
                'planning':result,'changes':result['changes']}, ['replan_trip' if snapshot.row['status'] in ('ACTIVE','PAUSED') else 'plan_trip']
    return {'status':'needs_clarification','reply':'可以查询当前行程、演出和商家资料，或明确提出本次旅行的修改；我不会从闲聊推断已完成或已确认安排。'}, []


def chat(session, trip_id, body):
    scope = 'city_chat:' + str(trip_id); data = body.model_dump(mode='json')
    with connect() as db:
        cached = state.receipt(db,session,body.request_id,scope,data)
        if cached is not None:
            row = state.owned(db,session,trip_id)
            if cached.get('trip_version') not in (None,row['version']):
                raise HTTPException(409,detail='该聊天回执对应旧版旅行，请以新请求读取当前状态。')
            return cached
    snapshot = context.load(session,trip_id,interpret.purpose(body.text),redact(body.text))
    if body.expected_version is not None and body.expected_version != snapshot.row['version']:
        raise HTTPException(409,detail={'code':'VERSION_CONFLICT','message':'请读取最新城市状态后再发送这项操作。','current_version':snapshot.row['version']})
    if body.expected_route_version is not None and body.expected_route_version != snapshot.row['route_version']:
        raise HTTPException(409,detail='当前正式路线已变化，请核对最新版本后再继续。')
    intent, basis, size = interpret.parse(redact(body.text),snapshot)
    intent = _merge(snapshot,intent,body.text)
    try:
        result, calls = _dispatch(session,trip_id,body,snapshot,intent)
    except HTTPException as exc:
        if exc.status_code in (401,403,404,409): raise
        result = {'status':'needs_data' if exc.status_code == 422 else 'unavailable',
                  'reply':exc.detail['message'] if isinstance(exc.detail,dict) and str(exc.detail.get('code','')).startswith('MULTI_DAY_') else '当前条件不足或工具暂不可用，正式行程没有被这条消息修改。','issues':exc.detail}
        calls = ['tool_failed']
    except (TimeoutError,RuntimeError):
        result = {'status':'unavailable','reply':'工具暂时失败或超时，正式行程保持不变；这不代表规划数学上不可行。'}
        calls = ['tool_failed']
    references = result.get('references',[])
    result.update(trip_id=str(trip_id),trip_scope='city',trip_version=snapshot.row['version'],route_version=snapshot.row['route_version'],
                  interpretation_basis=basis,memory_written=snapshot.history_enabled,long_term_written=False,
                  context_generated_at=snapshot.envelope.identity.generated_at.isoformat(),
                  metrics={'context_characters':snapshot.envelope.metrics['characters'],'model_injected_characters':size,
                           'context_assembly_ms':snapshot.envelope.metrics['assembly_ms'],'tool_calls':calls})
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached = state.receipt(db,session,body.request_id,scope,data)
        if cached is not None: return cached
        row = state.owned(db,session,trip_id);state.check_version(row,snapshot.row['version'])
        enabled = bool(recheck(db,session)['history_enabled'])
        result['memory_written'] = enabled
        if enabled:
            seq = db.execute('SELECT COALESCE(MAX(sequence),0) FROM conversation_messages WHERE session_id=?',(session.id,)).fetchone()[0]
            for offset,(role,text) in enumerate((('user',body.text),('assistant',result['reply'])),1):
                db.execute('INSERT INTO conversation_messages VALUES (?,?,?,?,?,?,?,?,?)',
                    (str(uuid4()),session.id,role,redact(text),str(body.request_id),seq+offset,body.channel,stamp(),recheck(db,session)['expires_at']))
            saved = result
        else:
            # Idempotency without a shadow copy of text, answers, unconfirmed conversational preferences or facts.
            saved = {'status':'history_not_retained','reply':'当前不保留对话，请读取最新行程或对应候选。',
                     'trip_id':str(trip_id),'trip_scope':'city','trip_version':row['version'],'route_version':row['route_version'],
                     'memory_written':False,'long_term_written':False,'confirmation':result.get('confirmation')}
        state.remember(db,session,body.request_id,trip_id,scope,data,saved)
    return result
