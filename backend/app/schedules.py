"""Date-specific, source-backed show/opening facts. Imported by a local maintenance CLI."""
import json
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from .db import connect, network_get

TZ = ZoneInfo('Asia/Shanghai')


class ScheduleFact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=120)
    scenic_id: str
    node_id: str | None = None  # null means whole-site opening window
    kind: Literal['show', 'opening']
    name: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    ends_at: datetime
    valid_date: date
    source_id: str = Field(min_length=1, max_length=120)
    source_url: HttpUrl
    publisher: str = Field(min_length=1, max_length=200)
    collected_at: datetime
    verified: bool
    is_simulated: bool

    @model_validator(mode='after')
    def validate_fact(self):
        for key in ('starts_at', 'ends_at', 'collected_at'):
            value = getattr(self, key)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError('日程时间必须包含时区')
            setattr(self, key, value.astimezone(TZ))
        if self.ends_at <= self.starts_at:
            raise ValueError('结束时间必须晚于开始时间')
        if self.starts_at.date() != self.valid_date or self.ends_at.date() != self.valid_date:
            raise ValueError('日程必须属于 valid_date；跨午夜需拆分')
        if self.kind == 'show' and not self.node_id:
            raise ValueError('演出必须关联真实 node_id')
        return self


def store_facts(records):
    """Validate the entire batch before writing; never modify scenic/network/observation data."""
    if not 1 <= len(records) <= 1000:
        raise ValueError('日程批次需为 1–1000 条')
    items = [ScheduleFact.model_validate(r) for r in records]
    if len({i.id for i in items}) != len(items):
        raise ValueError('批次存在重复日程 ID')
    for item in items:
        net = network_get(item.scenic_id)
        nodes = {f['properties']['id'] for f in net['features'] if f['geometry']['type'] == 'Point'} if net else set()
        if not net or (item.node_id and item.node_id not in nodes):
            raise ValueError('日程景区或点位不在现有路网中')
        if item.collected_at > datetime.now(TZ):
            raise ValueError('采集时间不能在未来')
    with connect() as db:
        for item in items:
            old = db.execute('SELECT scenic_id FROM schedule_facts WHERE id=?', (item.id,)).fetchone()
            if old and old['scenic_id'] != item.scenic_id:
                raise ValueError('日程 ID 已属于其他景区')
            db.execute('INSERT INTO schedule_facts VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET content=excluded.content',
                       (item.id, item.scenic_id, item.model_dump_json()))
    return {'stored': len(items)}


def facts_for(sid, visit_date, dataset='reported'):
    with connect() as db:
        rows = db.execute('SELECT content FROM schedule_facts WHERE scenic_id=? ORDER BY id', (sid,)).fetchall()
    facts = [ScheduleFact.model_validate_json(r['content']) for r in rows]
    # Explicit demo mode may consume demo facts. Tourist requests never consume them.
    return [f for f in facts if f.valid_date == visit_date and f.verified
            and (dataset == 'demo' or not f.is_simulated)]
