from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from ..schema import Strict
from ..basket import ObjectRef


class Action(Strict):
    kind: Literal['query','search_dining','search_services','modify','preference','cancel','progress','position']
    quote: str = Field(min_length=1, max_length=500)
    target_name: str | None = Field(default=None, max_length=120)
    purpose: Literal['trip','next','shows','dining','travel','history','reference'] | None = None
    # search_dining only: the visitor limited the search to where they are now.
    near: bool = False


class StateIntent(Strict):
    operation: Literal['complete','skip','position','cancel','restore']
    quote: str = Field(min_length=1,max_length=500)
    target_name: str | None = Field(default=None,max_length=120)
    start_time: str | None = Field(default=None,pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    start_quote: str | None = Field(default=None,max_length=200)
    restore_version: int | None = Field(default=None,ge=1)


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


class BasketProposalItem(ObjectRef):
    operation: Literal['put','standby']
    name: str = Field(min_length=1,max_length=120)
    priority: Literal['must','want','optional']
    quote: str = Field(min_length=1,max_length=500)


class BasketProposal(Strict):
    items: tuple[BasketProposalItem,...] = Field(min_length=1,max_length=12)
    basket_version: int = Field(ge=0)


class BasketCandidate(ObjectRef):
    name: str = Field(min_length=1,max_length=120)
    address: str | None = Field(default=None,max_length=300)


class BasketChoicePrompt(Strict):
    query: str = Field(min_length=1,max_length=120)
    quote: str = Field(min_length=1,max_length=500)
    priority: Literal['must','want','optional']
    candidates: tuple[BasketCandidate,...] = Field(min_length=2,max_length=3)


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
    # A turn may contain several bounded subrequests.  The server still
    # validates each typed branch and executes it through its own whitelist.
    model_config = {'json_schema_extra':{'description':
        'A turn can contain conversation plus read-only queries and/or one planning patch. '
        'Each branch is independent; formal itinerary submission is never part of this object.'}}
    schema_version: Literal['travel-intent/3.3'] = 'travel-intent/3.3'
    actions: tuple[Action, ...] = Field(default=(), max_length=8)
    constraints: tuple[Constraint, ...] = Field(default=(), max_length=16)
    corrections: tuple[Correction, ...] = Field(default=(), max_length=4)
    unresolved: tuple[str, ...] = Field(default=(), max_length=8)
    planning: PlanningIntent | None = None
    conversation: Conversation | None = None
    route_query: ReferenceRouteIntent | None = None
    weather_query: WeatherIntent | None = None
    service_query: ServiceIntent | None = None
    state_request: StateIntent | None = None

    @model_validator(mode='before')
    @classmethod
    def canonical_action_index(cls,value):
        # Typed branches carry the meaning; actions are only an execution index derived
        # from them. Fill the index entry a branch implies so the model states each
        # request once. This grants nothing: every branch still passes its own gates.
        if not isinstance(value,dict):return value
        actions=list(value.get('actions') or [])
        def has(kind,quote=None):
            return any(isinstance(a,dict) and a.get('kind')==kind and (quote is None or a.get('quote')==quote) for a in actions)
        planning=value.get('planning')
        if isinstance(planning,dict) and planning.get('quote') and not has('modify'):
            actions=[a for a in actions if not (isinstance(a,dict) and a.get('kind')=='query' and a.get('quote')==planning['quote'])]
            actions.append({'kind':'modify','quote':planning['quote']})
        for branch,kind in (('weather_query','query'),('route_query','query'),('service_query','search_services')):
            quote=(value.get(branch) or {}).get('quote') if isinstance(value.get(branch),dict) else None
            if quote and not has(kind,quote):actions.append({'kind':kind,'quote':quote})
        state=value.get('state_request')
        if isinstance(state,dict) and state.get('quote'):
            kind={'complete':'progress','skip':'progress','position':'position','cancel':'cancel','restore':'modify'}.get(state.get('operation'))
            if kind and not has(kind,state['quote']):actions.append({'kind':kind,'quote':state['quote']})
        return dict(value,actions=actions) if actions!=list(value.get('actions') or []) else value

    @model_validator(mode='after')
    def validate_subrequests(self):
        if self.state_request:
            expected={'complete':'progress','skip':'progress','position':'position','cancel':'cancel','restore':'modify'}[self.state_request.operation]
            if not any(a.kind==expected and a.quote==self.state_request.quote for a in self.actions):
                raise ValueError('state request needs matching action')
            if self.planning or self.service_query or self.weather_query or self.route_query or self.conversation or self.constraints:
                raise ValueError('state operation needs its own turn')
        if self.route_query and self.route_query.use_current_position and self.route_query.origin:
            raise ValueError('route query cannot carry both origin and current position')
        if self.weather_query and not any(a.kind=='query' and a.quote==self.weather_query.quote for a in self.actions):
            raise ValueError('weather query needs a matching read action')
        if self.service_query and not any(a.kind=='search_services' and a.quote==self.service_query.quote for a in self.actions):
            raise ValueError('service query needs a matching search action')
        if self.route_query and not any(a.kind=='query' and a.quote==self.route_query.quote for a in self.actions):
            raise ValueError('route query needs a matching read action')
        if self.planning and not any(a.kind=='modify' for a in self.actions):
            raise ValueError('planning needs a modify action')
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
