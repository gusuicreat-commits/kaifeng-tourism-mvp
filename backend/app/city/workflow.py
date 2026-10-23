"""Prepare remaining work and confirmation policy; never writes state or calls an LLM.

M7 will authorize, query facts, run the city solver outside a write transaction, and
recheck versions before an atomic commit. This module does not replace those gates.
"""
from dataclasses import dataclass
from datetime import datetime

from .schema import CityChangeRequest, CityTrip, PositionAnchor, Stop


@dataclass(frozen=True)
class RemainingWork:
    trip_id: str
    base_version: int
    base_route_version: int
    origin: PositionAnchor
    destination_place_id: str
    remaining_seconds: int
    completed_prefix: tuple[Stop, ...]
    remaining_stops: tuple[Stop, ...]
    locked_stop_ids: tuple[str, ...]


def prepare_remaining(trip: CityTrip, anchor: PositionAnchor, known_place_ids: set[str],
                      now: datetime, max_anchor_age_seconds: int = 900) -> RemainingWork:
    """known_place_ids must come from M2, not a user-supplied entity allow-list."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('当前时间必须包含时区')
    if anchor.place_id not in known_place_ids:
        raise ValueError('当前位置必须是事实服务中的已知地点')
    age = (now - anchor.confirmed_at).total_seconds()
    if max_anchor_age_seconds < 0 or not 0 <= age <= max_anchor_age_seconds:
        raise ValueError('当前位置过期或来自未来，请重新确认')
    if not trip.starts_at <= anchor.confirmed_at <= now < trip.ends_at:
        raise ValueError('当前时间或位置锚点不在行程时间范围内')
    completed = tuple(s for s in trip.stops if s.progress == 'completed')
    if completed and completed[-1].actual_end > anchor.confirmed_at:
        raise ValueError('当前位置早于已完成活动，不能重写历史')
    remaining = tuple(s for s in trip.stops if s.progress == 'pending')
    return RemainingWork(trip_id=str(trip.trip_id), base_version=trip.version,
                         base_route_version=trip.route_version, origin=anchor,
                         destination_place_id=trip.destination_place_id,
                         # Use current time, not stale confirmation time, for the budget.
                         remaining_seconds=int((trip.ends_at - now).total_seconds()),
                         completed_prefix=completed, remaining_stops=remaining,
                         locked_stop_ids=tuple(s.stop_id for s in remaining if s.locked))


def confirmation_required(trip: CityTrip, request: CityChangeRequest) -> bool:
    if request.trip_id != trip.trip_id or request.expected_version != trip.version:
        raise ValueError('行程或版本不匹配，请重新读取后生成候选')
    change = request.change
    if change.operation == 'remove_stop':
        stop = next((s for s in trip.stops if s.stop_id == change.stop_id), None)
        if stop is None or stop.progress != 'pending':
            raise ValueError('仅能修改当前未完成的活动')
        if stop.locked:
            return True
    if change.operation == 'set_end_time':
        return True
    # This is a minimum gate. The eventual solver MUST also check indirect impacts
    # on locked goals; False does not authorize a write or release hard constraints.
    return request.channel == 'natural_language'
