"""Propose -> validate -> explicitly confirm. No model gets a database writer."""
import json
import re
import uuid
from datetime import datetime, timedelta
from fastapi import HTTPException
from ..db import connect, scenic_get
from ..models import PlanRequest
from . import trips
from .context import assemble, owned, redact, trip_dict
from .schema import Candidate, Interview
from .session import recheck
from .storage import encoded, now, stamp


def append_turn(db,session,request_id,text,reply,channel,trip_id):
    row=recheck(db,session)
    if not row['history_enabled']:return
    seq=db.execute('SELECT COALESCE(MAX(sequence),0) FROM conversation_messages WHERE session_id=?',(session.id,)).fetchone()[0]
    for i,(role,content) in enumerate((('user',text),('assistant',reply)),1):
        db.execute('INSERT INTO conversation_messages VALUES (?,?,?,?,?,?,?,?,?)',
                   (str(uuid.uuid4()),session.id,role,redact(content),str(request_id),seq+i,channel,stamp(),row['expires_at']))
    rows=db.execute('SELECT * FROM conversation_messages WHERE session_id=? AND (turn_id=? OR turn_id IN (SELECT request_id FROM m7_receipts WHERE session_id=? AND scope=?)) ORDER BY sequence DESC LIMIT 24',
                    (session.id,str(request_id),session.id,'chat:'+str(trip_id))).fetchall()
    # Extractive, checkable projection: references only actual redacted messages, never invents business facts.
    older=list(reversed(rows[8:]))
    summary='\n'.join(f"{r['role']}: {r['content_redacted'][:65]}" for r in older)[:600]
    db.execute('UPDATE conversation_sessions SET summary=?,summary_sources=?,summary_version=? WHERE id=?',
               (summary,encoded([r['id'] for r in older]),seq+2,session.id))


def local_candidate(text,trip,route_version,route):
    if re.search(r'(删除|取消|不去|去掉|删).*(第[一二三四五六七八九十\d]+)',text) or re.search(r'第[一二三四五六七八九十\d]+.*(不去|取消|删|去掉)',text):
        match=re.search(r'第([一二三四五六七八九十\d]+)',text)
        if not route or route_version is None or route_version!=trip['route_version']:
            return None,'请先核对当前路线版本，再明确要取消的游览点；这条消息没有改动路线。'
        value=match[1];index=int(value) if value.isdigit() else {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}.get(value,0)
        stops=route['stops'][1:-1]
        if not 1<=index<=len(stops):return None,'当前路线没有这个序号的游览点，请按名称选择。'
        stop=stops[index-1]
        return Candidate(operation='remove_stop',poi_id=stop['id']),f"要取消第{index}个游览点“{stop['name']}”吗？确认后才会重新计算；如它原为必去点，也会取消该项要求。"
    if ('少走' in text or '轻松' in text) and not any(w in text for w in ('为什么','吗','怎么','？','?')):
        patch={'mode':'shortest'} if '少走' in text else {'walking':'relaxed'}
        return Candidate(operation='update_constraints',patch=patch),'已整理出本次行程的调整建议，确认后再应用，不会保存为长期习惯。'
    return None,None


def chat(session,body):
    data=body.model_dump(mode='json');scope='chat:'+str(body.trip_id)
    with connect() as db:
        cached=trips.receipt(db,session,body.request_id,scope,data)
        if cached:return cached
        row=owned(db,session,body.trip_id);trip=trip_dict(row)
        route_row=db.execute('SELECT result FROM plans WHERE id=?',(row['route_id'],)).fetchone() if row['route_id'] else None
        route=json.loads(route_row['result']) if route_row else None
    text=redact(body.text.strip())
    candidate,reply=local_candidate(text,trip,body.route_version,route)
    context=assemble(session,body.trip_id)
    if not reply:
        from .. import language
        d=trip['interview']['draft'];sid=d['scenic_id']
        # Questions use sourced descriptions and committed route reasons; no fictional free-form monitoring.
        if any(w in text for w in ('为什么','理由','怎么安排')) and route:
            reply=route['explanation']+' '+ '；'.join(r for s in route['stops'][1:-1] for r in s['reasons'])
        elif any(w in text for w in ('演出','节目','美食','吃','景区介绍')) and ('?' in text or '？' in text or any(w in text for w in ('什么','介绍','哪些','哪里'))):
            scenic=scenic_get(sid) if sid else None
            if not scenic:reply='先选择景区，我才能查询对应资料；当前行程未改变。'
            elif any(w in text for w in ('几点','时间','开演')):
                facts=context['scenic_facts']['schedules']
                reply='；'.join(f"{f['name']}：{f['starts_at']}，来源：{f['source_url']}" for f in facts) if facts else '目标日期暂无已核验场次，不能根据旧聊天猜测开演时间。'
            elif '吃' in text or '美食' in text:
                reply='；'.join(f"{f['name']}：{f['description']}（资料来源：{f['source_url']}）" for f in scenic['foods']) or '暂无可核实的餐饮信息。'
            else:
                reply=scenic['intro']+f"（资料核查日期：{scenic['checked_at']}；来源：{scenic['source_url']}。当日安排请再核对公告。）"
        elif language.available() and sid:
            try:
                parsed=language.interpret_with_context(language.InterpretRequest(text=text,selected_site_id=sid),context)
                patch={k:v for k,v in parsed['patch'].items() if k!='hour'}
                if parsed['unresolved_names']:
                    reply='以下地点还没匹配到：'+'、'.join(parsed['unresolved_names'])+'。请先核对，不会自动改动行程。'
                elif patch:
                    candidate=Candidate(operation='update_constraints',patch=patch)
                    reply='已理解你提出的调整，请核对候选内容并确认；尚未修改行程。'
                else:reply='这条消息没有明确的行程修改。可以继续询问已有景区资料，或用选项补充需求。'
            except (HTTPException,ValueError):
                reply='自然语言服务暂不可用；已保留当前行程，可以继续用选项回答或手动修改。'
        elif not reply:
            reply='我保留着当前行程。可以查询已有景区资料、解释路线，或用选项修改时间、兴趣和必去点；没有核验的信息我会明确说明。'
    if candidate:
        # Validate target and field values even before offering a confirm button.
        validate_candidate(trip,candidate)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached=trips.receipt(db,session,body.request_id,scope,data)
        if cached:return cached
        row=owned(db,session,body.trip_id);trips.version(row,trip['version'])
        proposal_id=str(uuid.uuid4()) if candidate else None
        if candidate:
            db.execute('INSERT INTO m7_proposals(id,session_id,trip_id,trip_version,route_version,candidate_json,source_turn_id,expires_at) VALUES (?,?,?,?,?,?,?,?)',
                       (proposal_id,session.id,str(body.trip_id),row['version'],row['route_version'],encoded(candidate.model_dump()),str(body.request_id),stamp(now()+timedelta(minutes=15))))
        append_turn(db,session,body.request_id,text,reply,body.channel,body.trip_id)
        result={'reply':reply,'status':'needs_confirmation' if candidate else 'answered',
                'proposal_id':proposal_id,'candidate':candidate.model_dump() if candidate else None,
                'trip_version':row['version'],'route_version':row['route_version'],
                'memory_written':bool(recheck(db,session)['history_enabled']),'long_term_written':False}
        trips.remember(db,session,body.request_id,scope,data,result)
        return result


def draft_patch(interview,patch):
    obj=interview.model_dump();d=obj['draft'];answered=obj['answered']
    for key,value in patch.items():
        if key=='visit_start':
            from ..schedules import TZ
            time=datetime.fromisoformat(value)
            if time.tzinfo is None or time.utcoffset() is None:raise ValueError('时区缺失')
            time=time.astimezone(TZ)
            if d['visit_date']!=time.date().isoformat():d['must_attend']=[];answered['interests']=False
            d['visit_date']=time.date().isoformat();d['visit_time']=time.strftime('%H:%M')
        else:d[key]=value
    if 'interests' in patch or 'must_visit' in patch:answered['interests']=True
    if 'mode' in patch or 'walking' in patch:
        answered['pace']=True;d['mode']=d['mode'] or 'preference';d['walking']=d['walking'] or 'normal'
    answered['basics']=bool(d['scenic_id'] and d['visit_date'] and d['visit_time'] and d['available_minutes'])
    result=Interview.model_validate(obj);trips.validate_interview(result)
    return result


def validate_candidate(trip,candidate):
    d=trip['interview']['draft'];sid=d['scenic_id']
    if candidate.operation=='remove_stop':
        if candidate.poi_id not in trips.poi_ids(sid):raise HTTPException(422,detail='候选点不属于本景区。')
    else:
        try:
            draft_patch(Interview.model_validate(trip['interview']),candidate.patch)
            if trip['constraints']:
                PlanRequest.model_validate(trip['constraints']|candidate.patch)
        except ValueError:
            raise HTTPException(422,detail='候选修改没有通过字段或时间校验。') from None


def confirm(session,trip_id,body):
    data=body.model_dump(mode='json');scope='trip:confirm:'+str(trip_id)
    with connect() as db:
        cached=trips.receipt(db,session,body.request_id,scope,data)
        if cached:return cached
        row=owned(db,session,trip_id);trips.version(row,body.expected_version)
        proposal=db.execute('SELECT * FROM m7_proposals WHERE id=? AND session_id=? AND trip_id=?',
                            (str(body.proposal_id),session.id,str(trip_id))).fetchone()
        if not proposal or proposal['consumed'] or proposal['expires_at']<=stamp():
            raise HTTPException(422,detail='候选修改不存在、已使用或已过期，请重新提出。')
        if proposal['trip_version']!=row['version'] or proposal['route_version']!=row['route_version']:
            raise HTTPException(409,detail={'code':'STALE_PROPOSAL','message':'候选修改基于旧路线，请按当前路线重新核对。'})
        candidate=Candidate.model_validate_json(proposal['candidate_json']);trip=trip_dict(row)
        validate_candidate(trip,candidate)
        if row['status'] in ('COMPLETED','CANCELLED','ARCHIVED'):raise HTTPException(422,detail='已结束行程不能接受候选修改。')
        if not trip['constraints']:
            if candidate.operation!='update_constraints':raise HTTPException(422,detail='尚未生成路线。')
            interview=draft_patch(Interview.model_validate(trip['interview']),candidate.patch)
        else:interview=None
    if interview is not None:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cached=trips.receipt(db,session,body.request_id,scope,data)
            if cached:return cached
            row=owned(db,session,trip_id);trips.version(row,body.expected_version)
            db.execute('UPDATE trip_states SET interview_json=?,version=version+1,updated_at=? WHERE id=?',
                       (encoded(interview.model_dump()),stamp(),str(trip_id)))
            db.execute('UPDATE m7_proposals SET consumed=1 WHERE id=?',(str(body.proposal_id),))
            result={'trip':trip_dict(owned(db,session,trip_id)),'plan':None,'status':'draft_updated'}
            trips.event(db,trip_id,body.request_id,'proposal_confirmed',row['version'],row['version']+1)
            trips.remember(db,session,body.request_id,scope,data,result)
            return result
    req=trip['constraints'].copy();excluded=trip['excluded_ids'][:]
    if candidate.operation=='remove_stop':
        poi=candidate.poi_id
        if poi in trip['completed_ids']:raise HTTPException(422,detail='已完成事实不能通过取消站点清除。')
        req['must_visit']=[p for p in req['must_visit'] if p!=poi]
        from ..schedules import facts_for
        day=datetime.fromisoformat(req['visit_start']).date()
        removed={f.id for f in facts_for(req['scenic_id'],day,'reported') if f.node_id==poi}
        req['must_attend']=[s for s in req['must_attend'] if s not in removed]
        excluded=sorted(set(excluded)|{poi});req['excluded_ids']=excluded
    else:req.update(candidate.patch)
    return trips.compute_and_commit(session,trip_id,body.request_id,body.expected_version,data,scope,
                                    PlanRequest.model_validate(req),excluded,str(body.proposal_id))
