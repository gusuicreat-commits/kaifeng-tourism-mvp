#!/usr/bin/env python3
"""Generate dated reference opening rules from AMap weekly business hours.

Dry-run by default. --apply backs up the database, imports the generated
city-facts/1 records and records explicit AMap POI bindings. Rules stay
reference/unverified; an existing rule for the same place and date wins.
"""
import argparse
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
load_dotenv(ROOT / '.env')
from app.city import facts
from app.city.engine.live_travel import to_gcj02
from app.city.schema import TZ

SOURCE_ID = 'src_amap_poi_business'
NOTE = '高德地点每周营业时间生成的参考规则；未人工核验，节假日、临时调整及闭园以现场为准。'
DAYS = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
TIME = r'\d{1,2}:\d{2}'
MAX_DISTANCE_M = 300


def distance_m(a, b):
    """Equirectangular approximation; adequate for a few hundred metres."""
    x = math.radians(b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    return 6371000 * math.hypot(x, math.radians(b[1] - a[1]))


def clock(value):
    h, m = map(int, value.split(':'))
    return (23, 59) if (h, m) == (24, 0) else (h, m)


def _strip_holiday_overrides(text):
    """Remove dated holiday overrides before reading the ordinary rule."""
    return re.sub(rf'(?:\d{{4}}-)?\d{{1,2}}-\d{{1,2}}至(?:\d{{4}}-)?\d{{1,2}}-\d{{1,2}}\s*(?:周[一二三四五六日天](?:至周[一二三四五六日天])?\s*)?(?:{TIME}-{TIME}[,，、\s]*)+', '', text)


def _last_entry(text):
    last = re.search(rf'(?:最晚进入|停止入园|最晚入园)\s*({TIME})|({TIME})\s*(?:停止入园|停止检票)', text)
    return clock(next(v for v in last.groups() if v)) if last else None


def _windows(text):
    windows = []
    for o, c in re.findall(rf'({TIME})\s*[-–]\s*(?:次日)?({TIME})', text):
        o, c = clock(o), clock(c)
        # Same-date rules only: an after-midnight close is clamped to 23:59.
        windows.append((o, c if c > o else (23, 59)))
    return windows


def _date_range(text):
    match = re.search(r'(\d{1,2})月(\d{1,2})日\s*至\s*(\d{1,2})月(\d{1,2})日', text)
    if match:
        return tuple(map(int, match.groups()))
    match = re.search(r'(\d{1,2})\s*/\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\s*/\s*(\d{1,2})', text)
    return tuple(map(int, match.groups())) if match else None


def _date_in_range(day, span):
    if not span:
        return False
    sm, sd, em, ed = span
    current, start, end = (day.month, day.day), (sm, sd), (em, ed)
    return start <= current <= end if start <= end else current >= start or current <= end


def _season_matches(name, day):
    month = day.month
    if name == '夏令时':
        return 5 <= month <= 9
    if name == '冬令时':
        return month >= 10 or month <= 4
    if name == '夏季':
        return 6 <= month <= 8
    if name == '冬季':
        return month in (12, 1, 2)
    if name == '春秋季':
        return month in (3, 4, 5, 9, 10, 11)
    return False


def _season_name(text):
    match = re.search(r'(夏令时|冬令时|夏季|冬季|春秋季)', text)
    return match.group(1) if match else None


def _parse_day_detail(text, day):
    """Return (windows, last_entry, assumed_season) for one business date."""
    if not text:
        return None, 'no_hours', None
    text = text.replace('：', ':')
    if '24小时' in text:
        return [((0, 0), (23, 59))], None, None

    text = _strip_holiday_overrides(text)
    parts = re.split(r'[；;]', text)
    seasonal_parts = []
    for part in parts:
        name = _season_name(part)
        if name:
            seasonal_parts.append((name, part))
    if seasonal_parts:
        for name, part in seasonal_parts:
            if _season_matches(name, day):
                windows = _windows(part)
                if windows:
                    return windows, _last_entry(part), name
        return None, 'seasonal_no_match', None

    date_parts = [part for part in parts if _date_range(part)]
    if date_parts:
        for part in date_parts:
            if _date_in_range(day, _date_range(part)):
                windows = _windows(part)
                if windows:
                    return windows, _last_entry(part), None
        return None, 'seasonal_no_match', None

    # Weekday rules retain parse_week's existing semantics. Normalize the
    # additional dash spelling only for this dated entry point.
    week, last = parse_week(text.replace('–', '-'))
    if week:
        return week.get(day.weekday(), []), last, None

    # A plain interval with no weekday prefix is a daily rule.
    windows = _windows(text)
    if windows:
        return windows, _last_entry(text), None
    return None, 'unparsed', None


def parse_day(text, day):
    """Return (windows, last_entry) applicable to ``day`` or (None, reason)."""
    windows, last, _ = _parse_day_detail(text, day)
    return (windows, last) if windows is not None else (None, last)


def _season_assumption(text, day):
    if not text:
        return None
    parts = re.split(r'[；;]', _strip_holiday_overrides(text.replace('：', ':')))
    for part in parts:
        name = _season_name(part)
        if name and _season_matches(name, day) and _windows(part):
            return name
    return None


def parse_week(text):
    """Return ({weekday: [(open, close)]}, last_entry) or (None, reason)."""
    if not text:
        return None, 'no_hours'
    if re.search(r'\d{1,2}月\d{1,2}日', text):
        return None, 'seasonal_text'
    # Drop holiday overrides such as "10-01至10-07 周一至周日 ..."; dates outside them use the weekly rule.
    text = _strip_holiday_overrides(text)
    if '24小时' in text:
        return {d: [((0, 0), (23, 59))] for d in range(7)}, None
    last = re.search(rf'(?:最晚进入|停止入园|最晚入园)\s*({TIME})|({TIME})\s*(?:停止入园|停止检票)', text)
    week = {}
    for days, times in re.findall(rf'(周[一二三四五六日天](?:至周[一二三四五六日天])?|每天|全天)\s*((?:{TIME}-(?:次日)?{TIME}[,，、\s]*)+)', text):
        if days in ('每天', '全天'):
            span = range(7)
        elif '至' in days:
            a, b = DAYS[days[1]], DAYS[days[-1]]
            span = range(a, b + 1) if a <= b else [*range(a, 7), *range(0, b + 1)]
        else:
            span = [DAYS[days[1]]]
        windows = []
        for o, c in re.findall(rf'({TIME})-(?:次日)?({TIME})', times):
            o, c = clock(o), clock(c)
            # Same-date rules only: an after-midnight close is clamped to 23:59.
            windows.append((o, c if c > o else (23, 59)))
        for d in span:
            week[d] = windows
    if not week:
        return None, 'unparsed'
    return week, (clock(next(v for v in last.groups() if v)) if last else None)


class Amap:
    def __init__(self):
        self.key = os.getenv('AMAP_WEB_SERVICE_KEY', '').strip()
        if not self.key:
            raise SystemExit('AMAP_WEB_SERVICE_KEY 未配置')
        self.client = httpx.Client(base_url='https://restapi.amap.com', timeout=8)
        self.calls = 0

    def get(self, path, **params):
        if self.calls:
            time.sleep(0.35)
        self.calls += 1
        data = self.client.get(path, params={'key': self.key, 'show_fields': 'business', **params}).json()
        if data.get('status') != '1':
            raise RuntimeError(f"AMap {path}: {data.get('info')}")
        return data.get('pois', [])

    def detail(self, poi_id):
        pois = self.get('/v5/place/detail', id=poi_id)
        return pois[0] if pois else None

    def search(self, name):
        return self.get('/v5/place/text', keywords=name, region='410200', city_limit='true', page_size=5)


def location(poi):
    lon, lat = map(float, poi['location'].split(','))
    return lon, lat


def resolve(place, bound, amap):
    """POI id from binding/external id, else an exact-name search hit within MAX_DISTANCE_M."""
    poi_id = bound.get(place.fact_id) or place.external_poi_id
    if poi_id:
        poi = amap.detail(poi_id)
        return (poi, 'binding') if poi else (None, 'binding_not_found')
    here = to_gcj02(place)
    if here.longitude is None or here.coordinate_system != 'GCJ02':
        return None, 'no_coordinates'
    names = {place.name, place.name.replace('（', '(').replace('）', ')')}
    for poi in amap.search(place.name):
        if poi['name'] in names and distance_m((here.longitude, here.latitude), location(poi)) <= MAX_DISTANCE_M:
            return poi, 'exact_name_nearby'
    return None, 'no_exact_match'


def moment(day, hm):
    return datetime(day.year, day.month, day.day, *hm, tzinfo=TZ)


def build(catalog, bound, explicit, days, amap):
    now = datetime.now(TZ)
    records, report, bindings = [], [], {}
    existing = {(r.place_id, r.business_date) for r in catalog.values() if isinstance(r, facts.OpenRule)}
    source = facts.Source(kind='source', fact_id=SOURCE_ID, name='高德地点营业时间', source_type='map_service',
                          url='https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch',
                          locator='AMap Web Service v5 place/detail show_fields=business', publisher='高德地图',
                          collected_at=now, rights_status='internal_only', planning_status='reference', planning_note=NOTE)
    old = catalog.get(('source', SOURCE_ID))
    if old:
        source = source.model_copy(update={'version': old.version + 1})
    records.append(source)
    places = [p for p in catalog.values() if isinstance(p, facts.Place) and not p.superseded_by
              and (p.latitude is not None or p.fact_id in explicit)]
    for place in sorted(places, key=lambda p: p.fact_id):
        row = {'place_id': place.fact_id, 'name': place.name}
        try:
            if place.fact_id in explicit:
                poi, row['match'] = amap.detail(explicit[place.fact_id]), 'explicit'
            else:
                poi, row['match'] = resolve(place, bound, amap)
        except (RuntimeError, httpx.HTTPError) as exc:
            poi, row['match'] = None, f'error: {exc}'
        if not poi:
            report.append(row)
            continue
        hours = poi.get('business', {}).get('opentime_week')
        row.update(poi_id=poi['id'], poi_name=poi['name'], hours=hours)
        if re.search(r'暂停营业|已关闭|停业|歇业', poi['name']):
            row['skipped'] = 'poi_marked_suspended'
            report.append(row)
            continue
        if place.fact_id in explicit:
            lon, lat = location(poi)
            records.append(place.model_copy(update={
                'version': place.version + 1, 'longitude': lon, 'latitude': lat, 'coordinate_system': 'GCJ02',
                'external_poi_id': poi['id'], 'source_ids': tuple(dict.fromkeys((*place.source_ids, SOURCE_ID)))}))
            row['coordinates'] = poi['location']
        if row['match'] in ('explicit', 'exact_name_nearby'):
            bindings[poi['id']] = place.fact_id
        made = 0
        reasons = []
        assumed = set()
        for day in days:
            windows, last = parse_day(hours, day)
            season = _season_assumption(hours, day) if windows is not None else None
            if windows is None:
                reasons.append(last)
                continue
            if (place.fact_id, day) in existing or not windows:
                continue
            for i, (o, c) in enumerate(windows, 1):
                entry = last if last and len(windows) == 1 and o <= last <= c else None
                suffix = f'_{i}' if len(windows) > 1 else ''
                fact_id = f'open_amap_{place.fact_id}_{day:%Y%m%d}{suffix}'
                before = catalog.get(('open_rule', fact_id))
                planning_note = NOTE
                if season:
                    assumed.add(season)
                    planning_note += '季节按通用划分推算，以景区公告为准。'
                records.append(facts.OpenRule(
                    kind='open_rule', fact_id=fact_id, version=before.version + 1 if before else 1,
                    place_id=place.fact_id, business_date=day, opens_at=moment(day, o), closes_at=moment(day, c),
                    last_entry_at=moment(day, entry) if entry else None, source_ids=(SOURCE_ID,),
                    rights_status='internal_only', planning_status='reference', planning_note=planning_note))
                made += 1
        if assumed:
            row['season_assumed'] = next(iter(assumed)) if len(assumed) == 1 else sorted(assumed)
        if not made and reasons:
            row['skipped'] = reasons[0]
        row['rules'] = made
        report.append(row)
    return records, report, bindings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    today = datetime.now(TZ).date()
    parser.add_argument('--from', dest='start', type=date.fromisoformat, default=today)
    parser.add_argument('--to', dest='end', type=date.fromisoformat, default=today + timedelta(days=14))
    parser.add_argument('--bind', action='append', default=[], metavar='PLACE_ID=POI_ID',
                        help='显式绑定高德地点并补坐标，例如 pl_wb_000001=B017A00OOX')
    parser.add_argument('--database', type=Path, default=Path(os.getenv('DATABASE_PATH', ROOT / 'data/tourism.sqlite3')))
    parser.add_argument('--apply', action='store_true', help='备份后写入数据库；默认只预演')
    args = parser.parse_args()
    if args.end < args.start or (args.end - args.start).days > 31:
        parser.error('日期范围须在 0–31 天内')
    explicit = dict(item.split('=', 1) for item in args.bind)
    days = [args.start + timedelta(days=i) for i in range((args.end - args.start).days + 1)]
    with sqlite3.connect(f'file:{args.database.resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        catalog = facts.records(db)
        bound = {r['fact_id']: r['object_id'] for r in db.execute(
            "SELECT object_id,fact_id FROM city_object_bindings WHERE kind='amap_poi' AND fact_kind='place'")}
    amap = Amap()
    records, report, bindings = build(catalog, bound, explicit, days, amap)
    bundle = facts.FactBundle(records=tuple(records))
    out = ROOT / f'data/mvp/amap-hours-{args.start:%Y%m%d}-{args.end:%Y%m%d}.json'
    out.write_text(bundle.model_dump_json(indent=1))
    rules = sum(r.kind == 'open_rule' for r in records)
    print(json.dumps({'places': report, 'open_rules': rules, 'new_bindings': bindings,
                      'amap_calls': amap.calls, 'bundle': str(out.relative_to(ROOT))}, ensure_ascii=False, indent=1))
    if not args.apply:
        print('\n预演完成，未写入数据库；确认后加 --apply。')
        return 0
    backup = args.database.with_name(f'{args.database.name}.bak-{datetime.now(TZ):%Y%m%d%H%M%S}')
    shutil.copy2(args.database, backup)
    with sqlite3.connect(args.database, timeout=15) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        result = facts.import_bundle(bundle, db, apply=True)
        for poi_id, fact_id in bindings.items():
            db.execute("INSERT OR REPLACE INTO city_object_bindings VALUES ('amap_poi',?,'place',?)", (poi_id, fact_id))
    print(f"\n已写入：{result['changed']} 条变更；备份 {backup.name}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
