"""Visitor mutations cannot provide ownership, verified facts or computed route versions."""
from datetime import date
from typing import Annotated, Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator
from .schema import (EntityId, PositionAnchor, Stop, Strict, TZ, DayIdentity, DateRange, Overnight,
                     validate_days, validate_day_stops, validate_trip_stops, validate_overnights)


class CityDraft(Strict):
    business_date: date | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    origin_place_id: EntityId | None = None
    destination_place_id: EntityId | None = None
    interests: tuple[Literal['show','photo','food','culture'], ...] = Field(default=(), max_length=4)
    dish_interest_ids: tuple[EntityId, ...] = Field(default=(), max_length=12)
    modes: tuple[Literal['walking','driving','transit'], ...] = Field(default=('walking',),min_length=1,max_length=3)
    pace: Literal['relaxed','standard','compact'] = 'standard'
    objective: Literal['balanced','less_walking','less_waiting','lower_cost'] = 'balanced'
    stops: tuple[Stop, ...] = Field(default=(), max_length=24)

    @model_validator(mode='after')
    def consistency(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError('结束时间必须晚于开始时间')
        for t in (self.starts_at, self.ends_at):
            if t and (not self.business_date or t.astimezone(TZ).date() != self.business_date):
                raise ValueError('起止时间必须属于明确的同一业务日')
        ids = [s.stop_id for s in self.stops]
        sessions = [s.session_id for s in self.stops if s.session_id]
        if len(set(ids)) != len(ids) or len(set(sessions)) != len(sessions): raise ValueError('活动或场次重复')
        pending = False; previous = self.starts_at
        for s in self.stops:
            if s.progress == 'pending': pending = True
            if s.progress == 'completed':
                if pending or (previous and s.actual_start < previous) or (self.ends_at and s.actual_end > self.ends_at):
                    raise ValueError('完成记录必须组成不重叠的已执行前缀')
                previous = s.actual_end
        return self


class CreateCityTrip(Strict):
    request_id: UUID
    draft: CityDraft = Field(default_factory=CityDraft)


class SaveDraft(Strict):
    operation: Literal['save_draft']
    draft: CityDraft


class SetAnchor(Strict):
    operation: Literal['set_anchor']
    anchor: PositionAnchor


class MarkCompleted(Strict):
    operation: Literal['mark_completed']
    stop_id: EntityId
    actual_start: AwareDatetime
    actual_end: AwareDatetime


class SkipStop(Strict):
    operation: Literal['skip_stop']
    stop_id: EntityId


class Transition(Strict):
    operation: Literal['transition']
    status: Literal['ACTIVE','PAUSED','COMPLETED','CANCELLED','ARCHIVED']


Action = Annotated[SaveDraft | SetAnchor | MarkCompleted | SkipStop | Transition, Field(discriminator='operation')]


class MutateCityTrip(Strict):
    request_id: UUID
    expected_version: int = Field(ge=1)
    action: Action


class ConfirmCityChange(Strict):
    request_id: UUID
    expected_version: int = Field(ge=1)
    proposal_id: UUID


class DayDraft(CityDraft,DayIdentity):
    business_date: date
    origin_text: str | None = Field(default=None,max_length=120)
    destination_text: str | None = Field(default=None,max_length=120)
    status: Literal['pending','active','closed'] = 'pending'

    @model_validator(mode='after')
    def day_valid(self):
        validate_day_stops(self)
        if len(set(self.modes))!=len(self.modes):raise ValueError('交通方式重复')
        return self


class CityDraftV2(DateRange):
    schema_version: Literal['city-draft/2']
    days: tuple[DayDraft,...] = Field(min_length=1,max_length=3)
    overnights: tuple[Overnight,...] = Field(default=(),max_length=2)

    @model_validator(mode='after')
    def valid(self):
        validate_days(self.start_date,self.end_date,self.days)
        validate_trip_stops(self.days)
        validate_overnights(self.days,self.overnights)
        return self


class CreateCityTripV2(CreateCityTrip):
    draft: CityDraftV2


class SaveDraftV2(Strict):
    operation: Literal['save_draft']
    draft: CityDraftV2


class SaveCityDraftV2(MutateCityTrip):
    action: SaveDraftV2


class MarkCompletedV2(MarkCompleted):
    day_id: EntityId


class SkipStopV2(SkipStop):
    day_id: EntityId


class SetAnchorV2(SetAnchor):
    day_id: EntityId


class CloseDay(Strict):
    operation: Literal['close_day']
    day_id: EntityId


ActionV2=Annotated[SaveDraftV2|MarkCompletedV2|SkipStopV2|SetAnchorV2|CloseDay|Transition,Field(discriminator='operation')]


class MutateCityTripV2(MutateCityTrip):
    schema_version: Literal['city-state/2']
    action: ActionV2


class CityStateV2(Strict):
    id: UUID
    schema_version: Literal['city-state/2']
    status: Literal['DRAFT','ACTIVE','PAUSED','COMPLETED','CANCELLED','ARCHIVED']
    draft: CityDraftV2
    anchor: PositionAnchor | None
    version: int = Field(ge=1)
    route_version: int = Field(ge=0)
    updated_at: AwareDatetime
    readiness: dict
