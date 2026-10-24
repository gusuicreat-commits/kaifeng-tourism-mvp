from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from ..schema import Strict
from ..basket import ObjectRef


class Action(Strict):
    kind: Literal['query','search_dining','search_services','modify','preference','cancel','progress','position']
    quote: str = Field(min_length=1, max_length=500)
    target_name: str | None = Field(default=None, max_length=120)


class Constraint(Strict):
    field: Literal['food','preserve_shows','walking_seconds','return_by','meal_at','visit_at','objective','pace']
    strength: Literal['hard','soft','optional','unknown']
    value: str | int | None = None
    quote: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def typed_value(self):
        if self.field == 'walking_seconds' and self.value is not None:
            if type(self.value) is not int or not 1 <= self.value <= 43200:
                raise ValueError('walking_seconds must be an integer in 1..43200')
        if self.field in ('meal_at','return_by','visit_at') and self.value is not None:
            import re
            if not isinstance(self.value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',self.value):
                raise ValueError('clock must be HH:MM')
        if self.field == 'objective' and self.value not in ('balanced','less_walking','less_waiting','lower_cost'):
            raise ValueError('unknown objective')
        if self.field == 'pace' and self.value not in ('relaxed','standard','compact'):
            raise ValueError('unknown pace')
        return self


class Correction(Strict):
    before: str = Field(max_length=200)
    after: str = Field(max_length=200)
    quote: str = Field(min_length=1, max_length=500)


class PlanningField(Strict):
    field: Literal['city','date','start_time','end_time','origin','destination','pace','objective','mode','budget_fen']
    value: str | int
    quote: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def typed_value(self):
        allowed={'pace':('relaxed','standard','compact'),
                 'objective':('balanced','less_walking','less_waiting','lower_cost'),
                 'mode':('walking','driving','transit')}
        if self.field=='budget_fen':
            if type(self.value) is not int or not 0<=self.value<=100000000:raise ValueError('invalid budget')
        elif not isinstance(self.value,str) or not self.value.strip() or len(self.value)>120:
            raise ValueError('planning value must be bounded text')
        if self.field in allowed and self.value not in allowed[self.field]:raise ValueError('unknown preference')
        return self


class MaterialChoice(Strict):
    name: str = Field(min_length=1,max_length=120)
    priority: Literal['must','want','optional']
    status: Literal['pending','standby']='pending'
    quote: str = Field(min_length=1,max_length=500)


class PlanningIntent(Strict):
    operation: Literal['update','continue','weather_replan']='update'
    quote: str = Field(min_length=1,max_length=500)
    fields: tuple[PlanningField,...] = Field(default=(),max_length=12)
    choices: tuple[MaterialChoice,...] = Field(default=(),max_length=12)


class Conversation(Strict):
    reply: str = Field(min_length=1, max_length=1200)


class ReferenceRouteIntent(Strict):
    quote: str = Field(min_length=1,max_length=500)
    origin: str | None = Field(default=None,min_length=1,max_length=80)
    destination: str = Field(min_length=1,max_length=80)
    use_current_position: bool = False
    mode: Literal['walking','driving','transit'] = 'walking'

    @model_validator(mode='after')
    def endpoint_roles(self):
        if bool(self.origin)==self.use_current_position:raise ValueError('explicit origin or visitor position, never both')
        return self


class WeatherIntent(Strict):
    quote: str = Field(min_length=1,max_length=500)


class ServiceField(Strict):
    field: Literal['check_in','check_out','nights','guests','rooms','max_price','stay_budget_fen',
                   'location_text','room_type','window','external_window','dry_wet','breakfast',
                   'free_parking','clean','quiet']
    value: str | int | bool
    quote: str = Field(min_length=1,max_length=500)
    strength: Literal['hard','soft','optional'] = 'hard'


class ServiceIntent(Strict):
    quote: str = Field(min_length=1,max_length=500)
    category: Literal['hotels','parking','merchants'] = 'hotels'
    fields: tuple[ServiceField,...] = Field(default=(),max_length=16)


class ReferenceSelection(Strict):
    request_id: UUID
    expected_step: int = Field(ge=0,le=12)
    role: Literal['origin','destination']
    resolution_id: UUID
    candidate_id: str = Field(min_length=1,max_length=160)


class Understanding(Strict):
    # Also publish mutual exclusion in JSON Schema; a Python-only validator is invisible to the model.
    model_config = {'json_schema_extra':{'allOf':[{
        'if':{'properties':{'conversation':{'type':'object'}},'required':['conversation']},
        'then':{'properties':{'route_query':{'type':'null'},'planning':{'type':'null'},'constraints':{'maxItems':0},
                              'corrections':{'maxItems':0},'unresolved':{'maxItems':0},
                              'actions':{'maxItems':0},'weather_query':{'type':'null'},'service_query':{'type':'null'}}},
    }]}}
    schema_version: Literal['travel-intent/3.1'] = 'travel-intent/3.1'
    actions: tuple[Action, ...] = Field(default=(), max_length=8)
    constraints: tuple[Constraint, ...] = Field(default=(), max_length=16)
    corrections: tuple[Correction, ...] = Field(default=(), max_length=4)
    unresolved: tuple[str, ...] = Field(default=(), max_length=8)
    planning: PlanningIntent | None = None
    conversation: Conversation | None = None
    route_query: ReferenceRouteIntent | None = None
    weather_query: WeatherIntent | None = None
    service_query: ServiceIntent | None = None

    @model_validator(mode='after')
    def read_only_conversation(self):
        if self.conversation and (self.route_query or self.weather_query or self.service_query or self.planning or self.constraints or self.corrections or self.unresolved or self.actions):
            raise ValueError('conversation cannot carry business actions')
        if self.route_query and (self.planning or self.weather_query or self.service_query or self.conversation or self.constraints or self.corrections or self.unresolved or any(a.kind!='query' for a in self.actions)):
            raise ValueError('reference route query is read only')
        if self.weather_query and (self.planning or self.service_query or self.conversation or self.route_query or self.constraints or self.corrections or self.unresolved or any(a.kind!='query' for a in self.actions)):
            raise ValueError('weather query is read only')
        if self.service_query and (self.planning or self.conversation or self.route_query or self.weather_query or self.constraints or self.corrections or self.unresolved or any(a.kind!='search_services' for a in self.actions)):
            raise ValueError('service request cannot carry itinerary changes')
        return self


class StartTask(Strict):
    request_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)
    expected_route_version: int = Field(ge=0)
    page_focus: ObjectRef | None = None


class AdvanceTask(Strict):
    request_id: UUID
    expected_step: int = Field(ge=0, le=12)


class Decision(Strict):
    tool: Literal['search_dining','query_shows','verify_travel','preview_plan','search_locations','query_reference_route','finish','clarify']
    place_id: str | None = Field(default=None, max_length=100)
    relax_optional_food: bool = False


class Budget(Strict):
    max_tools: int = Field(default=4, ge=1, le=4)
    max_model_calls: int = Field(default=6, ge=1, le=6)
    max_seconds: float = Field(default=45, ge=1, le=240)
    max_repairs: int = Field(default=1, ge=0, le=1)
