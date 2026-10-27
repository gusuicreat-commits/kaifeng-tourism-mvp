"""Read-only catalog plus M7-owned visitor city drafts. No HTTP fact imports."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from ..db import connect
from ..memory.session import Session, current, recheck, limit
from . import facts, state
from .state_schema import ConfirmCityChange
from .versions import CreateBody, MutationBody
from .location_resolution import ResolutionConfirm, ResolutionInput, SampleAssessmentInput

router = APIRouter(prefix='/api/city/v1', tags=['City architecture'])


@router.post('/locations/search')
def location_search(body: ResolutionInput, s: Session = Depends(current)):
    """Return short-lived AMap POI candidates for explicit user disambiguation."""
    from .location_resolution import search
    limit('location-search:' + s.id, 20)
    return search(s.id, body.query)


@router.post('/locations/{resolution_id}/confirm')
def location_confirm(resolution_id: str, body: ResolutionConfirm, s: Session = Depends(current)):
    """Confirm a candidate for this session without promoting it to a city fact."""
    from .location_resolution import confirm
    limit('location-confirm:' + s.id, 40)
    return confirm(s.id, resolution_id, body.candidate_id)


@router.post('/sample-assessments/kaifeng-station-qingming')
def sample_assessment(body: SampleAssessmentInput, s: Session = Depends(current)):
    """Read-only readiness gate for the first real station-to-scenic sample."""
    from .location_resolution import assess_sample
    limit('sample-assessment:' + s.id, 20)
    return assess_sample(body)


@router.get('/trips/{trip_id}/basket')
def basket_get(trip_id:UUID,s:Session=Depends(current)):
    from .basket import read
    return read(s,trip_id)


from .basket import BasketMutation, Kind, ObjectRef


@router.get('/objects/{kind}/{object_id}')
def object_detail(kind:Kind,object_id:str):
    from .basket import resolve
    with connect() as db:return resolve(db,ObjectRef(kind=kind,object_id=object_id))


@router.patch('/trips/{trip_id}/basket')
def basket_change(trip_id:UUID,body:BasketMutation,s:Session=Depends(current)):
    from .basket import mutate
    limit('basket:'+s.id,120)
    return mutate(s,trip_id,body)


@router.get('/capabilities')
def capabilities():
    return {
        'contract_version': 'city-trip/1',
        'stage': 'city_state_and_catalog',
        'city_planning_available': False,
        'planning_tools_available': True,
        'planning_tool_prefix': '/api/city/v1/trips/{trip_id}/engine',
        'assistant_context_available': True,
        'assistant_tool_prefix': '/api/city/v1/trips/{trip_id}/assistant',
        'implemented_primitives': ['typed_city_contracts', 'travel_provider_boundary',
                                   'connection_time_assessment', 'remaining_work_preparation',
                                   'city_fact_catalog', 'city_trip_persistence', 'city_draft_frontend',
                                   'city_constraint_engine', 'remaining_trip_engine', 'confirmed_route_versions'],
        'not_connected': ['production_travel_provider', 'city_assistant_frontend'],
        'legacy': {'planning': '/api/plans', 'state': '/api/v1/trips', 'scope': 'single_scenic'},
        'notice': '独立规划工具已实现；真实城市数据、交通时段依据及前端接入尚未验收。',
    }


@router.get('/facts')
def catalog(kind: str | None = Query(default=None,pattern='^(source|place|entrance|open_rule|program|highlight|facility|show_session|food|vendor|supply)$')):
    return facts.public_records(kind)


@router.post('/trips', status_code=201)
def create(body:CreateBody,s:Session=Depends(current)):
    limit('city-write:'+s.id,120)
    return state.create(s,body)


@router.get('/trips')
def list_trips(s:Session=Depends(current)):
    with connect() as db:
        recheck(db,s);items=facts.records(db);output=[]
        for row in db.execute('SELECT * FROM city_trip_states WHERE session_id=? ORDER BY updated_at DESC LIMIT 50',(s.id,)):
            try:state.owned(db,s,row['id'])
            except HTTPException as exc:
                if exc.status_code==404:continue
                raise
            output.append(state.describe(row,items))
        return {'items':output}


@router.get('/trips/current')
def current_trip(s:Session=Depends(current)):
    with connect() as db:
        row=recheck(db,s)
        if not row['active_city_trip_id']:return {'trip':None}
        try:trip=state.owned(db,s,row['active_city_trip_id'])
        except HTTPException as exc:
            if exc.status_code==404:return {'trip':None}
            raise
        return {'trip':state.describe(trip,facts.records(db))}


@router.get('/trips/{trip_id}')
def get_trip(trip_id:UUID,s:Session=Depends(current)):
    with connect() as db:return state.describe(state.owned(db,s,trip_id),facts.records(db))


@router.patch('/trips/{trip_id}')
def update(trip_id:UUID,body:MutationBody,s:Session=Depends(current)):
    limit('city-write:'+s.id,120)
    return state.mutate(s,trip_id,body)


@router.post('/trips/{trip_id}/changes')
def propose(trip_id:UUID,body:MutationBody,s:Session=Depends(current)):
    limit('city-write:'+s.id,120)
    return state.mutate(s,trip_id,body,propose=True)


@router.post('/trips/{trip_id}/confirm')
def confirm(trip_id:UUID,body:ConfirmCityChange,s:Session=Depends(current)):
    limit('city-write:'+s.id,120)
    return state.confirm(s,trip_id,body)
