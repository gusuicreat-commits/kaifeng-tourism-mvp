"""Additive city assistant endpoints; existing scenic clients keep their contracts."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query

from ...db import connect
from ...memory.session import Session, current, limit, recheck
from .. import facts, state
from . import context, service, tools
from .models import CityChat, ConfirmAssistant, DiscardAssistant, Purpose, ActivateCity

router = APIRouter(prefix='/api/city/v1',tags=['City assistant V2'])


@router.get('/assistant/current-context')
def current_context(purpose:Purpose='trip',s:Session=Depends(current)):
    snapshot = context.load(s,purpose=purpose)
    if snapshot is None: return {'trip':None,'notice':'请先创建或选择城市旅行，不会自动混用旧单景区行程。'}
    return {'trip':snapshot.envelope.model_dump(mode='json')}


@router.post('/trips/{trip_id}/activate')
def activate(trip_id:UUID,body:ActivateCity,s:Session=Depends(current)):
    data = body.model_dump(mode='json');scope = 'activate_city:' + str(trip_id)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cached = state.receipt(db,s,body.request_id,scope,data)
        if cached is not None: return cached
        row = state.owned(db,s,trip_id)
        db.execute('UPDATE conversation_sessions SET active_city_trip_id=? WHERE id=?',(str(trip_id),s.id))
        result = state.describe(row,facts.records(db))
        state.remember(db,s,body.request_id,trip_id,scope,data,result)
        return result


@router.get('/trips/{trip_id}/assistant/context')
def get_context(trip_id:UUID,purpose:Purpose='trip',s:Session=Depends(current)):
    return context.load(s,trip_id,purpose).envelope.model_dump(mode='json')


@router.get('/trips/{trip_id}/assistant/messages')
def get_messages(trip_id:UUID,before:int | None=Query(default=None,ge=1),s:Session=Depends(current)):
    """Display history is paged separately from the model's bounded context."""
    from ...memory.storage import stamp
    with connect() as db:
        state.owned(db,s,trip_id)
        if not recheck(db,s)['history_enabled']:return {'messages':[],'before':None}
        rows=db.execute('''SELECT m.id,m.role,m.content_redacted,m.sequence FROM conversation_messages m
            WHERE m.session_id=? AND m.expires_at>? AND (? IS NULL OR m.sequence<?)
            AND EXISTS (SELECT 1 FROM city_receipts r WHERE r.session_id=m.session_id
                AND r.trip_id=? AND r.request_id=m.turn_id AND r.scope IN (?,?))
            ORDER BY m.sequence DESC LIMIT 41''',
            (s.id,stamp(),before,before,str(trip_id),'city_chat:'+str(trip_id),'agent_chat:'+str(trip_id))).fetchall()
        page=rows[:40]
        return {'messages':[{'id':r['id'],'role':r['role'],'text':r['content_redacted']} for r in reversed(page)],
                'before':page[-1]['sequence'] if len(rows)>40 else None}


@router.post('/trips/{trip_id}/assistant/chat')
def chat(trip_id:UUID,body:CityChat,s:Session=Depends(current)):
    limit('city-chat:'+s.id,60)
    return service.chat(s,trip_id,body)


@router.post('/trips/{trip_id}/assistant/confirm')
def confirm(trip_id:UUID,body:ConfirmAssistant,s:Session=Depends(current)):
    limit('city-write:'+s.id,120)
    return tools.confirm(s,trip_id,body)


@router.post('/trips/{trip_id}/assistant/cancel')
def cancel(trip_id:UUID,body:DiscardAssistant,s:Session=Depends(current)):
    limit('city-write:'+s.id,120)
    return tools.discard(s,trip_id,body)
