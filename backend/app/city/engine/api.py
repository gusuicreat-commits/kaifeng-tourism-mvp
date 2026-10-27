"""New backend-only endpoints; existing cityApi.ts and memoryTypes.ts stay compatible."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from ...memory.session import Session, current, limit
from .models import ConfirmRequest
from . import service
from ..versions import PreviewBody, ReplanBody

router=APIRouter(prefix='/api/city/v1/trips/{trip_id}/engine',tags=['City deterministic planning'])


@router.post('/preview')
def preview(trip_id:UUID,body:PreviewBody,s:Session=Depends(current)):
    limit('engine:'+s.id,20)
    return service.preview(s,trip_id,body)


@router.post('/replan-preview')
def replan(trip_id:UUID,body:ReplanBody,s:Session=Depends(current)):
    limit('engine:'+s.id,20)
    return service.preview(s,trip_id,body,replanning=True)


@router.post('/confirm')
def confirm(trip_id:UUID,body:ConfirmRequest,s:Session=Depends(current)):
    limit('engine:'+s.id,40)
    return service.confirm_itinerary(s,trip_id,body)


@router.get('/current')
def read(trip_id:UUID,s:Session=Depends(current)):
    return service.read_itinerary(s,trip_id)


@router.get('/versions/{route_version}')
def version(trip_id:UUID,route_version:int,s:Session=Depends(current)):
    return service.read_itinerary(s,trip_id,route_version)
