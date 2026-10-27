"""Versioned city domain. These are internal contracts, not trusted HTTP write inputs.

Facts must be loaded by server adapters. A valid shape is not proof of a real place,
walkable road, opening time, consent, ownership, or a feasible itinerary.
"""
from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

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
    path_status: Literal['verified', 'estimated', 'demo', 'unknown'] = 'unknown'
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


class DayIdentity(Strict):
    business_date: date
    day_id: EntityId = Field(default_factory=lambda data:'day:'+data['business_date'].isoformat())

    @model_validator(mode='before')
    @classmethod
    def identity(cls,value):
        if isinstance(value,dict) and value.get('business_date') is not None:
            value=dict(value)
            day=TypeAdapter(date).validate_python(value['business_date'])
            expected='day:'+day.isoformat()
            if 'day_id' in value and value['day_id']!=expected:raise ValueError('day_id 必须与业务日期一致：'+expected)
            value['day_id']=expected
        return value


def validate_days(start,end,days):
    count=(end-start).days+1
    if not 1<=count<=3:raise ValueError('日范围必须为连续 1–3 天')
    if len({d.day_id for d in days})!=len(days):raise ValueError('day_id 重复')
    expected=tuple(start+timedelta(days=i) for i in range(count))
    if tuple(d.business_date for d in days)!=expected:raise ValueError('每日日期必须按日范围连续排列，无遗漏或重复')


class DateRange(Strict):
    start_date: date
    end_date: date
    timezone: Literal['Asia/Shanghai'] = 'Asia/Shanghai'
    day_count: int = Field(default_factory=lambda data:(data['end_date']-data['start_date']).days+1,ge=1,le=3,strict=True)

    @model_validator(mode='after')
    def range_valid(self):
        count=(self.end_date-self.start_date).days+1
        if not 1<=count<=3:raise ValueError('日范围必须为连续 1–3 天')
        if self.day_count!=count:raise ValueError('day_count 必须与日范围一致')
        return self


class DayWindow(DayIdentity):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    origin_place_id: EntityId
    destination_place_id: EntityId

    @model_validator(mode='after')
    def window_valid(self):
        if self.ends_at<=self.starts_at:raise ValueError('每日结束须晚于开始')
        if any(t.astimezone(TZ).date()!=self.business_date for t in (self.starts_at,self.ends_at)):
            raise ValueError('每日时间窗必须属于上海时区同一业务日')
        return self


class Overnight(Strict):
    overnight_id: EntityId
    from_day_id: EntityId
    to_day_id: EntityId
    night_date: date
    place_id: EntityId | None = None
    source_kind: Literal['selected_hotel','user_place']
    service_selection_id: str | None = Field(default=None,max_length=120)
    service_version: int | None = Field(default=None,ge=0)
    binding_revision: int | None = Field(default=None,ge=1)
    check_in: AwareDatetime | None = None
    check_out: AwareDatetime | None = None
    place_evidence: Literal['verified','reference','synthetic','unknown'] = 'unknown'
    stay_evidence: Literal['verified','reference','synthetic','unknown'] = 'unknown'
    evidence_refs: tuple[EntityId,...] = Field(default=(),max_length=20)

    @model_validator(mode='after')
    def valid(self):
        if self.from_day_id!='day:'+self.night_date.isoformat() or self.to_day_id!='day:'+(self.night_date+timedelta(days=1)).isoformat():
            raise ValueError('过夜边界必须连接相邻日期的 day_id')
        if self.check_in and self.check_out and self.check_out<=self.check_in:raise ValueError('入住退房时刻无效')
        return self


def validate_overnights(days,overnights,complete=False):
    if len({o.overnight_id for o in overnights})!=len(overnights):raise ValueError('过夜 ID 重复')
    pairs={(a.day_id,b.day_id):(a,b) for a,b in zip(days,days[1:])}
    seen=set()
    for o in overnights:
        pair=(o.from_day_id,o.to_day_id)
        if pair not in pairs or pair in seen:raise ValueError('过夜边界不存在或重复')
        seen.add(pair);a,b=pairs[pair]
        if o.place_id and any(p is not None and p!=o.place_id for p in (a.destination_place_id,b.origin_place_id)):
            raise ValueError('过夜地点必须与前日终点、次日起点一致')
        if complete and (not o.place_id or a.destination_place_id!=o.place_id or b.origin_place_id!=o.place_id):
            raise ValueError('正式路线过夜边界必须连接两个已知端点')
    if complete and seen!=set(pairs):raise ValueError('每对相邻日必须有一个过夜边界')


def validate_day_stops(day):
    pending=False;previous=day.starts_at
    for s in day.stops:
        for t in (s.window_start,s.window_end,s.actual_start,s.actual_end):
            if t and t.astimezone(TZ).date()!=day.business_date:raise ValueError('活动时刻必须属于所在业务日')
        if s.progress=='pending':pending=True
        elif pending:raise ValueError('已完成或跳过的记录必须组成每日已执行前缀')
        if s.progress=='completed':
            if (previous and s.actual_start<previous) or (day.ends_at and s.actual_end>day.ends_at):
                raise ValueError('完成记录必须组成不重叠的每日已执行前缀')
            previous=s.actual_end


def validate_trip_stops(days):
    stops=[s for d in days for s in d.stops]
    ids=[s.stop_id for s in stops];sessions=[s.session_id for s in stops if s.session_id]
    if len(set(ids))!=len(ids) or len(set(sessions))!=len(sessions):raise ValueError('全程活动或场次重复')
    if sum(d.status=='active' for d in days)>1:raise ValueError('全程最多一个 active 日')


class CityTripDay(DayWindow):
    stops: tuple[Stop,...] = Field(default=(),max_length=24)
    status: Literal['pending','active','closed'] = 'pending'

    @model_validator(mode='after')
    def valid(self):
        validate_day_stops(self)
        return self


class CityTripV2(DateRange):
    schema_version: Literal['city-trip/2']
    trip_id: UUID
    version: int = Field(ge=1)
    route_version: int = Field(default=0,ge=0)
    city_id: Literal['kaifeng'] = 'kaifeng'
    status: Literal['DRAFT','PLANNED','ACTIVE','PAUSED','COMPLETED','CANCELLED','ARCHIVED'] = 'DRAFT'
    days: tuple[CityTripDay,...] = Field(min_length=1,max_length=3)
    overnights: tuple[Overnight,...] = Field(default=(),max_length=2)
    anchor: PositionAnchor | None = None
    legacy_trip_id: UUID | None = None

    @model_validator(mode='after')
    def valid(self):
        validate_days(self.start_date,self.end_date,self.days)
        validate_trip_stops(self.days)
        validate_overnights(self.days,self.overnights,complete=self.route_version>0)
        return self
