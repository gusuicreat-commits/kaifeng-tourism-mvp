from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class NormalizedSession:
    program_name: str
    venue_name: str
    service_date: date
    start_time: time | None
    schedule_note: str = ""
    area: str | None = None
    duration_minutes: int | None = None
    end_time: time | None = None
    entry_deadline: time | None = None
    requires_booking: bool | None = None
    weather_sensitive: bool | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    operating_status: str = "scheduled"


class AdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=100)
    expected_version: int | None = Field(default=None, ge=1)


class ReviewRequest(AdminRequest):
    decision: str = Field(pattern="^(approve|reject|mark_unavailable)$")
    applicability_verified: bool = False
    completeness_verified: bool = False
    note: str = Field(default="", max_length=1000)


class VenueBindingRequest(AdminRequest):
    venue_name: str = Field(min_length=1, max_length=200)
    verified_poi_id: str | None = Field(default=None, min_length=1, max_length=120)
    routing_verified: bool = False
    verification_method: str = Field(default="", max_length=1000)
    evidence_ref: str = Field(default="", max_length=1000)


class SyncSettings(BaseModel):
    database_url: str | None = None
    source_url: str = "https://www.qmsyun.com/Mobile_page/o_program.php"
    minimum_interval_minutes: int = 60
    connect_timeout_seconds: float = 6
    read_timeout_seconds: float = 20
    maximum_bytes: int = 2_000_000


class PublishRequest(AdminRequest):
    confirmed_verified_routes: bool


@dataclass
class ParsedSchedule:
    source_updated_on: date | None
    sessions: list[NormalizedSession]
    complete: bool
    issues: list[str] = field(default_factory=list)


CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def parse_clock(value: str, *, allow_empty=True) -> time | None:
    value = value.strip().replace("：", ":")
    if not value and allow_empty:
        return None
    if not CLOCK.fullmatch(value):
        raise ValueError(f"时间须为 HH:MM：{value!r}")
    return time.fromisoformat(value)


def optional_date(value: str) -> date | None:
    return date.fromisoformat(value.strip()) if value.strip() else None

