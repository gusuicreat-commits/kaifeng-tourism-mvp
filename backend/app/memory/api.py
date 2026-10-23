import asyncio
import json
import secrets
import sqlite3
from uuid import UUID,uuid4
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from ..db import connect
from . import coordinator, trips
from .context import assemble, owned, trip_dict
from .schema import Chat, Confirm, ConfirmedAction, CreateTrip, HistorySetting, Replan, UpdateTrip
from .session import COOKIE, Session, cookie, current, describe, limit, recheck, start
from .storage import digest, ledger, maintenance, purge_session, stamp

router=APIRouter(prefix='/api/v1',tags=['M7 memory'])


@router.post('/sessions')
def sessions(request:Request,response:Response):
    return start(request,response)


@router.post('/sessions/rotate')
def rotate(request:Request,response:Response,s:Session=Depends(current)):
    token=secrets.token_urlsafe(32)
    with connect() as db:
        recheck(db,s)
        db.execute('UPDATE conversation_sessions SET guest_token_hash=? WHERE id=?',(digest(token),s.id))
    cookie(response,request,token)
    return {'status':'rotated'}


@router.get('/sessions/current')
def session_info(s:Session=Depends(current)):
    with connect() as db:return describe(recheck(db,s))


@router.post('/trips',status_code=201)
def create_trip(body:CreateTrip,s:Session=Depends(current)):
    return trips.create(s,body)


@router.get('/trips')
def list_trips(s:Session=Depends(current)):
    with connect() as db:
        recheck(db,s)
        ids=[r['id'] for r in db.execute('SELECT id FROM trip_states WHERE session_id=? ORDER BY updated_at DESC LIMIT 50',(s.id,))]
        items=[]
        for tid in ids:
            try:items.append(trip_dict(owned(db,s,tid)))
            except HTTPException as e:
                if e.status_code!=404:raise
        return {'items':items}


@router.get('/trips/{trip_id}')
def get_trip(trip_id:UUID,s:Session=Depends(current)):
    with connect() as db:return trip_dict(owned(db,s,trip_id))


@router.post('/trips/{trip_id}/activate')
def activate(trip_id:UUID,s:Session=Depends(current)):
    with connect() as db:
        row=owned(db,s,trip_id)
        db.execute('UPDATE conversation_sessions SET active_trip_id=? WHERE id=?',(str(trip_id),s.id))
        return trip_dict(row)


@router.patch('/trips/{trip_id}')
def update_trip(trip_id:UUID,body:UpdateTrip,request:Request,s:Session=Depends(current)):
    if request.headers.get('If-Match') and request.headers['If-Match'].strip('"')!=str(body.expected_version):
        raise HTTPException(422,detail='If-Match与expected_version不一致。')
    if request.headers.get('Idempotency-Key') and request.headers['Idempotency-Key']!=str(body.request_id):
        raise HTTPException(422,detail='幂等键与request_id不一致。')
    return trips.update(s,trip_id,body)


@router.post('/trips/{trip_id}/replan')
def replan(trip_id:UUID,body:Replan,s:Session=Depends(current)):
    return trips.replan(s,trip_id,body)


@router.post('/trips/{trip_id}/confirm')
def confirm(trip_id:UUID,body:Confirm,s:Session=Depends(current)):
    return coordinator.confirm(s,trip_id,body)


@router.get('/trips/{trip_id}/context')
def context(trip_id:UUID,s:Session=Depends(current)):
    return assemble(s,trip_id)


@router.post('/chat')
def chat(body:Chat,s:Session=Depends(current)):
    limit('chat:'+s.id,60)
    if body.trip_scope=='city':
        from ..city.assistant.models import CityChat
        from ..city.assistant.service import chat as city_chat
        return city_chat(s,body.trip_id,CityChat(request_id=body.request_id,text=body.text,channel=body.channel,
                                              expected_route_version=body.route_version))
    return coordinator.chat(s,body)


@router.put('/memory/history')
def history(body:HistorySetting,s:Session=Depends(current)):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');recheck(db,s)
        db.execute("UPDATE conversation_sessions SET history_enabled=?,summary='',summary_sources='[]',summary_version=0 WHERE id=?",(int(body.enabled),s.id))
        if not body.enabled:
            db.execute('DELETE FROM conversation_messages WHERE session_id=?',(s.id,))
            db.execute('DELETE FROM m7_proposals WHERE session_id=?',(s.id,))
            db.execute('DELETE FROM city_proposals WHERE session_id=?',(s.id,))
            db.execute('DELETE FROM city_engine_candidates WHERE trip_id IN (SELECT id FROM city_trip_states WHERE session_id=?)',(s.id,))
            db.execute("UPDATE city_receipts SET result_json=? WHERE session_id=? AND scope LIKE 'agent_chat:%'",
                       (json.dumps({'status':'history_cleared','memory_written':False}),s.id))
            db.execute("UPDATE city_receipts SET result_json=? WHERE session_id=? AND scope LIKE 'city_chat:%'",
                       (json.dumps({'status':'history_cleared','reply':'城市对话已清除，正式行程仍保留。','memory_written':False}),s.id))
            db.execute("UPDATE city_receipts SET result_json=? WHERE session_id=? AND (scope LIKE 'engine_preview:%' OR scope LIKE 'engine_replan:%')",
                       (json.dumps({'status':'proposal_cleared','message':'候选已清除，没有执行修改。'}),s.id))
            db.execute("UPDATE city_receipts SET result_json=? WHERE session_id=? AND scope LIKE 'exploration:%'",
                       (json.dumps({'status':'unknown','reply':'规划候选已清除，正式行程保持不变。','planning':None,'issues':[],'options':[]}),s.id))
            db.execute("UPDATE city_receipts SET result_json=? WHERE session_id=? AND scope LIKE 'propose:%'",
                       (json.dumps({'status':'proposal_cleared','message':'候选已清除，没有执行修改。'}),s.id))
            # Receipts may contain old conversation replies; do not leave a derived copy retrievable.
            db.execute("UPDATE m7_receipts SET result_json=? WHERE session_id=? AND scope LIKE 'chat:%'",
                       (json.dumps({'status':'answered','reply':'这段对话记录已经清除，未执行任何修改。','proposal_id':None,'candidate':None,'memory_written':False,'long_term_written':False}),s.id))
    if not body.enabled:
        from ..city.agent.service import purge
        purge(s.id)
        from ..city.location_resolution import STORE
        STORE.purge_session(s.id)
    return {'history_enabled':body.enabled,'long_term_memory_enabled':False}


@router.get('/memory/export')
def export(s:Session=Depends(current)):
    from ..city.state import export_city
    with connect() as db:
        session=recheck(db,s)
        msgs=[{'role':r['role'],'text':r['content_redacted'],'created_at':r['created_at']} for r in
              db.execute('SELECT * FROM conversation_messages WHERE session_id=? AND expires_at>? ORDER BY sequence',(s.id,stamp()))] if session['history_enabled'] else []
        return {'trips':[trip_dict(owned(db,s,r['id'])) for r in db.execute('SELECT id FROM trip_states WHERE session_id=?',(s.id,))
                         if owned(db,s,r['id'])], 'messages':msgs,'preferences':[],
                'city_trips':export_city(db,s),
                'notice':'仅本会话记录；城市行程含主动确认的已知地点，不含原始音频或连续GPS轨迹。'}


def require_login(s):
    # No client-supplied user_id or fictional login. The project has no verified identity provider yet.
    if not s.owner_user_id:raise HTTPException(401,detail='长期偏好和保存历史行程需要完成身份验证；匿名会话不写入长期记忆。')
    raise HTTPException(501,detail='账户适配器尚未接入，本版本不开放长期记忆写入。')


@router.get('/memory/preferences')
def preferences(s:Session=Depends(current)):return require_login(s)


@router.put('/memory/preferences/{key}')
def save_preference(key:str,body:ConfirmedAction,s:Session=Depends(current)):return require_login(s)


@router.delete('/memory/preferences/{key}')
def delete_preference(key:str,s:Session=Depends(current)):return require_login(s)


@router.post('/trips/{trip_id}/save')
def save_trip(trip_id:UUID,body:ConfirmedAction,s:Session=Depends(current)):
    with connect() as db:owned(db,s,trip_id)
    return require_login(s)


@router.post('/privacy/delete-request')
def delete(body:ConfirmedAction,request:Request,response:Response):
    if not body.confirmed:raise HTTPException(422,detail='请明确确认删除本次记录。')
    hashed=digest(request.cookies.get(COOKIE,''))
    with ledger() as journal:
        previous=journal.execute('SELECT * FROM deletions WHERE token_hash=? AND request_id=?',(hashed,str(body.request_id))).fetchone()
    if previous:
        return deletion_response(previous['id'],previous['status'],response)
    s=current(request,response)
    job_id=str(uuid4());requested=stamp()
    # Durable revocation precedes main DB purge. If purge fails, all reads are already denied.
    with ledger() as journal:
        journal.execute('INSERT OR IGNORE INTO deletions(id,session_id,requested_at,status,purged_at,request_id,token_hash) VALUES (?,?,?,?,?,?,?)',
                        (job_id,s.id,requested,'revoked',None,str(body.request_id),s.token_hash))
        existing=journal.execute('SELECT * FROM deletions WHERE session_id=?',(s.id,)).fetchone()
        if existing['request_id']!=str(body.request_id):raise HTTPException(409,detail='该会话已经提交删除。')
        job_id=existing['id']
    from ..city.location_resolution import STORE
    STORE.purge_session(s.id)
    status='online_revoked'
    try:
        maintenance();status='online_purged'
    except sqlite3.Error:
        pass
    return deletion_response(job_id,status,response)


def deletion_response(job_id,status,response):
    response.delete_cookie(COOKIE,path='/api',httponly=True,samesite='strict')
    return {'receipt_id':job_id,'status':status,'online_retrievable':False,
            'backup_status':'restore_guard_recorded; physical_backup_expiry_pending',
            'message':'在线记录已停止读取。备份须按留存周期清理，恢复前必须应用删除台账。'}


@router.get('/privacy/delete-requests/{receipt_id}')
def deletion_receipt(receipt_id:UUID):
    # Opaque receipt contains only non-sensitive deletion status, no owner/session/content.
    with ledger() as journal:
        row=journal.execute('SELECT status,purged_at FROM deletions WHERE id=?',(str(receipt_id),)).fetchone()
        if not row:raise HTTPException(404,detail='删除回执不存在。')
        return {'receipt_id':str(receipt_id),'status':row['status'],'purged_at':row['purged_at'],
                'backup_status':'physical_backup_expiry_pending'}


async def retention_worker():
    while True:
        await asyncio.sleep(60)
        try:await asyncio.to_thread(maintenance)
        except Exception:
            # No sensitive payloads or exception strings in operational logs.
            import logging
            logging.getLogger(__name__).error('M7 retention failed; reads retain authorization/expiry/ledger checks')
