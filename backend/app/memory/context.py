"""Bounded, source-tagged context. Summaries cannot populate authoritative trip fields."""
import json
import re
from fastapi import HTTPException
from ..db import connect, scenic_get
from ..schedules import facts_for
from .session import recheck
from .storage import now, stamp


def redact(text):
    text=re.sub(r'(?<!\d)\d{11,18}[Xx]?(?!\d)','[已隐藏号码]',text)
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[已隐藏邮箱]',text)
    text=re.sub(r'-?\d{1,3}\.\d{4,}','[已隐藏精确坐标]',text)
    text=re.sub(r'[^。！？\n]*(?:身份证|我家住|家庭住址|银行卡|病史|疾病|心脏病|过敏|怀孕|残疾|诊断|密码|口令|api[_ -]?key|sk-[\w-]+|GPS|经纬度|东经|北纬)[^。！？\n]*','[已隐藏非必要敏感内容]',text,flags=re.I)
    return text[:2000]


def trip_dict(row):
    return {'id':row['id'],'status':row['status'],'interview':json.loads(row['interview_json']),
            'constraints':json.loads(row['constraints_json']) if row['constraints_json'] else None,
            'excluded_ids':json.loads(row['excluded_ids']),'completed_ids':json.loads(row['completed_ids']),
            'route_id':row['route_id'],'route_version':row['route_version'],'version':row['version'],
            'updated_at':row['updated_at']}


def owned(db,session,trip_id):
    recheck(db,session)
    row=db.execute('SELECT * FROM trip_states WHERE id=? AND session_id=?',(str(trip_id),session.id)).fetchone()
    if not row:
        raise HTTPException(404,detail='找不到属于本会话的行程。')
    if row['ended_at']:
        from datetime import datetime,timedelta
        if datetime.fromisoformat(row['ended_at'])+timedelta(hours=72)<=now():
            raise HTTPException(404,detail='行程恢复期限已过。')
    return row


def assemble(session,trip_id):
    with connect() as db:
        s=recheck(db,session);t=trip_dict(owned(db,session,trip_id))
        scope='chat:'+str(trip_id)
        rows=db.execute('SELECT id,role,content_redacted,sequence FROM conversation_messages WHERE session_id=? AND expires_at>? AND turn_id IN (SELECT request_id FROM m7_receipts WHERE session_id=? AND scope=?) ORDER BY sequence DESC LIMIT 8',
                        (session.id,stamp(),session.id,scope)).fetchall() if s['history_enabled'] else []
        sources=json.loads(s['summary_sources'])
        valid=bool(s['history_enabled']) and all(db.execute('SELECT 1 FROM conversation_messages WHERE id=? AND session_id=? AND expires_at>? AND turn_id IN (SELECT request_id FROM m7_receipts WHERE session_id=? AND scope=?)',
                        (i,session.id,stamp(),session.id,scope)).fetchone() for i in sources)
        summary=s['summary'][:600] if valid else ''
    d=t['interview']['draft'];sid=(t['constraints'] or {}).get('scenic_id') or d['scenic_id']
    scenic=scenic_get(sid) if sid else None
    facts=[]
    if sid and d.get('visit_date'):
        from datetime import date
        facts=[f.model_dump(mode='json') for f in facts_for(sid,date.fromisoformat(d['visit_date']),'reported')]
    # No raw GPS, account/token hashes, arbitrary database rows or full road geometry.
    envelope={'schema_version':1,'authority':'trip_state_and_fresh_scenic_facts_only',
            'trip':t,'preferences':{},'recent_messages':[{'id':r['id'],'role':r['role'],'text':r['content_redacted'][:500]} for r in reversed(rows)],
            'summary':{'text':summary,'source_message_ids':sources if valid else [],'authoritative':False},
            'scenic_facts':{'scenic_id':sid,'checked_at':scenic.get('checked_at') if scenic else None,
                            'source_url':scenic.get('source_url') if scenic else None,'schedules':facts[:40]},
            'limits':{'recent_messages':8,'summary_characters':600,'location_included':False,'max_characters':12000}}
    while len(json.dumps(envelope,ensure_ascii=False))>12000:
        if envelope['scenic_facts']['schedules']:envelope['scenic_facts']['schedules'].pop()
        elif envelope['recent_messages']:envelope['recent_messages'].pop(0)
        elif envelope['summary']['text']:envelope['summary']={'text':'','source_message_ids':[],'authoritative':False}
        else:raise HTTPException(422,detail='结构化状态超出上下文预算，请缩小行程。')
    return envelope
