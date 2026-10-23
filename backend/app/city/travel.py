"""Replaceable server-side ETA port. No provider is wired into production yet."""
from datetime import datetime
from typing import Protocol

from .schema import Evidence, Mode, TravelEstimate


class TravelTimeProvider(Protocol):
    def estimate(self, origin: str, destination: str, mode: Mode, departure: datetime) -> TravelEstimate: ...


def unknown(origin, destination, mode, reason):
    return TravelEstimate(origin_place_id=origin, destination_place_id=destination, mode=mode,
                          evidence=Evidence(evidence_id='unavailable', revision='1', kind='unknown'),
                          unavailable_reason=reason)


class UnavailableTravelTimeProvider:
    def estimate(self, origin, destination, mode, departure):
        return unknown(origin, destination, mode, '城市交通估时服务尚未接入')


class SurveyedWalkingProvider:
    """Only accepts prevalidated, directed server records. Never infers reverse legs.

    Record validity must cover the departure time; bounds include walking uncertainty.
    A path reference must identify the surveyed corridor, not a straight-line drawing.
    """
    def __init__(self, records: tuple[TravelEstimate, ...]):
        self.records = {}
        for record in records:
            key = (record.origin_place_id, record.destination_place_id, record.mode)
            if record.mode != 'walking' or record.path_status != 'verified' or record.evidence.kind != 'verified':
                raise ValueError('走查适配器只接收核验步行记录')
            if key in self.records:
                raise ValueError('同方向走查记录冲突，必须先由事实服务消解')
            self.records[key] = record

    def estimate(self, origin, destination, mode, departure):
        record = self.records.get((origin, destination, mode))
        if record and record.usable_at(departure):
            return record
        return unknown(origin, destination, mode, '无适用方向、交通方式或有效日期的核验步行记录')


def safe_estimate(provider: TravelTimeProvider, origin, destination, mode, departure):
    """Validate adapter output, degrading to unknown instead of zero/straight-line ETA."""
    try:
        result = TravelEstimate.model_validate(provider.estimate(origin, destination, mode, departure))
        if (result.origin_place_id, result.destination_place_id, result.mode) != (origin, destination, mode):
            return unknown(origin, destination, mode, '交通结果与请求端点或交通方式不一致')
        if result.usable_at(departure):
            return result
        return unknown(origin, destination, mode, result.unavailable_reason or '交通路径、证据或有效期不满足要求')
    except Exception:
        # Do not expose provider URLs, credentials, or raw exception payloads.
        return unknown(origin, destination, mode, '交通估时服务失败')
