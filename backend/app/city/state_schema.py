"""Visitor mutations cannot provide ownership, verified facts or computed route versions."""
from datetime import date
from typing import Annotated, Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator
from .schema import EntityId, PositionAnchor, Stop, Strict, TZ


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
