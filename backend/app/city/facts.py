"""Versioned fact catalog. Maintenance imports are atomic and never exposed as HTTP writes."""
import json
from datetime import date
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import AwareDatetime, Field, HttpUrl, TypeAdapter, model_validator

from ..db import connect
from ..memory.storage import encoded, now, stamp
from .schema import EntityId, Strict, TZ


class Fact(Strict):
    fact_id: EntityId
    version: int = Field(default=1, ge=1)
    source_ids: tuple[EntityId, ...] = Field(default=(), max_length=20)
    validation_status: Literal['unverified', 'verified'] = 'unverified'
    business_status: Literal['active', 'cancelled'] = 'active'
    is_demo: bool = False
    verified_at: AwareDatetime | None = None
    reviewer: str | None = Field(default=None, min_length=1, max_length=100)
    verification_method: str | None = Field(default=None, min_length=1, max_length=300)
    observed_at: AwareDatetime | None = None
    effective_from: AwareDatetime | None = None
    effective_to: AwareDatetime | None = None
    rights_status: Literal['permitted', 'internal_only', 'unknown', 'prohibited'] = 'unknown'

    @model_validator(mode='after')
    def verify_metadata(self):
        if self.effective_from and self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError('事实有效区间无效')
        if self.verified_at and self.verified_at > now():
            raise ValueError('核验时间不能来自未来')
        if self.validation_status == 'verified':
            if not self.verified_at or not self.reviewer or not self.verification_method or not self.effective_from or not self.effective_to:
                raise ValueError('核验事实必须记录核验人、方法、核验时间及有效区间')
        if self.observed_at and self.observed_at > now():raise ValueError('观测时间不能来自未来')
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError('来源不可重复')
        return self


class Source(Fact):
    kind: Literal['source']
    name: str = Field(min_length=1, max_length=200)
    source_type: Literal['official', 'operator', 'field', 'map_service', 'other']
    url: HttpUrl | None = None
    locator: str | None = Field(default=None, min_length=1, max_length=500)
    publisher: str | None = Field(default=None, min_length=1, max_length=200)
    collected_at: AwareDatetime
    published_at: AwareDatetime | None = None


class Place(Fact):
    kind: Literal['place']
    name: str = Field(min_length=1, max_length=120)
    place_type: Literal['scenic', 'poi', 'stage', 'entrance', 'restaurant', 'night_visit', 'transport', 'rest']
    parent_place_id: EntityId | None = None
    address: str | None = Field(default=None, max_length=300)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    coordinate_system: Literal['WGS84', 'GCJ02', 'local_demo', 'unknown'] = 'unknown'

    @model_validator(mode='after')
    def coordinates(self):
        if (self.longitude is None) != (self.latitude is None):
            raise ValueError('经纬度必须成对提供')
        if self.longitude is not None and self.coordinate_system == 'unknown':
            raise ValueError('坐标必须标明坐标系')
        if self.coordinate_system == 'local_demo' and not self.is_demo:
            raise ValueError('示意坐标必须标记演示')
        return self


class Entrance(Fact):
    kind: Literal['entrance']
    place_id: EntityId
    entrance_place_id: EntityId
    direction: Literal['entry', 'exit', 'both']


class OpenRule(Fact):
    kind: Literal['open_rule']
    place_id: EntityId
    business_date: date
    opens_at: AwareDatetime | None = None
    closes_at: AwareDatetime | None = None
    last_entry_at: AwareDatetime | None = None
    reentry: Literal['allowed', 'forbidden', 'unknown'] = 'unknown'
    reservation: Literal['required', 'not_required', 'unknown'] = 'unknown'
    ticket_scope: str | None = Field(default=None, max_length=200)

    @model_validator(mode='after')
    def times(self):
        if (self.opens_at is None) != (self.closes_at is None):
            raise ValueError('营业起止必须完整或同时未知')
        if self.opens_at and self.closes_at <= self.opens_at:
            raise ValueError('营业区间无效')
        for value in (self.opens_at, self.closes_at, self.last_entry_at):
            if value and value.astimezone(TZ).date() != self.business_date:
                raise ValueError('营业时间必须属于明确的业务日期，跨日请拆分')
        if self.last_entry_at and self.closes_at and not self.opens_at <= self.last_entry_at <= self.closes_at:
            raise ValueError('停止入园时间不在营业区间')
        return self


class Program(Fact):
    kind: Literal['program']
    name: str = Field(min_length=1, max_length=120)


class ShowSession(Fact):
    kind: Literal['show_session']
    program_id: EntityId
    stage_place_id: EntityId
    business_date: date
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    admission_deadline: AwareDatetime | None = None

    @model_validator(mode='after')
    def times(self):
        if self.starts_at.astimezone(TZ).date() != self.business_date:
            raise ValueError('开演必须属于明确的演出日期，不能用采集日代替')
        if self.ends_at and (self.ends_at <= self.starts_at or self.ends_at.astimezone(TZ).date() != self.business_date):
            raise ValueError('演出结束时间无效，首版不支持跨日')
        if self.admission_deadline and self.admission_deadline > self.starts_at:
            raise ValueError('停止入场不能晚于开演')
        return self


class Food(Fact):
    kind: Literal['food']
    name: str = Field(min_length=1, max_length=120)


class Vendor(Fact):
    kind: Literal['vendor']
    place_id: EntityId


class Supply(Fact):
    kind: Literal['supply']
    vendor_id: EntityId
    food_id: EntityId
    evidence_note: str = Field(min_length=1, max_length=500)


Record = Annotated[Source | Place | Entrance | OpenRule | Program | ShowSession | Food | Vendor | Supply,
                   Field(discriminator='kind')]
RECORD = TypeAdapter(Record)


class FactBundle(Strict):
    schema_version: Literal['city-facts/1'] = 'city-facts/1'
    records: tuple[Record, ...] = Field(max_length=2000)


def init_catalog(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS city_facts (
      kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL, record_json TEXT NOT NULL,
      PRIMARY KEY(kind,id)
    );
    CREATE TABLE IF NOT EXISTS city_fact_revisions (
      kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL, record_json TEXT NOT NULL,
      imported_at TEXT NOT NULL, PRIMARY KEY(kind,id,version)
    );
    ''')


def records(db):
    return {(r['kind'], r['id']): RECORD.validate_json(r['record_json']) for r in db.execute('SELECT * FROM city_facts')}


def references(item):
    refs = [('source', sid) for sid in item.source_ids]
    if isinstance(item, Place) and item.parent_place_id:
        refs.append(('place', item.parent_place_id))
    if isinstance(item, Entrance): refs += [('place', item.place_id), ('place', item.entrance_place_id)]
    if isinstance(item, (OpenRule, Vendor)): refs.append(('place', item.place_id))
    if isinstance(item, ShowSession): refs += [('program', item.program_id), ('place', item.stage_place_id)]
    if isinstance(item, Supply): refs += [('vendor', item.vendor_id), ('food', item.food_id)]
    return refs


def check_links(items):
    for key, item in items.items():
        for ref in references(item):
            if ref not in items:
                raise ValueError(f'{key[0]}:{key[1]} 引用不存在：{ref[0]}:{ref[1]}')
            if ref == key: raise ValueError('事实不能引用自身')
        if not isinstance(item, Source) and item.validation_status == 'verified' and not item.source_ids:
            raise ValueError(f'{key[0]}:{key[1]} 已核验记录缺来源')
        if isinstance(item, ShowSession) and items['place', item.stage_place_id].place_type != 'stage':
            raise ValueError('场次必须关联独立舞台地点')
        if isinstance(item, Vendor) and items['place', item.place_id].place_type != 'restaurant':
            raise ValueError('商家必须关联餐饮地点')
        if isinstance(item, Entrance) and items['place', item.entrance_place_id].place_type != 'entrance':
            raise ValueError('入口关系必须关联真实入口类型')
        if isinstance(item, Source) and (item.collected_at > now() or item.source_ids):
            raise ValueError('来源采集时间不能来自未来，来源不接受嵌套引用')
    # Parent cycles would make park membership and entrance transitions ambiguous.
    for key, item in items.items():
        if not isinstance(item, Place): continue
        seen = {item.fact_id}
        while item.parent_place_id:
            if item.parent_place_id in seen: raise ValueError('地点归属存在循环')
            seen.add(item.parent_place_id)
            item = items['place', item.parent_place_id]


def gaps(item, items, moment=None):
    moment = moment or now()
    result = []
    if item.validation_status != 'verified': result.append('UNVERIFIED')
    if item.business_status != 'active': result.append('CANCELLED')
    if item.is_demo: result.append('DEMO_ONLY')
    if item.rights_status != 'permitted': result.append('RIGHTS_NOT_CONFIRMED')
    if not item.effective_from or not item.effective_to or not item.effective_from <= moment < item.effective_to:
        result.append('OUTSIDE_VALIDITY_OR_UNKNOWN')
    if isinstance(item, Place) and (item.longitude is None or item.coordinate_system in ('unknown', 'local_demo')):
        result.append('LOCATION_UNKNOWN')
    if isinstance(item, ShowSession) and item.ends_at is None: result.append('SHOW_END_UNKNOWN')
    if isinstance(item, OpenRule) and item.opens_at is None: result.append('OPENING_UNKNOWN')
    if isinstance(item, Source) and not item.url and not item.locator: result.append('SOURCE_LOCATOR_UNKNOWN')
    if isinstance(item,(ShowSession,OpenRule)) and item.business_date != moment.astimezone(TZ).date():
        result.append('BUSINESS_DATE_NOT_APPLICABLE')
    return result


def status(item, items, moment=None):
    problems = gaps(item, items, moment)
    # Dependencies are checked too: verifying a supply edge cannot verify its restaurant.
    seen = set()
    def visit(record):
        for ref in references(record):
            if ref in seen: continue
            seen.add(ref)
            dependency = items.get(ref)
            if dependency is None or gaps(dependency, items, moment):
                problems.append('DEPENDENCY_NOT_READY:' + ':'.join(ref))
            if dependency is not None: visit(dependency)
    visit(item)
    return {'ready': not problems, 'issues': list(dict.fromkeys(problems))}


def import_bundle(bundle: FactBundle, db, apply=False):
    """Caller owns the transaction. Dry-run uses the same checks and writes nothing."""
    existing = records(db)
    merged = dict(existing)
    seen = set(); changed = []
    for item in bundle.records:
        key = (item.kind, item.fact_id)
        if key in seen: raise ValueError('同一批次事实 ID 重复')
        seen.add(key)
        before = existing.get(key)
        if before and before.model_dump(mode='json') == item.model_dump(mode='json'): continue
        if item.version != (before.version + 1 if before else 1):
            raise ValueError(f'{item.kind}:{item.fact_id} 版本冲突，须从1开始逐版增加')
        merged[key] = item; changed.append(item)
    check_links(merged)
    report = {'status': 'imported' if apply else 'dry_run', 'received': len(bundle.records),
              'changed': len(changed), 'unchanged': len(bundle.records)-len(changed),
              'records': [{'kind': i.kind, 'id': i.fact_id, **status(i, merged)} for i in bundle.records]}
    if apply:
        for item in changed:
            content = encoded(item.model_dump(mode='json'))
            db.execute('INSERT OR REPLACE INTO city_facts VALUES (?,?,?,?)', (item.kind,item.fact_id,item.version,content))
            db.execute('INSERT INTO city_fact_revisions VALUES (?,?,?,?,?)', (item.kind,item.fact_id,item.version,content,stamp()))
    return report


def require(items, kind, identity):
    record = items.get((kind, identity))
    if record is None: raise HTTPException(422, detail=f'未找到已有{kind}实体：{identity}，请先通过事实入口核验或建候选。')
    return record


def public_records(kind=None):
    with connect() as db: items = records(db)
    output = []
    for item in items.values():
        if kind and item.kind != kind: continue
        if item.rights_status != 'permitted': continue
        output.append({'record': item.model_dump(mode='json'), **status(item,items)})
    return {'items': output, 'notice': '候选与可用事实分别标识；ready不代表整条行程已通过路线校验。'}
