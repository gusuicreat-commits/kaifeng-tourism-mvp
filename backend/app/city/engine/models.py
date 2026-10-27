"""Stable tool inputs; operational facts are server-only, never HTTP request fields."""
from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator
from ..schema import Strict, EntityId, Mode, Evidence, TravelEstimate, Stop, TZ


class Goal(Strict):
    goal_id: EntityId
    target_kind: Literal['place', 'session', 'program', 'food']
    target_id: EntityId
    kind: Literal['visit', 'show', 'dining', 'rest'] = 'visit'
    required: bool = False
    importance: int = Field(default=5, ge=0, le=10)
    fixed_place_id: EntityId | None = None
    fixed_session_id: EntityId | None = None
    duration_seconds: int | None = Field(default=None, ge=1, le=21600)
    window_start: AwareDatetime | None = None
    window_end: AwareDatetime | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.fixed_session_id and self.target_kind not in ('session','program'):raise ValueError('只有演出目标可固定场次')
        if self.fixed_place_id and self.target_kind=='place' and self.fixed_place_id!=self.target_id:raise ValueError('固定地点与目标不一致')
        if self.fixed_session_id and self.target_kind=='session' and self.fixed_session_id!=self.target_id:raise ValueError('固定场次与目标不一致')
        if self.target_kind in ('session','program') and self.kind != 'show':
            raise ValueError('场次/节目目标须使用 show')
        if self.target_kind == 'food' and self.kind != 'dining':raise ValueError('菜品目标须使用 dining')
        if self.target_kind == 'place' and self.kind == 'show':raise ValueError('演出须指定场次或节目')
        if self.window_start and self.window_end and self.window_end <= self.window_start:raise ValueError('目标时间窗无效')
        return self


class Requirements(Strict):
    schema_version: Literal['city-engine/1'] = 'city-engine/1'
    business_date: date
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    origin_place_id: EntityId
    destination_place_id: EntityId
    goals: tuple[Goal, ...] = Field(min_length=0, max_length=30)
    modes: tuple[Mode, ...] = Field(default=('walking',), min_length=1, max_length=3)
    pace: Literal['relaxed','standard','compact'] = 'standard'
    objective: Literal['balanced','less_walking','less_waiting','lower_cost'] = 'balanced'
    max_walking_seconds: int | None = Field(default=None, ge=1, le=43200)
    budget_fen: int | None = Field(default=None, ge=0, le=100000000)
    access_confirmations: tuple[EntityId, ...] = Field(default=(), max_length=60)
    show_buffer_seconds: int = Field(default=300, ge=0, le=3600)
    solve_seconds: float = Field(default=1, ge=0.05, le=3)
    alternatives: int = Field(default=3, ge=1, le=3)

    @model_validator(mode='after')
    def valid(self):
        if self.ends_at <= self.starts_at:raise ValueError('结束须晚于开始')
        if any(t.astimezone(TZ).date()!=self.business_date for t in (self.starts_at,self.ends_at)):
            raise ValueError('仅支持上海时区同一业务日')
        if len({g.goal_id for g in self.goals})!=len(self.goals):raise ValueError('目标 ID 重复')
        if len(set(self.modes))!=len(self.modes):raise ValueError('交通方式重复')
        return self


class Boundary(Strict):
    park_id: EntityId
    gate_place_id: EntityId
    direction: Literal['entry','exit']
    seconds: int = Field(ge=0, le=7200)
    evidence: Evidence
    cost_upper_fen: int | None = Field(default=None, ge=0)


class Journey(Strict):
    journey_id: EntityId
    revision: int = Field(default=1, ge=1)
    travel: TravelEstimate
    distance_m: float = Field(ge=0, le=1000000)
    cost_lower_fen: int | None = Field(default=None, ge=0)
    cost_upper_fen: int | None = Field(default=None, ge=0)
    boundaries: tuple[Boundary, ...] = Field(default=(), max_length=8)
    # Curated source facts carry provenance and usage rights; a URL string is insufficient.
    source_ids: tuple[EntityId, ...] = Field(default=(), max_length=20)
    # A conservative static envelope: it must cover the entire planning horizon.
    time_basis: Literal['survey_envelope','provider_estimate','synthetic']
    estimated_seconds: int | None = Field(default=None, ge=0, le=172800)
    planning_buffer_seconds: int = Field(default=0, ge=0, le=7200)
    quote_expires_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def costs(self):
        if (self.cost_lower_fen is None)!=(self.cost_upper_fen is None):raise ValueError('费用区间须完整或未知')
        if self.cost_lower_fen is not None and self.cost_upper_fen<self.cost_lower_fen:raise ValueError('费用上界小于下界')
        return self


class PlaceCost(Strict):
    place_id: EntityId
    revision: int = Field(default=1, ge=1)
    lower_fen: int = Field(ge=0)
    upper_fen: int = Field(ge=0)
    evidence: Evidence

    @model_validator(mode='after')
    def valid(self):
        if self.upper_fen < self.lower_fen:raise ValueError('费用区间无效')
        return self


class OperationalBundle(Strict):
    schema_version: Literal['city-operations/1'] = 'city-operations/1'
    journeys: tuple[Journey, ...] = Field(default=(), max_length=5000)
    costs: tuple[PlaceCost, ...] = Field(default=(), max_length=2000)


class Issue(Strict):
    code: str
    message: str
    affected_ids: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


class ScheduledActivity(Strict):
    activity_id: EntityId
    goal_id: EntityId
    place_id: EntityId
    kind: Literal['visit','show','dining','rest']
    session_id: EntityId | None = None
    arrives_at: AwareDatetime
    starts_at: AwareDatetime
    departs_at: AwareDatetime
    duration_seconds: int
    wait_seconds: int
    required: bool
    adjustable: bool
    state: Literal['planned'] = 'planned'
    data_status: Literal['verified','reference','synthetic']
    evidence_refs: tuple[str, ...]


class ScheduledLeg(Strict):
    journey: Journey
    departs_at: AwareDatetime
    arrives_at: AwareDatetime


class Itinerary(Strict):
    objectives: tuple[Literal['balanced','less_walking','less_waiting','lower_cost'], ...] = Field(default=('balanced',), min_length=0, max_length=3)
    activities: tuple[ScheduledActivity, ...]
    legs: tuple[ScheduledLeg, ...]
    completed_prefix: tuple[Stop, ...] = ()
    ongoing: dict | None = None
    returns_at: AwareDatetime
    total_seconds: int
    travel_seconds: int
    walking_distance_m: float
    known_cost_upper_fen: int
    history_cost_fen: int = 0
    history_cost_unknown: bool = False
    history_cost_basis: Literal['none','user_reported'] = 'none'
    cost_unknown: bool
    score: dict[str, float]
    evidence_mode: Literal['verified_with_estimates','reference_with_estimates','synthetic']
    navigation_certified: Literal[False] = False


class SolveResult(Strict):
    status: Literal['feasible','infeasible','needs_data','timeout','unavailable','invalid']
    itineraries: tuple[Itinerary, ...] = ()
    issues: tuple[Issue, ...] = ()
    warnings: tuple[str, ...] = ()
    metrics: dict = Field(default_factory=dict)
    computed_at: AwareDatetime


class PreviewRequest(Strict):
    request_id: UUID
    expected_version: int = Field(ge=1)
    requirements: Requirements


class OngoingInput(Strict):
    stop_id: EntityId
    remaining_seconds: int = Field(ge=1, le=21600)
    basis: Literal['user_reported','prior_plan_estimate'] = 'user_reported'
    # Explicit user estimate or preserved plan estimate; never a measured queue.


class ReplanRequest(PreviewRequest):
    skipped_goal_ids: tuple[EntityId, ...] = Field(default=(), max_length=1,
        description='本轮明确跳过的一项剩余目标，允许在预览中解除必选；仅确认新路线时记录跳过。')
    ongoing: OngoingInput | None = None
    end_ongoing: bool = False
    spent_cost_fen: int | None = Field(default=None, ge=0, le=100000000)
    spend_as_of: AwareDatetime | None = None
    fixed_goal_ids: tuple[EntityId, ...] = Field(default=(), max_length=30)
    restore_route_version: int | None = Field(default=None, ge=1)


class ConfirmRequest(Strict):
    request_id: UUID
    expected_version: int = Field(ge=1)
    candidate_id: UUID
    alternative_index: int = Field(default=0, ge=0, le=2)
