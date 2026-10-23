"""Atomic state machine, optimistic concurrency and idempotent route commits."""
import json
import uuid
from fastapi import HTTPException
from ..db import connect, network_get
from ..models import PlanRequest
from ..planner import plan
from .context import owned, trip_dict
from .schema import Candidate, Interview
from .session import recheck
from .storage import digest, encoded, stamp

TRANSITIONS={'DRAFT':{'CANCELLED'},'PLANNED':{'ACTIVE','CANCELLED'},'ACTIVE':{'PAUSED','COMPLETED','CANCELLED'},
             'PAUSED':{'ACTIVE','CANCELLED'},'COMPLETED':{'ARCHIVED'},'CANCELLED':{'ARCHIVED'},'ARCHIVED':set()}


def receipt(db,session,request_id,scope,body):
    recheck(db,session)
    row=db.execute('SELECT * FROM m7_receipts WHERE session_id=? AND request_id=?',(session.id,str(request_id))).fetchone()
    if not row:return None
    if row['scope']!=scope or row['body_hash']!=digest(encoded(body)):
        raise HTTPException(409,detail={'code':'IDEMPOTENCY_CONFLICT','message':'同一请求编号不能用于不同操作或内容。'})
    result=json.loads(row['result_json'])
    # A cached response is not an authorization or TTL bypass.
    trip_id=result.get('trip',{}).get('id') or (result.get('id') if scope=='trip:create' else scope.rsplit(':',1)[-1])
    if trip_id:owned(db,session,trip_id)
    return result


def remember(db,session,request_id,scope,body,result):
    db.execute('INSERT INTO m7_receipts VALUES (?,?,?,?,?,?)',
               (session.id,str(request_id),scope,digest(encoded(body)),encoded(result),stamp()))


def version(row,expected):
    if row['version']!=expected:
        raise HTTPException(409,detail={'code':'VERSION_CONFLICT','message':'行程已被另一请求修改，请重新加载后核对。',
                                       'current_version':row['version'],'current_route_version':row['route_version']})


def event(db,trip_id,request_id,kind,before,after):
    db.execute('INSERT INTO trip_events VALUES (?,?,?,?,?,?,?,?)',
               (str(uuid.uuid4()),str(trip_id),str(request_id),kind,'user',before,after,stamp()))


def poi_ids(sid):
    net=network_get(sid)
    if not net:raise HTTPException(422,detail='该景区没有可用路网。')
    return {f['properties']['id'] for f in net['features'] if f['geometry']['type']=='Point' and f['properties']['kind']=='poi'}


def validate_interview(interview):
    d=interview.draft
    if d.scenic_id:
        known=poi_ids(d.scenic_id)
        if set(d.must_visit)-known:raise HTTPException(422,detail='必去点不属于当前景区。')
    elif d.must_visit or d.must_attend:raise HTTPException(422,detail='请先选择景区。')
    if interview.answered.basics and not (d.scenic_id and d.visit_date and d.visit_time and d.available_minutes):
        raise HTTPException(422,detail='基本安排尚未完整。')


def create(session,body):
    data=body.model_dump(mode='json');scope='trip:create'
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached=receipt(db,session,body.request_id,scope,data)
        if cached:return cached
        validate_interview(body.interview)
        sid=str(uuid.uuid4())
        db.execute('INSERT INTO trip_states(id,session_id,status,interview_json,updated_at) VALUES (?,?,?,?,?)',
                   (sid,session.id,'DRAFT',encoded(data['interview']),stamp()))
        db.execute('UPDATE conversation_sessions SET active_trip_id=? WHERE id=?',(sid,session.id))
        result=trip_dict(owned(db,session,sid))
        event(db,sid,body.request_id,'created',0,1)
        remember(db,session,body.request_id,scope,data,result)
        return result


def update(session,trip_id,body):
    data=body.model_dump(mode='json');scope='trip:update:'+str(trip_id)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached=receipt(db,session,body.request_id,scope,data)
        if cached:return cached
        row=owned(db,session,trip_id);version(row,body.expected_version)
        terminal=row['status'] in ('COMPLETED','ARCHIVED','CANCELLED')
        if body.operation=='save_draft':
            if terminal:raise HTTPException(422,detail='已结束行程不能修改，请新建行程。')
            validate_interview(body.interview)
            old=json.loads(row['interview_json'])['draft'];new=body.interview.draft
            if row['constraints_json'] and old['scenic_id']!=new.scenic_id:
                raise HTTPException(422,detail='更换景区请新建行程，原行程和路线会保留。')
            db.execute('UPDATE trip_states SET interview_json=? WHERE id=?',(encoded(data['interview']),str(trip_id)))
        elif body.operation=='transition':
            if body.status not in TRANSITIONS[row['status']]:
                raise HTTPException(422,detail='不允许该行程状态转换。')
            db.execute('UPDATE trip_states SET status=?,ended_at=? WHERE id=?',
                       (body.status,stamp() if body.status in ('COMPLETED','CANCELLED','ARCHIVED') else None,str(trip_id)))
        else:
            if row['status']!='ACTIVE':raise HTTPException(422,detail='仅游览中的行程可以确认已完成点位。')
            if not row['route_id']:raise HTTPException(422,detail='当前没有已提交路线。')
            p=json.loads(db.execute('SELECT result FROM plans WHERE id=?',(row['route_id'],)).fetchone()['result'])
            allowed={s['id'] for s in p['stops'][1:-1]}
            if body.poi_id not in allowed:raise HTTPException(422,detail='完成点必须属于当前路线。')
            done=set(json.loads(row['completed_ids']));done.add(body.poi_id)
            db.execute('UPDATE trip_states SET completed_ids=? WHERE id=?',(encoded(sorted(done)),str(trip_id)))
        db.execute('UPDATE trip_states SET version=version+1,updated_at=? WHERE id=?',(stamp(),str(trip_id)))
        result=trip_dict(owned(db,session,trip_id));event(db,trip_id,body.request_id,body.operation,row['version'],result['version'])
        remember(db,session,body.request_id,scope,data,result)
        return result


def interview_for(req):
    data=req.model_dump(mode='json')
    return {'draft':{'scenic_id':req.scenic_id,'visit_date':req.visit_start.date().isoformat(),
                     'visit_time':req.visit_start.strftime('%H:%M'),'available_minutes':req.available_minutes,
                     'interests':req.interests,'must_visit':req.must_visit,'must_attend':req.must_attend,'walking':req.walking,'mode':req.mode},
            'answered':{'basics':True,'interests':True,'pace':True}}


def prepare_constraints(req,excluded):
    if req.dataset!='reported' or req.crowd_overrides:
        raise HTTPException(422,detail='个人行程只接受非模拟数据，不允许客流覆盖。')
    if req.visit_start is None:raise HTTPException(422,detail='行程必须有明确日期、时刻和时区。')
    if set(req.must_visit)&set(excluded):raise HTTPException(422,detail='必去点和排除点不能冲突。')
    return PlanRequest.model_validate(req.model_dump()|{'excluded_ids':excluded})


def compute_and_commit(session,trip_id,request_id,expected,data,scope,constraints,excluded=None,proposal_id=None):
    # Slow solver runs outside the write transaction. Recheck authorization + CAS afterwards.
    with connect() as db:
        cached=receipt(db,session,request_id,scope,data)
        if cached:return cached
        row=owned(db,session,trip_id);version(row,expected)
        if row['status'] not in ('DRAFT','PLANNED','ACTIVE','PAUSED'):
            raise HTTPException(422,detail='该行程已经结束，请新建行程。')
        if json.loads(row['completed_ids']):
            raise HTTPException(422,detail='已有完成进度；当前框架尚未接入新鲜起点，不能从旧入口重算。原路线和进度已保留。')
        old=json.loads(row['constraints_json']) if row['constraints_json'] else None
        if old and old['scenic_id']!=constraints.scenic_id:raise HTTPException(422,detail='跨景区需新建行程。')
        excluded=excluded if excluded is not None else json.loads(row['excluded_ids'])
        if not set(excluded)<=poi_ids(constraints.scenic_id):raise HTTPException(422,detail='排除点不属于当前景区。')
    req=prepare_constraints(constraints,excluded)
    result=plan(req)  # Already performs independent hard-constraint validation.
    result.update(id=uuid.uuid4().hex,created_at=stamp(),trip_id=str(trip_id))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached=receipt(db,session,request_id,scope,data)
        if cached:return cached
        row=owned(db,session,trip_id);version(row,expected)
        route_version=row['route_version']+1
        result.update(route_version=route_version,trip_version=row['version']+1)
        # The route, confirmed constraints, trip version, event and idempotency receipt share one commit.
        db.execute('INSERT INTO plans VALUES (?,?,?)',(result['id'],result['created_at'],encoded(result)))
        db.execute('INSERT INTO m7_plan_owners VALUES (?,?,?)',(result['id'],session.id,str(trip_id)))
        db.execute('UPDATE trip_states SET status=?,interview_json=?,constraints_json=?,excluded_ids=?,route_id=?,route_version=?,version=version+1,updated_at=? WHERE id=? AND version=?',
                   ('PLANNED' if row['status']=='DRAFT' else row['status'],encoded(interview_for(req)),encoded(req.model_dump(mode='json')),
                    encoded(excluded),result['id'],route_version,stamp(),str(trip_id),expected))
        if proposal_id:db.execute('UPDATE m7_proposals SET consumed=1 WHERE id=?',(proposal_id,))
        event(db,trip_id,request_id,'route_committed',expected,expected+1)
        output={'trip':trip_dict(owned(db,session,trip_id)),'plan':result,'status':'updated'}
        remember(db,session,request_id,scope,data,output)
        return output


def replan(session,trip_id,body):
    return compute_and_commit(session,trip_id,body.request_id,body.expected_version,body.model_dump(mode='json'),
                              'trip:replan:'+str(trip_id),body.constraints)
