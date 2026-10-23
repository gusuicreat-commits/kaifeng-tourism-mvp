"""Only a server-issued cookie binds identity. Client user/session IDs never authorize."""
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from fastapi import HTTPException, Request, Response
from ..db import connect
from .storage import check_ledger, digest, now, stamp

COOKIE = 'm7_session'
_rates = {}
_lock = threading.Lock()
_salt = secrets.token_hex(32)


def limit(key, maximum):
    tick = int(time.monotonic() // 60)
    with _lock:
        for old in [k for k in _rates if k[1] != tick]:
            del _rates[old]
        if len(_rates) > 4096:
            raise HTTPException(429, detail='请求过于频繁，请稍后重试。')
        pair = (key,tick)
        _rates[pair] = _rates.get(pair,0)+1
        if _rates[pair] > maximum:
            raise HTTPException(429, detail='请求过于频繁，请稍后重试。')


@dataclass(frozen=True)
class Session:
    id: str
    token_hash: str
    owner_user_id: str | None = None


def recheck(db, session):
    check_ledger(db, session.id)
    row = db.execute('SELECT * FROM conversation_sessions WHERE id=? AND guest_token_hash=?',
                     (session.id,session.token_hash)).fetchone()
    if not row or row['expires_at'] <= stamp():
        raise HTTPException(401, detail='会话已过期，请重新开始；不会沿用旧记录。')
    return row


def current(request: Request, response: Response):
    token = request.cookies.get(COOKIE,'')
    if not token or len(token)>200:
        raise HTTPException(401, detail='请先开始会话。')
    hashed = digest(token)
    with connect() as db:
        row = db.execute('SELECT * FROM conversation_sessions WHERE guest_token_hash=?',(hashed,)).fetchone()
        if not row:
            raise HTTPException(401, detail='会话无效或已删除，请重新开始。')
        session = Session(row['id'],hashed,row['owner_user_id'])
        recheck(db,session)
        expiry = stamp(now()+timedelta(hours=24))
        db.execute('UPDATE conversation_sessions SET last_activity_at=?,expires_at=? WHERE id=?',
                   (stamp(),expiry,session.id))
        db.execute('UPDATE conversation_messages SET expires_at=? WHERE session_id=?',(expiry,session.id))
    cookie(response,request,token)
    return session


def cookie(response,request,token):
    # Secure in HTTPS deployments; localhost HTTP is the explicit development exception.
    secure = request.url.scheme == 'https' or os.getenv('M7_COOKIE_SECURE','').lower() == 'true'
    if not secure and request.url.hostname not in ('127.0.0.1','localhost','testserver'):
        raise HTTPException(503, detail='访客记忆需要HTTPS，请配置安全会话。')
    response.set_cookie(COOKIE,token,max_age=86400,httponly=True,secure=secure,samesite='strict',path='/api')


def describe(row):
    return {'session_id':row['id'],'active_trip_id':row['active_trip_id'],
            'active_city_trip_id':row['active_city_trip_id'],
            'expires_at':row['expires_at'],'history_enabled':bool(row['history_enabled']),
            'long_term_memory_enabled':False,'authenticated':False,
            'retention':{'guest_inactive_hours':24,'finished_trip_hours':72,'audit_days':30}}


def start(request,response):
    if request.cookies.get(COOKIE):
        try:
            s=current(request,response)
            with connect() as db:return describe(recheck(db,s))
        except HTTPException as e:
            if e.status_code!=401:raise
    limit(digest(_salt+(request.client.host if request.client else 'local')),int(os.getenv('M7_SESSION_RATE_LIMIT','60')))
    import uuid
    token=secrets.token_urlsafe(32);sid=str(uuid.uuid4());moment=stamp()
    with connect() as db:
        check_ledger(db)
        db.execute('INSERT INTO conversation_sessions(id,guest_token_hash,last_activity_at,expires_at,created_at) VALUES (?,?,?,?,?)',
                   (sid,digest(token),moment,stamp(now()+timedelta(hours=24)),moment))
        row=db.execute('SELECT * FROM conversation_sessions WHERE id=?',(sid,)).fetchone()
    cookie(response,request,token)
    return describe(row)


def ensure(request,response):
    try:return current(request,response)
    except HTTPException as e:
        if e.status_code!=401:raise
    result=start(request,response)
    with connect() as db:
        row=db.execute('SELECT * FROM conversation_sessions WHERE id=?',(result['session_id'],)).fetchone()
        return Session(row['id'],row['guest_token_hash'],row['owner_user_id'])


def authorize_plan(db,plan_id,request,response):
    binding=db.execute('SELECT session_id FROM m7_plan_owners WHERE plan_id=?',(plan_id,)).fetchone()
    if binding:
        s=current(request,response)
        if s.id!=binding['session_id']:raise HTTPException(404,detail='路线不存在或不属于本会话。')
    elif db.execute('SELECT 1 FROM plans WHERE id=?',(plan_id,)).fetchone():
        # Never invent ownership for a pre-M7 record or keep it publicly readable.
        raise HTTPException(410,detail='这条旧路线缺少可验证的属主，原记录已保留，请重新安排行程。')
