"""Versioned city domain. These are internal contracts, not trusted HTTP write inputs.

Facts must be loaded by server adapters. A valid shape is not proof of a real place,
walkable road, opening time, consent, ownership, or a feasible itinerary.
"""
from datetime import date
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

TZ = ZoneInfo('Asia/Shanghai')
EntityId = Annotated[str, Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')]
Mode = Literal['walking', 'driving', 'transit']
EvidenceKind = Literal['verified', 'estimate', 'reported', 'demo', 'unknown']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, frozen=True)


class Evidence(Strict):
    evidence_id: EntityId
    revision: EntityId
    kind: EvidenceKind
    source: str | None = Field(default=None, min_length=1, max_length=500)
    checked_at: AwareDatetime | None = None
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None

    @model_validator(mode='after')
    def validity(self):
        if self.kind != 'unknown' and (not self.source or not self.checked_at):
            raise ValueError('非未知证据必须记录来源与核对时间')
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError('证据有效区间无效')
        if self.checked_at and self.valid_until and self.valid_until <= self.checked_at:
            raise ValueError('证据核对时已失效')
        return self

    def usable_at(self, moment):
        # No expiry means unknown freshness, not indefinitely trustworthy.
        return bool(self.kind in ('verified', 'estimate') and self.checked_at
                    and self.checked_at <= moment and self.valid_from is not None
                    and self.valid_until is not None and self.valid_from <= moment < self.valid_until)


class Stop(Strict):
    stop_id: EntityId
    place_id: EntityId
    kind: Literal['visit', 'show', 'dining', 'rest', 'return']
    scenic_id: EntityId | None = None
    session_id: EntityId | None = None
    locked: bool = False
    duration_seconds: int | None = Field(default=None, ge=1, le=86400)
    window_start: AwareDatetime | None = None
    window_end: AwareDatetime | None = None
    evidence_refs: tuple[EntityId, ...] = Field(default=(), max_length=20)
    progress: Literal['pending', 'completed', 'skipped'] = 'pending'
    actual_start: AwareDatetime | None = None
    actual_end: AwareDatetime | None = None
    progress_source: Literal['user_reported'] | None = None

    @model_validator(mode='after')
    def consistency(self):
        if (self.kind == 'show') != (self.session_id is not None):
            raise ValueError('演出活动必须关联场次；其他活动不接收场次')
        if self.window_start and self.window_end and self.window_end <= self.window_start:
            raise ValueError('活动时间窗无效')
        if self.progress == 'completed':
            if not self.actual_start or not self.actual_end or self.actual_end < self.actual_start:
                raise ValueError('完成活动必须保留有效的实际起止时间')
        elif self.actual_start or self.actual_end:
            raise ValueError('未完成活动不接收完成时间')
        if self.progress == 'pending' and self.progress_source:
            raise ValueError('未执行活动不接收进度来源')
        if self.progress == 'skipped' and self.locked:
            raise ValueError('必须先明确解除锁定，才能跳过重要目标')
        return self


class PositionAnchor(Strict):
    place_id: EntityId
    confirmed_at: AwareDatetime
    source: Literal['manual_known_place'] = 'manual_known_place'
    # No continuous coordinates or raw GPS in this contract.


class CityTrip(Strict):
    schema_version: Literal['city-trip/1'] = 'city-trip/1'
    trip_id: UUID
    version: int = Field(ge=1)
    route_version: int = Field(default=0, ge=0)
    city_id: Literal['kaifeng'] = 'kaifeng'
    status: Literal['DRAFT', 'PLANNED', 'ACTIVE', 'PAUSED', 'COMPLETED', 'CANCELLED', 'ARCHIVED'] = 'DRAFT'
    business_date: date
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    origin_place_id: EntityId
    destination_place_id: EntityId
    modes: tuple[Mode, ...] = Field(default=('walking',), min_length=1, max_length=3)
    interests: tuple[Literal['show', 'photo', 'food', 'culture'], ...] = Field(default=(), max_length=4)
    dish_interest_ids: tuple[EntityId, ...] = Field(default=(), max_length=12)
    stops: tuple[Stop, ...] = Field(min_length=1, max_length=24)
    anchor: PositionAnchor | None = None
    legacy_trip_id: UUID | None = None

    @model_validator(mode='after')
    def consistency(self):
        if self.ends_at <= self.starts_at:
            raise ValueError('结束时间必须晚于开始时间')
        if any(t.astimezone(TZ).date() != self.business_date for t in (self.starts_at, self.ends_at)):
            raise ValueError('首版仅支持上海时区同一业务日')
        if len(set(self.modes)) != len(self.modes):
            raise ValueError('交通方式不可重复')
        ids = [s.stop_id for s in self.stops]
        sessions = [s.session_id for s in self.stops if s.session_id]
        if len(ids) != len(set(ids)) or len(sessions) != len(set(sessions)):
            raise ValueError('活动和场次不可重复；同一地点的不同场次可以重复到访')
        pending_seen = False
        last_completed_end = self.starts_at
        for stop in self.stops:
            if stop.progress == 'completed':
                if pending_seen or stop.actual_start < last_completed_end or stop.actual_end > self.ends_at:
                    raise ValueError('完成记录必须组成不重叠的已执行前缀')
                last_completed_end = stop.actual_end
            elif stop.progress == 'pending':
                pending_seen = True
        return self


class TravelEstimate(Strict):
    origin_place_id: EntityId
    destination_place_id: EntityId
    mode: Mode
    lower_seconds: int | None = Field(default=None, ge=0, le=172800)
    upper_seconds: int | None = Field(default=None, ge=0, le=172800)
    evidence: Evidence
    path_status: Literal['verified', 'demo', 'unknown'] = 'unknown'
    path_ref: EntityId | None = None
    coordinate_system: Literal['WGS84', 'GCJ02', 'local_demo', 'unknown'] = 'unknown'
    unavailable_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def consistency(self):
        if (self.lower_seconds is None) != (self.upper_seconds is None):
            raise ValueError('估时区间必须完整，未知不填零')
        if self.lower_seconds is not None and self.lower_seconds > self.upper_seconds:
            raise ValueError('估时下界不能超过上界')
        if self.path_status == 'verified' and (not self.path_ref or self.coordinate_system in ('unknown', 'local_demo')):
            raise ValueError('核验路径必须关联真实路径及已知坐标系')
        return self

    def usable_at(self, moment):
        return (self.lower_seconds is not None and self.path_status == 'verified'
                and self.evidence.usable_at(moment))


class Leg(Strict):
    leg_id: EntityId
    from_stop_id: EntityId | None = None  # None = current confirmed anchor/origin.
    to_stop_id: EntityId | None = None  # None = requested return endpoint.
    departs_at: AwareDatetime | None = None
    travel: TravelEstimate
    boundary_seconds: int | None = Field(default=None, ge=0, le=86400)
    boundary_evidence: Evidence | None = None


class AddStop(Strict):
    operation: Literal['add_stop']
    stop: Stop


class RemoveStop(Strict):
    operation: Literal['remove_stop']
    stop_id: EntityId


class SetAnchor(Strict):
    operation: Literal['set_anchor']
    anchor: PositionAnchor


class SetEndTime(Strict):
    operation: Literal['set_end_time']
    ends_at: AwareDatetime


Change = Annotated[AddStop | RemoveStop | SetAnchor | SetEndTime, Field(discriminator='operation')]


class CityChangeRequest(Strict):
    request_id: UUID
    trip_id: UUID
    expected_version: int = Field(ge=1)
    channel: Literal['control', 'natural_language']
    change: Change
    # No owner or confirmation bypass boolean; M7 resolves identity/impact server-side.


class PlanningIssue(Strict):
    code: EntityId
    message: str = Field(min_length=1, max_length=500)
    affected_stop_ids: tuple[EntityId, ...] = ()
    evidence_refs: tuple[EntityId, ...] = ()


class ScheduledStop(Strict):
    stop: Stop
    arrives_at: AwareDatetime | None = None
    departs_at: AwareDatetime | None = None
    feasibility: Literal['pending', 'conflict', 'constraints_satisfied'] = 'pending'
    evidence_status: Literal['verified', 'estimate', 'reported', 'demo', 'unknown', 'mixed'] = 'unknown'


class CityPlanPreview(Strict):
    """Proposed output shape only. Schema validity cannot certify physical feasibility."""
    status: Literal['preview'] = 'preview'
    trip_id: UUID
    base_version: int = Field(ge=1)
    base_route_version: int = Field(ge=0)
    fact_revisions: dict[EntityId, EntityId] = Field(default_factory=dict, max_length=200)
    stops: tuple[ScheduledStop, ...] = Field(max_length=24)
    legs: tuple[Leg, ...] = Field(max_length=25)
    feasibility: Literal['pending', 'conflict', 'constraints_satisfied'] = 'pending'
    issues: tuple[PlanningIssue, ...] = Field(default=(), max_length=100)
    unmet_locked_stop_ids: tuple[EntityId, ...] = ()
    # No generic `success` boolean conflating evidence, feasibility and persistence.


class CityContracts(Strict):
    """Schema export root, not an HTTP body or persisted snapshot."""
    trip: CityTrip
    leg: Leg
    mutation: CityChangeRequest
    preview: CityPlanPreview
