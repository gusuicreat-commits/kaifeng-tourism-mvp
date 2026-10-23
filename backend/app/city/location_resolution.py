"""Ephemeral city location resolution and the first sample readiness gate.

POI search is an opt-in adapter boundary. A provider POI is a candidate only:
confirmation does not promote it to a fact, entrance, route, or engine input.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from datetime import date
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from . import facts
from .engine import amap
from .engine.storage import load_operations
from .schema import TZ, EntityId
from ..memory.storage import now
from .engine.context import PlanningContext
from .engine.models import Requirements
from .engine.travel import TravelTimeService
from .engine.tools import plan_trip
from ..db import connect


class ResolutionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=80)

    @field_validator('query', mode='before')
    @classmethod
    def trim(cls, value):
        if not isinstance(value, str):
            return value
        return value.strip()


class ResolutionConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: str = Field(min_length=1, max_length=160)


class SampleAssessmentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    origin_fact_id: EntityId
    destination_fact_id: EntityId
    origin_entrance_id: EntityId | None = None
    destination_entrance_id: EntityId | None = None
    early_time_confirmed: bool = False
    business_date: date
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    mode: str = Field(default='walking', pattern=r'^(walking|driving|transit)$')

    @model_validator(mode='after')
    def valid_window(self):
        if self.ends_at <= self.starts_at:
            raise ValueError('结束时间必须晚于开始时间')
        if self.starts_at.astimezone(TZ).date() != self.business_date or self.ends_at.astimezone(TZ).date() != self.business_date:
            raise ValueError('首版只接受上海时区同一业务日')
        return self


@dataclass(frozen=True)
class ResolutionCandidate:
    candidate_id: str
    poi_id: str
    name: str
    address: str | None
    longitude: float
    latitude: float
    citycode: str | None
    category: str | None
    children: tuple
    entrance_status: str = 'needs_entrance_verification'
    fact_status: str = 'provider_candidate_only'

    @staticmethod
    def _safe_children(children):
        """Expose only stable child identity fields, never the provider payload."""
        output = []
        for child in children:
            if not isinstance(child, dict):
                continue
            item = {}
            for key in ('id', 'name', 'address', 'location', 'type'):
                value = child.get(key)
                if isinstance(value, str) and len(value) <= 300:
                    item[key] = value
            if item:
                output.append(item)
        return output

    def public(self):
        return {
            'candidate_id': self.candidate_id,
            'poi_id': self.poi_id,
            'name': self.name,
            'address': self.address,
            'longitude': self.longitude,
            'latitude': self.latitude,
            'citycode': self.citycode,
            'category': self.category,
            'children': self._safe_children(self.children),
            'entrance_status': self.entrance_status,
            'fact_status': self.fact_status,
        }


@dataclass
class _Resolution:
    resolution_id: str
    session_id: str
    query: str
    candidates: tuple[ResolutionCandidate, ...]
    created_at: float


class ResolutionStore:
    def __init__(self, ttl_seconds: int = 900, max_items: int = 512):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._items: dict[str, _Resolution] = {}
        self._lock = Lock()

    def _purge(self):
        cutoff = time.monotonic() - self.ttl_seconds
        for key, value in list(self._items.items()):
            if value.created_at < cutoff:
                self._items.pop(key, None)

    def put(self, session_id: str, query: str, pois: tuple[amap.Poi, ...]):
        resolution_id = str(uuid4())
        candidates = tuple(ResolutionCandidate(
            candidate_id=hashlib.sha256(f'{resolution_id}:{poi.poi_id}'.encode()).hexdigest()[:32],
            poi_id=poi.poi_id, name=poi.name, address=poi.address, longitude=poi.longitude,
            latitude=poi.latitude, citycode=poi.citycode, category=poi.category,
            children=poi.children,
        ) for poi in pois)
        with self._lock:
            self._purge()
            if len(self._items) >= self.max_items:
                self._items.pop(next(iter(self._items)))
            self._items[resolution_id] = _Resolution(resolution_id, session_id, query, candidates, time.monotonic())
        return resolution_id, candidates

    def purge_session(self, session_id: str):
        with self._lock:
            for key, item in list(self._items.items()):
                if item.session_id == session_id:
                    self._items.pop(key, None)

    def confirm(self, session_id: str, resolution_id: str, candidate_id: str):
        with self._lock:
            self._purge()
            item = self._items.get(resolution_id)
            if not item or item.session_id != session_id:
                raise HTTPException(404, detail='地点候选已失效，请重新搜索。')
            candidate = next((x for x in item.candidates if x.candidate_id == candidate_id), None)
            if not candidate:
                raise HTTPException(404, detail='未找到所选地点候选，请重新搜索。')
            return item, candidate


STORE = ResolutionStore()


def provider_from_environment():
    """POI-only opt-in; this flag never enables the formal traffic provider."""
    enabled = os.getenv('AMAP_LOCATION_SEARCH_ENABLED', '').lower() in {'1', 'true', 'yes'}
    reviewed = os.getenv('AMAP_TERMS_REVIEWED', '').lower() in {'1', 'true', 'yes'}
    return amap.AmapProvider.from_environment(
        enabled=enabled, terms_reviewed=reviewed,
        api_version=os.getenv('AMAP_LOCATION_POI_API_VERSION', 'v5'), retries=0,
    )


def search(session_id: str, query: str):
    provider = provider_from_environment()
    try:
        result = provider.search_pois(query, version=provider.api_version)
        if result.status != 'found':
            return {'status': 'needs_data', 'resolution_id': None, 'query': query,
                    'provider_status': result.status, 'candidates': [],
                    'provider_diagnostics': {'http_attempts': provider.request_count, 'events': list(provider.events)},
                    'formal_traffic_provider': 'disabled'}
        resolution_id, candidates = STORE.put(session_id, query, result.pois)
        return {'status': 'candidates', 'resolution_id': resolution_id, 'query': query,
                'provider_status': result.status, 'rejected_count': result.rejected_count,
                'provider_diagnostics': {'http_attempts': provider.request_count, 'events': list(provider.events)},
                'candidates': [candidate.public() for candidate in candidates],
                'requires_user_confirmation': True, 'expires_in_seconds': STORE.ttl_seconds,
                'coordinate_system': 'GCJ02',
                'formal_traffic_provider': 'disabled'}
    finally:
        provider.close()


def confirm(session_id: str, resolution_id: str, candidate_id: str):
    item, candidate = STORE.confirm(session_id, resolution_id, candidate_id)
    # A curated maintenance binding is the only bridge from provider identity to
    # existing facts. Never infer a binding from name, proximity, or POI children.
    formal_match = None
    with connect() as db:
        binding = db.execute("SELECT fact_kind,fact_id FROM city_object_bindings WHERE kind='amap_poi' AND object_id=?",
                             (candidate.poi_id,)).fetchone()
        if binding and binding['fact_kind'] == 'place':
            ctx = PlanningContext(facts.records(db), load_operations(db), now())
            record = ctx.facts.get(('place', binding['fact_id']))
            if ctx.ready(record, ctx.assessed_at):
                formal_match = {'fact_id': record.fact_id, 'name': record.name,
                                'basis': 'curated_amap_poi_binding_to_verified_fact'}
    return {
        'status': 'matched_fact' if formal_match else 'needs_data',
        'formal_match': formal_match,
        'resolution_id': item.resolution_id,
        'query': item.query,
        'selected': candidate.public(),
        'next_step': ('使用已核验地点 ID 进入现有规划；交通、入口和旅行日期仍由既有引擎重新检查。' if formal_match else
                      '由维护者核验真实入口、开放规则、来源权限并导入 city_facts；当前选择不会写入事实库或路线。'),
        'formal_fact_status': candidate.fact_status,
        'formal_traffic_provider': 'disabled',
    }


def assess_sample(body: SampleAssessmentInput):
    """Read-only station-exit -> scenic-entry transfer, through the existing engine.

    This deliberately checks only transfer to a verified entrance, not a full
    park visit or internal navigation. No candidate, fact or route is persisted.
    """
    with connect() as db:
        ctx = PlanningContext(facts.records(db), load_operations(db), now())
    items = ctx.facts
    issues = []
    starts, ends = body.starts_at.astimezone(TZ), body.ends_at.astimezone(TZ)

    def issue(code, message, identity):
        issues.append({'code': code, 'message': message, 'id': identity})

    def ready(record):
        return ctx.ready(record, starts, ends)

    if starts <= ctx.assessed_at:
        issue('DEPARTURE_NOT_FUTURE', '出发时刻已过，请重新确认旅行日期和时间。', 'starts_at')
    if starts.hour < 6 and not body.early_time_confirmed:
        issue('EARLY_TIME_CONFIRMATION_REQUIRED', '开始时间在凌晨，请确认这是你的实际出发时间。', 'starts_at')

    origin = items.get(('place', body.origin_fact_id))
    destination = items.get(('place', body.destination_fact_id))
    for role, record, identity in (('ORIGIN', origin, body.origin_fact_id),
                                    ('DESTINATION', destination, body.destination_fact_id)):
        if record is None:
            issue(role + '_FACT_MISSING', '出发地点或目的地尚未进入正式事实库。', identity)
        elif not ready(record):
            issue(role + '_FACT_NOT_READY', '地点或其来源的坐标、许可、核验及有效期未通过检查。', identity)

    def entrance(place, explicit_id, direction, role):
        if place is None:
            return None
        relations = [r for r in items.values() if isinstance(r, facts.Entrance)
                     and r.place_id == place.fact_id and r.direction in (direction, 'both')]
        if explicit_id:
            relations = [r for r in relations if r.entrance_place_id == explicit_id]
        if len(relations) != 1:
            issue(role + '_ENTRANCE_UNRESOLVED',
                  '请核验并明确选择一个车站出口或景区入园入口；POI中心不能替代入口。', place.fact_id)
            return None
        relation = relations[0]
        gate = items.get(('place', relation.entrance_place_id))
        if not ready(relation) or not ready(gate):
            issue(role + '_ENTRANCE_NOT_READY', '出入口关系、入口地点或其来源未通过核验。', relation.fact_id)
            return None
        return gate

    origin_gate = entrance(origin, body.origin_entrance_id, 'exit', 'ORIGIN')
    destination_gate = entrance(destination, body.destination_entrance_id, 'entry', 'DESTINATION')

    # Only one explicit daily window is accepted. Unknown access rules are not defaults.
    if destination:
        rules = ctx.rules(destination.fact_id, body.business_date)
        if len(rules) != 1:
            issue('OPEN_RULE_MISSING_OR_AMBIGUOUS', '该日开放时间尚未核验，或存在多个未消解的开放时段。', destination.fact_id)
        else:
            rule = rules[0]
            if not ready(rule) or not rule.opens_at or not rule.closes_at or not rule.last_entry_at:
                issue('OPEN_RULE_NOT_READY', '需补齐适用该日的开放时间、停止入场时间及来源有效期。', rule.fact_id)
            if rule.reentry == 'unknown' or rule.reservation == 'unknown' or rule.reservation == 'required' or rule.ticket_scope:
                issue('ACCESS_RULE_NEEDS_CONFIRMATION', '预约、票种或再入园规则尚未明确；本样例不代游客确认票务资格。', rule.fact_id)

    result = None
    requirements = None
    if origin_gate and destination_gate:
        requirements = Requirements(business_date=body.business_date, starts_at=starts, ends_at=ends,
            origin_place_id=origin_gate.fact_id, destination_place_id=destination_gate.fact_id,
            goals=(), modes=(body.mode,), alternatives=1)
        journeys = [j for j in ctx.operations.journeys
                    if (j.travel.origin_place_id, j.travel.destination_place_id, j.travel.mode)
                    == (origin_gate.fact_id, destination_gate.fact_id, body.mode)]
        service = TravelTimeService(ctx)
        # Match engine conditions, but never admit a point ETA as a horizon envelope.
        usable = [j for j in journeys if service.usable(j, requirements)
                  and j.time_basis == 'survey_envelope']
        if not journeys:
            issue('TRAVEL_RECORD_MISSING', '缺少选定出口到入园入口的有方向交通记录。', origin_gate.fact_id + '->' + destination_gate.fact_id)
        elif not usable:
            issue('TRAVEL_RECORD_NOT_VALID', '交通路径、授权来源、入园规则、边界耗时或证据未覆盖当前时刻和整个规划时段。', origin_gate.fact_id + '->' + destination_gate.fact_id)
        if not issues:
            result = plan_trip(requirements, ctx)
            issues.extend({'code': i.code, 'message': i.message, 'id': ','.join(i.affected_ids)} for i in result.issues)
            # Transfer-only endpoints are not visit activities: check opening on arrival explicitly.
            if result.status == 'feasible':
                for plan in result.itineraries:
                    for pid in (destination.fact_id, destination_gate.fact_id):
                        for rule in ctx.rules(pid, body.business_date):
                            if (not ready(rule) or not rule.opens_at or not rule.closes_at
                                or not rule.opens_at <= plan.returns_at < rule.closes_at
                                or rule.last_entry_at and plan.returns_at > rule.last_entry_at):
                                issue('ARRIVAL_OUTSIDE_OPENING', '到达入口时尚未开放、已停止入场或规则无效。', pid)
    elif not any(i['code'].startswith('TRAVEL_') for i in issues):
        issue('TRAVEL_ENDPOINTS_UNRESOLVED', '需先确定两端真实出入口，才能核验交通记录。', 'entrances')

    return {
        'status': ('transfer_preview' if result and result.status == 'feasible' and not issues else
                   'infeasible' if result and result.status == 'infeasible' else 'needs_data'),
        'sample': 'kaifeng_station_to_qingming_riverside_garden',
        'scope': 'station_exit_to_scenic_entry_only',
        'origin_fact_id': body.origin_fact_id, 'destination_fact_id': body.destination_fact_id,
        'matched_origin_entrance_id': origin_gate.fact_id if origin_gate else None,
        'matched_destination_entrance_id': destination_gate.fact_id if destination_gate else None,
        'time_input': {'business_date': body.business_date.isoformat(),
                       'starts_at': body.starts_at.isoformat(), 'ends_at': body.ends_at.isoformat(),
                       'timezone': 'Asia/Shanghai', 'normalized_starts_at': starts.isoformat(),
                       'normalized_ends_at': ends.isoformat(), 'early_time_confirmed': body.early_time_confirmed},
        'dynamic_traffic_provider': 'disabled_formal_gate', 'issues': issues,
        'engine': 'existing_or_tools_chain_invoked' if result else 'existing_or_tools_chain_not_invoked',
        'engine_status': result.status if result else None,
        'engine_metrics': result.metrics if result else None,
        'requirements': requirements.model_dump(mode='json') if requirements else None,
        'transfer_preview': result.model_dump(mode='json') if result and not issues else None,
        'formal_confirmation': 'existing_trip_engine_preview_then_explicit_confirm_required',
        'not_assessed': ['station_internal_route', 'park_internal_route', 'full_city_itinerary'],
        'writes': [],
    }
