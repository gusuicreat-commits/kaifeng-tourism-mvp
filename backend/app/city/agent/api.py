from uuid import UUID
from fastapi import APIRouter, Depends
from ...memory.session import Session, current, limit
from ...memory.context import redact
from ..assistant import context
from . import service, understand
from .models import StartTask, AdvanceTask, ReferenceSelection

router=APIRouter(prefix='/api/city/v1/trips/{trip_id}/agent',tags=['小汴梁 V3'])


@router.post('/understand')
def parse(trip_id:UUID,body:StartTask,s:Session=Depends(current)):
    limit('v3-parse:'+s.id,30)
    snapshot=context.load(s,trip_id,'modify',redact(body.text),body.page_focus)
    from ..state import check_version
    from fastapi import HTTPException
    check_version(snapshot.row,body.expected_version)
    if snapshot.row['route_version']!=body.expected_route_version:raise HTTPException(409,detail='路线版本变化。')
    intent,meta=understand.parse(redact(body.text),snapshot)
    mode,questions=understand.route(intent,snapshot)
    return {'understanding':intent.model_dump(mode='json'),'mode':mode,'questions':questions,
            'metrics':meta,'formal_state_changed':False,'memory_written':False}


@router.post('/tasks')
def create(trip_id:UUID,body:StartTask,s:Session=Depends(current)):
    limit('v3-task:'+s.id,30);return service.start(s,trip_id,body)


@router.get('/tasks/{task_id}')
def get(trip_id:UUID,task_id:UUID,s:Session=Depends(current)):
    return service.get(s,trip_id,task_id)


@router.post('/tasks/{task_id}/step')
def step(trip_id:UUID,task_id:UUID,body:AdvanceTask,s:Session=Depends(current)):
    limit('v3-step:'+s.id,120);return service.advance(s,trip_id,task_id,body)


@router.post('/tasks/{task_id}/cancel')
def cancel(trip_id:UUID,task_id:UUID,s:Session=Depends(current)):
    return service.cancel(s,trip_id,task_id)


@router.get('/capabilities')
def capabilities(trip_id:UUID,s:Session=Depends(current)):
    from .. import state
    from ...db import connect
    from .reference_routes import capabilities as describe
    with connect() as db:state.owned(db,s,trip_id)
    return describe()


@router.post('/tasks/{task_id}/locations/confirm')
def location_confirm(trip_id:UUID,task_id:UUID,body:ReferenceSelection,s:Session=Depends(current)):
    limit('v3-location:'+s.id,40)
    return service.select_reference_location(s,trip_id,task_id,body)
