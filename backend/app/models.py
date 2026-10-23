from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator, field_validator

Interest = Literal['show','photo','food','culture']
Dataset = Literal['demo','reported']


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenic_id: str
    available_minutes: int = Field(default=180, ge=15, le=720)
    interests: list[Interest] = Field(default_factory=lambda:['culture'], max_length=4)
    must_visit: list[str] = Field(default_factory=list, max_length=6)
    avoid_crowds: bool = True
    walking: Literal['relaxed','normal','brisk'] = 'normal'
    mode: Literal['shortest','preference','quiet'] = 'preference'
    dataset: Dataset = 'demo'
    hour: int = Field(default=13,ge=0,le=23)
    visit_start: datetime | None = None
    must_attend: list[str] = Field(default_factory=list, max_length=6)
    excluded_ids: list[str] = Field(default_factory=list, max_length=6)
    # Temporary scenario, intentionally limited to simulated data; never stored as an observation.
    crowd_overrides: dict[str,float] = Field(default_factory=dict, max_length=8)

    @model_validator(mode='after')
    def validate_scenario(self):
        if self.visit_start is not None:
            if self.visit_start.tzinfo is None or self.visit_start.utcoffset() is None:
                raise ValueError('入园时间必须包含时区')
            from zoneinfo import ZoneInfo
            self.visit_start = self.visit_start.astimezone(ZoneInfo('Asia/Shanghai'))
            self.hour = self.visit_start.hour
        if len(self.must_attend) != len(set(self.must_attend)):
            raise ValueError('必看场次不可重复')
        if self.crowd_overrides and self.dataset!='demo':
            raise ValueError('情景调整只能用于演示数据')
        if any(not 0<=v<=1 for v in self.crowd_overrides.values()):
            raise ValueError('拥挤值必须在0到1之间')
        if len(self.must_visit)!=len(set(self.must_visit)):
            raise ValueError('必去点不可重复')
        if len(self.excluded_ids)!=len(set(self.excluded_ids)) or set(self.must_visit)&set(self.excluded_ids):
            raise ValueError('排除点不可重复或与必去点冲突')
        return self


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenic_id: str
    start: str = 'gate'
    end: str = 'exit'
    mode: Literal['shortest','quiet'] = 'shortest'
    dataset: Dataset = 'demo'
    hour: int = Field(default=13,ge=0,le=23)
    crowd_overrides: dict[str,float] = Field(default_factory=dict,max_length=8)

    @model_validator(mode='after')
    def validate_scenario(self):
        if self.dataset != 'demo' and self.crowd_overrides:
            raise ValueError('情景调整只能用于演示数据')
        if any(not 0<=v<=1 for v in self.crowd_overrides.values()):
            raise ValueError('拥挤值必须在0到1之间')
        return self


class Observation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    scenic_id: str
    node_id: str
    observed_at: datetime
    period_end: datetime | None = None
    count: int | None = Field(default=None,ge=0,le=10000000)
    crowd_level: float | None = Field(default=None,ge=0,le=1)
    capacity: int | None = Field(default=None,gt=0,le=10000000)
    source_type: Literal['official_history','official_summary','field','third_party']
    source_name: str = Field(min_length=1,max_length=200)
    source_url: HttpUrl | None = None
    measurement_kind: Literal['observation','aggregate']
    confidence: float = Field(ge=0,le=1)
    is_simulated: bool

    @field_validator('observed_at','period_end')
    @classmethod
    def timezone_required(cls,value):
        if value and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('时间必须包含时区，例如 +08:00')
        return value.astimezone(timezone.utc) if value else value

    @model_validator(mode='after')
    def validate_measurement(self):
        if self.count is None and self.crowd_level is None:
            raise ValueError('至少提供 count 或 crowd_level')
        if self.measurement_kind=='aggregate' and not self.period_end:
            raise ValueError('汇总数据必须提供 period_end')
        if self.measurement_kind=='observation' and self.period_end:
            raise ValueError('瞬时观测不应提供 period_end')
        if self.period_end and self.period_end<=self.observed_at:
            raise ValueError('period_end 必须晚于 observed_at')
        return self
