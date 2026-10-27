"""Read-only schema dispatch. Never migrate or downgrade a saved snapshot."""
import json
from typing import Annotated
from fastapi import HTTPException
from pydantic import Discriminator, Tag, ValidationError
from .state_schema import (CityDraft, CityDraftV2, CreateCityTrip, CreateCityTripV2, MutateCityTrip,
                           MutateCityTripV2, SaveCityDraftV2)
from .engine.models import (Requirements, MultiDayRequirements, Itinerary, MultiDayItinerary, SolveResult,
                            SolveResultV2, PreviewRequest, PreviewRequestV2, ReplanRequest, ReplanRequestV2,
                            MultiDayConstraints)
from .schema import CityTrip, CityTripV2


def content(value):
    return json.loads(value) if isinstance(value,str) else value.model_dump(mode='json') if hasattr(value,'model_dump') else value


def read(value,legacy,current,tag):
    try:value=content(value)
    except (ValueError,TypeError):raise HTTPException(422,detail={'code':'SCHEMA_INVALID','message':'保存的 JSON 不合法。'}) from None
    version=value.get('schema_version') if isinstance(value,dict) else None
    legacy_tag=legacy.model_fields['schema_version'].default if 'schema_version' in legacy.model_fields else None
    if version not in (None,tag,legacy_tag):
        raise HTTPException(422,detail={'code':'SCHEMA_UNSUPPORTED','message':'不支持的 schema：'+str(version)})
    model=current if version==tag else legacy
    try:return model.model_validate(value)
    except ValidationError as e:raise HTTPException(422,detail={'code':'SCHEMA_INVALID','message':e.errors()[0]['msg']}) from None


def read_draft(value):return read(value,CityDraft,CityDraftV2,'city-draft/2')
def read_requirements(value):return read(value,Requirements,MultiDayRequirements,'city-engine/2')
def read_itinerary(value):return read(value,Itinerary,MultiDayItinerary,'city-itinerary/2')
def read_result(value):return read(value,SolveResult,SolveResultV2,'city-engine-result/2')
def read_trip(value):return read(value,CityTrip,CityTripV2,'city-trip/2')


def read_constraints(value):
    from .exploration import Constraints
    if value is None or value=='null':return None
    return read(value,Constraints,MultiDayConstraints,'city-constraints/2')


def single_day(*values):
    if any(isinstance(v,(CityDraftV2,MultiDayRequirements,MultiDayItinerary,SolveResultV2,MultiDayConstraints,MutateCityTripV2)) for v in values):
        raise HTTPException(503,detail={'status':'unavailable','code':'MULTI_DAY_UNAVAILABLE',
            'message':'多天行程写入和此工具尚未接通，已保留完整行程，请等待多天流程开放。'})


def guard_single_day(db,row):
    draft=read_draft(row['draft_json']);single_day(draft)
    saved=db.execute('SELECT requirements_json,itinerary_json FROM city_engine_routes WHERE trip_id=? ORDER BY route_version DESC LIMIT 1',(row['id'],)).fetchone()
    if saved:single_day(read_requirements(saved['requirements_json']),read_itinerary(saved['itinerary_json']))
    workspace=db.execute('SELECT constraints_json FROM city_exploration_states WHERE trip_id=?',(row['id'],)).fetchone()
    if workspace:single_day(read_constraints(workspace['constraints_json']))
    return draft


def engine_schema(value):
    req=value.get('requirements',{}) if isinstance(value,dict) else getattr(value,'requirements',None)
    return req.get('schema_version','city-engine/1') if isinstance(req,dict) else getattr(req,'schema_version','city-engine/1')


def draft_schema(value):
    action=value.get('action',value) if isinstance(value,dict) else getattr(value,'action',value)
    draft=action.get('draft',{}) if isinstance(action,dict) else getattr(action,'draft',{})
    return draft.get('schema_version','legacy') if isinstance(draft,dict) else 'city-draft/2' if isinstance(draft,CityDraftV2) else 'legacy'


def mutation_schema(value):
    version=value.get('schema_version') if isinstance(value,dict) else getattr(value,'schema_version',None)
    return version if version is not None else draft_schema(value)


PreviewBody=Annotated[Annotated[PreviewRequest,Tag('city-engine/1')]|Annotated[PreviewRequestV2,Tag('city-engine/2')],Discriminator(engine_schema)]
ReplanBody=Annotated[Annotated[ReplanRequest,Tag('city-engine/1')]|Annotated[ReplanRequestV2,Tag('city-engine/2')],Discriminator(engine_schema)]
CreateBody=Annotated[Annotated[CreateCityTrip,Tag('legacy')]|Annotated[CreateCityTripV2,Tag('city-draft/2')],Discriminator(draft_schema)]
MutationBody=Annotated[Annotated[MutateCityTrip,Tag('legacy')]|Annotated[SaveCityDraftV2,Tag('city-draft/2')]|
                       Annotated[MutateCityTripV2,Tag('city-state/2')],Discriminator(mutation_schema)]
