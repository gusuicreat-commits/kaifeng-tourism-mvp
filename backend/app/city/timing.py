"""Independent connection-time assessment, NOT a city solver or navigation guarantee."""
from datetime import timedelta
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .schema import EntityId, Evidence, Strict, TravelEstimate


class ShowTiming(Strict):
    place_id: EntityId
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    evidence: Evidence
    admission_deadline: AwareDatetime | None = None
    admission_evidence: Evidence | None = None

    @model_validator(mode='after')
    def consistency(self):
        if self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError('演出结束必须晚于开始')
        if self.admission_deadline and self.admission_deadline > self.starts_at:
            raise ValueError('停止入场时间不能晚于开演')
        return self


class ConnectionInput(Strict):
    previous_ends_at: AwareDatetime
    assessed_at: AwareDatetime
    travel: TravelEstimate
    show: ShowTiming
    buffer_seconds: int = Field(default=300, ge=0, le=7200)
    # A product parameter, not measured queuing time.
    tight_threshold_seconds: int = Field(default=300, ge=0, le=7200)
    boundary_seconds: int | None = Field(default=None, ge=0, le=86400)
    boundary_evidence: Evidence | None = None

    @model_validator(mode='after')
    def endpoint(self):
        if self.travel.destination_place_id != self.show.place_id:
            raise ValueError('转场终点与演出场地不匹配')
        return self


class ConnectionAssessment(Strict):
    status: Literal['slack', 'tight', 'conflict', 'pending']
    latest_arrival: AwareDatetime | None
    earliest_arrival: AwareDatetime | None
    conservative_arrival: AwareDatetime | None
    slack_seconds: int | None
    planned_wait_seconds: int | None
    issues: tuple[str, ...]
    buffer_basis: Literal['planning_parameter'] = 'planning_parameter'
    navigation_certified: Literal[False] = False


def assess_connection(data: ConnectionInput) -> ConnectionAssessment:
    issues = []
    show = data.show
    # Facts must be fresh now AND applicable on the activity date.
    show_known = (show.evidence.kind == 'verified' and show.evidence.usable_at(data.assessed_at)
                  and show.evidence.usable_at(show.starts_at))
    if not show_known:
        issues.append('SHOW_FACT_UNVERIFIED_OR_EXPIRED')
    if show.ends_at is None:
        issues.append('SHOW_END_UNKNOWN')
    elif not show.evidence.usable_at(show.ends_at):
        issues.append('SHOW_END_OUTSIDE_EVIDENCE_VALIDITY')
    latest = show.starts_at - timedelta(seconds=data.buffer_seconds) if show_known else None
    if show.admission_deadline:
        admission = show.admission_evidence
        if (admission and admission.kind == 'verified' and admission.usable_at(data.assessed_at)
                and admission.usable_at(show.starts_at)):
            if latest is not None:
                latest = min(latest, show.admission_deadline)
        else:
            issues.append('ADMISSION_DEADLINE_UNVERIFIED')

    travel = data.travel
    travel_known = travel.usable_at(data.previous_ends_at) and travel.evidence.usable_at(data.assessed_at)
    if not travel_known:
        issues.append('TRAVEL_TIME_OR_PATH_UNKNOWN')
    boundary = data.boundary_evidence
    boundary_known = (data.boundary_seconds is not None and boundary is not None and boundary.kind == 'verified'
                      and boundary.usable_at(data.assessed_at) and boundary.usable_at(data.previous_ends_at))
    if not boundary_known:
        issues.append('BOUNDARY_RULE_OR_DURATION_UNKNOWN')

    earliest = conservative = slack = wait = None
    if travel_known and boundary_known:
        earliest = data.previous_ends_at + timedelta(seconds=travel.lower_seconds + data.boundary_seconds)
        conservative = data.previous_ends_at + timedelta(seconds=travel.upper_seconds + data.boundary_seconds)
        if latest is not None:
            slack = int((latest - conservative).total_seconds())
            wait = max(0, int((show.starts_at - conservative).total_seconds()))
    # A known violation stays visible even when a different dependency is missing.
    conflict = latest is not None and (data.previous_ends_at > latest or (slack is not None and slack < 0))
    if conflict:
        issues.append('ARRIVAL_DEADLINE_CONFLICT')
    status = ('conflict' if conflict else 'pending' if issues else
              'tight' if slack < data.tight_threshold_seconds else 'slack')
    return ConnectionAssessment(status=status, latest_arrival=latest, earliest_arrival=earliest,
                                conservative_arrival=conservative, slack_seconds=slack,
                                planned_wait_seconds=wait, issues=tuple(issues))
