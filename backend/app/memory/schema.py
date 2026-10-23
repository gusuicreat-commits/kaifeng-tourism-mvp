from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..models import Interest, PlanRequest


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Draft(Strict):
    scenic_id: str = Field(default='', max_length=120)
    visit_date: str = ''
    visit_time: str = Field(default='', pattern=r'^$|^([01]\d|2[0-3]):[0-5]\d$')
    available_minutes: int | None = Field(default=None, ge=15, le=720)
    interests: list[Interest] = Field(default_factory=list, max_length=4)
    must_visit: list[str] = Field(default_factory=list, max_length=6)
    must_attend: list[str] = Field(default_factory=list, max_length=6)
    walking: Literal['normal', 'relaxed', 'brisk'] | None = None
    mode: Literal['shortest', 'preference', 'quiet'] | None = None

    @model_validator(mode='after')
    def valid_date(self):
        if self.visit_date:
            date.fromisoformat(self.visit_date)
        return self


class Answered(Strict):
    basics: bool = False
    interests: bool = False
    pace: bool = False


class Interview(Strict):
    draft: Draft = Field(default_factory=Draft)
    answered: Answered = Field(default_factory=Answered)


class RequestId(Strict):
    request_id: UUID


class CreateTrip(RequestId):
    interview: Interview = Field(default_factory=Interview)


class UpdateTrip(RequestId):
    expected_version: int = Field(ge=1)
    operation: Literal['save_draft', 'transition', 'mark_completed']
    interview: Interview | None = None
    status: Literal['DRAFT', 'PLANNED', 'ACTIVE', 'PAUSED', 'COMPLETED', 'ARCHIVED', 'CANCELLED'] | None = None
    poi_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode='after')
    def operation_fields(self):
        fields = {'save_draft': ('interview',), 'transition': ('status',), 'mark_completed': ('poi_id',)}
        expected = fields[self.operation]
        for key in ('interview', 'status', 'poi_id'):
            if (getattr(self, key) is not None) != (key in expected):
                raise ValueError('操作字段不匹配')
        return self


class Replan(RequestId):
    expected_version: int = Field(ge=1)
    constraints: PlanRequest


class Chat(RequestId):
    trip_id: UUID
    trip_scope: Literal['scenic','city'] = 'scenic'
    text: str = Field(min_length=1, max_length=2000)
    channel: Literal['text', 'voice'] = 'text'
    route_version: int | None = Field(default=None, ge=0)


class Confirm(RequestId):
    expected_version: int = Field(ge=1)
    proposal_id: UUID


class Candidate(Strict):
    operation: Literal['update_constraints', 'remove_stop']
    patch: dict = Field(default_factory=dict)
    poi_id: str | None = Field(default=None, max_length=120)
    # No arbitrary owner, SQL, GPS, or long-term preference keys.
    @model_validator(mode='after')
    def whitelist(self):
        allowed = {'available_minutes', 'interests', 'must_visit', 'must_attend', 'walking', 'mode', 'visit_start'}
        if set(self.patch) - allowed:
            raise ValueError('候选修改包含未授权字段')
        if self.operation == 'remove_stop' and (not self.poi_id or self.patch):
            raise ValueError('删除站点需明确点位，不接受混合操作')
        if self.operation == 'update_constraints' and (not self.patch or self.poi_id):
            raise ValueError('缺少候选修改')
        return self


class HistorySetting(Strict):
    enabled: bool


class ConfirmedAction(RequestId):
    confirmed: bool
