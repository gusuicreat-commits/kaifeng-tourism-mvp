"""Merge sourced collection bundles into the explicit MVP runtime catalog.

This is an adoption boundary, not a verification shortcut. Original bundles and
verification metadata stay unchanged; imported records remain unverified unless
they already carried a supported verified status. The command defaults to a
read-only dry run and only --apply writes the explicitly selected SQLite file.
"""
import argparse
import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from app.city import facts
from scripts.install_mvp_data import build as build_route_data
from scripts.import_historical_mvp import build as build_historical

OUT = ROOT / 'data/mvp'
NOTE = '已有团队或公开来源资料，按 MVP 参考准入；不等于人工核实、实时营业、当日供应或现场通行保证。'
MANIFEST = [
    ('team-workbook', 'data/team-handoff/city-facts-teamhandoff-20261023.json'),
    ('expanded-collection', 'data/collection-20261023-expanded/city-facts-candidate-expanded.json'),
    ('initial-collection', 'data/collection-20261023/city-facts-candidate.json'),
    ('first-collector-run', 'data/collection-runs/run_1d70ae0fdd19/bundle/city-facts-candidate.json'),
    ('second-collector-run', 'data/collection-runs/run_10712c5a07e1/bundle/city-facts-candidate.json'),
    ('coordinate-backfill', 'data/collection-runs/coord_backfill_20261024/bundle/city-facts-candidate.json'),
    ('dated-opening-rules', 'data/collection-runs/open_rules_2026-10-24/bundle/city-facts-candidate.json'),
    ('scenic-alignment', 'data/collection-runs/scenic_align_20261024/bundle/city-facts-candidate.json'),
    ('initial-route-sources', 'data/route-acceptance/kaifeng-real-route-v1-20261024-facts-candidate.json'),
]
# The newer workbook already contains these relationships under the correct
# scenic. v2 reused their IDs for a different scenic, so adopting it would
# silently rewrite the Qingming entrances. Do not mix that package into facts.
EXCLUDED = [{
    'path': 'data/route-acceptance/kaifeng-real-route-v2-20261025-facts-candidate.json',
    'reason': '复用清明上河园迎宾门/金水门 ID 记录开封府入口并改写归属、坐标；保留原包，禁止覆盖。可用队员回填与高德坐标已由路线导入器采用。',
}]

# Reconcile evidence-backed duplicate collector aliases without changing source rows.
# The canonical ID is selected per duplicate group by the number of references from
# open rules, supplies, programs/sessions, entrance relations, bindings and demo
# material; ties keep the maintained workbook/collector ID.  This preserves the
# most-used identity and lets old links resolve through superseded_by.
PLACE_ALIASES = {
    'baogongci': 'pl_wb_000008',
    'daxiangguosi': 'pl_wb_000005',
    'kaifengfu': 'pl_wb_000003',
    'hanyuan': 'pl_wb_000007',
    'longting': 'pl_wb_000004',
    'qingming': 'pl_wb_000001',
    'wansuishan': 'pl_wb_000002',
    'tieta': 'pl_wb_000006',
    'tianboyangfu': 'pl_wb_000009',
    'wall': 'pl_wb_000010',
    'shanshangan': 'pl_wb_000011',
    'yanqingguan': 'pl_wb_000012',
    'gulou_night': 'pl_wb_000013',
    'heda_night': 'pl_wb_000014',
    'diyilou_sihou': 'pl_wb_vendor_000001',
    'place_diyilou_sihou': 'pl_wb_vendor_000001',
    'heyuan_jinxiang': 'place_heyuan_jinxiang',
    'place_huangjia_dalianglu': 'huangjia_daliang',
    'huangjia_xisi': 'pl_wb_vendor_000016',
    'place_huangjia_xisi': 'pl_wb_vendor_000016',
    'place_huangjia_jinanlu': 'pl_wb_vendor_000003',
    'louzhonglou_xiaosong': 'pl_wb_vendor_000004',
    'place_louzhonglou_xiaosong': 'pl_wb_vendor_000004',
    # Reviewed by Claude 2026-10-26 without address/coordinate evidence: each is the
    # only market of that name in Kaifeng and every source describes the same one,
    # so these are name identities, not near-matches. The canonical keeps the AMap
    # coordinates; place_type differences come from older collector labels.
    'place_gulou_night_market': 'pl_wb_000013',
    'xisi_night': 'pl_wb_000015',
    'place_xisi_night_market': 'pl_wb_000015',
    'xiaosongcheng': 'pl_wb_000016',
    'place_xiaosongcheng': 'pl_wb_000016',
    'place_xingjia_daliangmen': 'pl_wb_vendor_000010',
    'xingjia_daliangmen': 'pl_wb_vendor_000010',
    'place_heyuan_dingli': 'pl_wb_vendor_000012',
    'heyuan_hanxing': 'pl_wb_vendor_000024',
    'place_heyuan_hanxing': 'pl_wb_vendor_000024',
}


def _place_reference_counts(items):
    """Count all maintained fact references used to choose a canonical place."""
    counts = Counter()
    vendors = {item['fact_id']: item.get('place_id') for item in items.values()
               if item.get('kind') == 'vendor'}
    for item in items.values():
        kind = item.get('kind')
        for field in ('place_id', 'parent_place_id', 'stage_place_id', 'entrance_place_id'):
            target = item.get(field)
            if target:
                counts[target] += 1
        if kind == 'supply' and vendors.get(item.get('vendor_id')):
            counts[vendors[item['vendor_id']]] += 1
    return counts


def _rewrite_place_references(items):
    """Point dependent facts at canonical places after marking old records."""
    for item in items.values():
        for field in ('place_id', 'parent_place_id', 'stage_place_id', 'entrance_place_id'):
            target = item.get(field)
            if target in PLACE_ALIASES:
                item[field] = PLACE_ALIASES[target]


def _validate_alias_selection(items):
    counts = _place_reference_counts(items)
    for alias_id, canonical_id in PLACE_ALIASES.items():
        if alias_id in counts and counts[canonical_id] < counts[alias_id]:
            raise ValueError(f'规范地点引用数不足：{alias_id} -> {canonical_id}')


def _rewrite_place_bindings(db):
    """Move maintenance bindings to canonical facts while preserving basket keys."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'city_object_bindings' not in tables:
        return
    for alias_id, canonical_id in PLACE_ALIASES.items():
        db.execute("UPDATE city_object_bindings SET fact_id=? WHERE fact_kind='place' AND fact_id=?",
                   (canonical_id, alias_id))


def load_sources():
    path = ROOT / 'data/collection.db'
    if not path.exists():
        return {}
    with sqlite3.connect(f'file:{path.resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return {r['source_key']: dict(r) for r in db.execute('SELECT * FROM source_registry')}


def source_from_registry(item, row):
    """Keep workbook provenance; enrich opaque collector sources from its ledger."""
    if row.get('access_kind') == 'team_handoff':
        return item
    changed = dict(item)
    if not item.get('url') and row.get('url', '').startswith(('http://', 'https://')):
        changed['url'] = row['url']
    if not item.get('publisher') and row.get('publisher'):
        changed['publisher'] = row['publisher']
    if item.get('name') == item['fact_id'] and row.get('name'):
        changed['name'] = row['name']
    if not item.get('locator') or item['locator'].startswith('run:'):
        raw = row.get('raw_path')
        if raw:
            candidate = Path(raw)
            relative = candidate.relative_to(ROOT) if candidate.is_absolute() else candidate
            if (ROOT / relative).exists():
                changed['locator'] = str(relative)
        elif row.get('url', '').startswith('search://'):
            changed['locator'] = row['url']
    return changed


def build_inventory(day):
    source_time = None
    previous_path = OUT / f'city-facts-reference-{day}.json'
    if previous_path.exists():
        previous = json.loads(previous_path.read_text())['records']
        source_time = next((r['collected_at'] for r in previous
                            if r['fact_id'] == 'src_mvp_amap'), None)
    route, _, _ = build_route_data(day, collected_at=source_time)
    preferred = {(r['kind'], r['fact_id']): r for r in route['records']}
    items = copy.deepcopy(preferred)
    variants, excluded_records, input_counts = [], [], {}
    registry = load_sources()
    for name, relative in MANIFEST:
        path = ROOT / relative
        raw = json.loads(path.read_text())
        input_counts[name] = {'path': relative, 'records': len(raw['records']),
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for original in raw['records']:
            item = original
            key = item['kind'], item['fact_id']
            if item['kind'] == 'source' and item['fact_id'] in registry:
                item = source_from_registry(item, registry[item['fact_id']])
            item = copy.deepcopy(item)
            if key in preferred:
                continue
            if key in items and items[key] != item:
                before, after = items[key], item
                # Add compatible evidence/coordinates, never replace a conflicting
                # identity or silently combine two locations under one name.
                changed = []
                if before['kind'] == 'place':
                    for field in ('longitude', 'latitude', 'coordinate_system', 'parent_place_id'):
                        if before.get(field) in (None, 'unknown') and after.get(field) not in (None, 'unknown'):
                            before[field] = after[field]
                            changed.append(field)
                    if len(str(after.get('address') or '')) > len(str(before.get('address') or '')):
                        before['address'] = after.get('address')
                        changed.append('address')
                merged_sources = list(dict.fromkeys(before.get('source_ids', []) + after.get('source_ids', [])))
                if len(merged_sources) <= 20:
                    before['source_ids'] = merged_sources
                variants.append({'kind': key[0], 'fact_id': key[1],
                                 'kept': 'route_override_or_first_input',
                                 'additional_source': name, 'adopted_fields': changed})
                continue
            if key not in items:
                items[key] = item
    historical, historical_audit = build_historical()
    historical_duplicates = []
    for item in historical:
        key = item['kind'], item['fact_id']
        if key in items:
            continue
        # Merge same-scenic catalog duplicates without treating similar names
        # in different parks as the same entity.
        if item['kind'] in ('place', 'program'):
            match = next((old for old in items.values() if old['kind'] == item['kind']
                          and old.get('name') == item.get('name')
                          and (item['kind'] != 'place' or old.get('place_type') == item.get('place_type'))
                          and old.get('parent_place_id', old.get('place_id')) == item.get('parent_place_id', item.get('place_id'))), None)
            if match:
                merged_sources = list(dict.fromkeys(match.get('source_ids', []) + item.get('source_ids', [])))
                if len(merged_sources) <= 20:
                    match['source_ids'] = merged_sources
                for field in ('description', 'schedule_text', 'stage_name', 'notes', 'suggested_stay_min'):
                    if match.get(field) in (None, '') and item.get(field) not in (None, ''):
                        match[field] = item[field]
                if item['kind'] == 'place' and match.get('longitude') is None and item.get('longitude') is not None:
                    for field in ('longitude', 'latitude', 'coordinate_system'):
                        match[field] = item[field]
                historical_duplicates.append({'kind': item['kind'], 'from': item['fact_id'], 'to': match['fact_id']})
                continue
        if item['kind'] == 'facility':
            match = next((old for old in items.values() if old['kind'] == 'facility'
                          and old['place_id'] == item['place_id'] and old['name'] == item['name']), None)
            if match:
                match['source_ids'] = list(dict.fromkeys(match['source_ids'] + item['source_ids']))
                if not match.get('description') and item.get('description'):
                    match['description'] = item['description']
                historical_duplicates.append({'kind': item['kind'], 'from': item['fact_id'], 'to': match['fact_id']})
                continue
        items[key] = item
    workbook = ROOT / 'data/team-handoff/汴梁智游_最新数据模板_v1.1_成员1媒体试点_20261022_v17.xlsx'
    import openpyxl
    from scripts.import_team_workbook import load_sheet_rows, source_refs
    sheet = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    materialized_rules = []
    skipped_rules = []
    for row in load_sheet_rows(sheet, '12_open_rules'):
        weekdays = {int(x) for x in str(row.get('weekdays') or '').split(';') if x.isdigit()}
        pid = 'pl_wb_' + str(row.get('place_id') or '')[3:]
        if day.isoweekday() not in weekdays or pid not in {k[1] for k in items if k[0] == 'place'}:
            continue
        if row.get('validation_status') == 'expired':
            skipped_rules.append({'id': row['open_rule_id'], 'reason': '工作簿已标过期'})
            continue
        opening, closing = str(row.get('open_time') or ''), str(row.get('close_time') or '')
        if opening >= closing or (row.get('last_entry_time') and not opening <= str(row['last_entry_time']) <= closing):
            skipped_rules.append({'id': row['open_rule_id'], 'reason': '跨日或原始时间区间矛盾；保留原表，不伪造目标日规则'})
            continue
        # Route-specific evidence wins over a generic recurring rule.
        stamp = lambda value: f'{day}T{value}:00+08:00' if value else None
        identity = 'open_wb_' + str(row['open_rule_id'])[3:] + '_' + day.isoformat()
        record = {'kind': 'open_rule', 'fact_id': identity, 'version': 1,
                  'source_ids': source_refs(row, 'open_rules'), 'validation_status': 'unverified',
                  'business_status': 'active', 'is_demo': False, 'rights_status': 'internal_only',
                  'place_id': pid, 'business_date': day.isoformat(), 'opens_at': stamp(row.get('open_time')),
                  'closes_at': stamp(row.get('close_time')), 'last_entry_at': stamp(row.get('last_entry_time')),
                  'reentry': {'true': 'allowed', 'false': 'forbidden'}.get(str(row.get('reentry_allowed')), 'unknown'),
                  'reservation': {'true': 'required', 'false': 'not_required'}.get(str(row.get('reservation_required')), 'unknown'),
                  'ticket_scope': row.get('ticket_scope')}
        items['open_rule', identity] = record
        materialized_rules.append(identity)
    vendor_rules = []
    for place in list(items.values()):
        if place['kind'] != 'place' or place.get('place_type') != 'restaurant' or not place.get('business_hours'):
            continue
        hours = place['business_hours'].replace('：', ':').replace('—', '-').replace('–', '-')
        import re
        match = re.fullmatch(r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})', hours)
        if not match:
            continue
        opening, closing = match.groups()
        closing = '23:59:59' if closing == '24:00' else closing
        if opening >= closing:
            continue
        identity = 'open_vendor_' + place['fact_id'].removeprefix('pl_wb_vendor_') + '_' + day.isoformat()
        items['open_rule', identity] = {'kind': 'open_rule', 'fact_id': identity, 'version': 1,
            'source_ids': place['source_ids'], 'validation_status': 'unverified', 'business_status': 'active',
            'is_demo': False, 'rights_status': 'internal_only', 'place_id': place['fact_id'],
            'business_date': day.isoformat(), 'opens_at': f'{day}T{opening}:00+08:00',
            'closes_at': f'{day}T{closing}' + ('' if closing.count(':') == 2 else ':00') + '+08:00', 'last_entry_at': None,
            'reentry': 'unknown', 'reservation': 'unknown', 'ticket_scope': None}
        vendor_rules.append(identity)
    if 'src_teamworkbook_open_rules' not in {r['fact_id'] for r in items.values() if r['kind'] == 'source'}:
        from scripts.import_team_workbook import handoff_source
        record = handoff_source('open_rules')
        items['source', record['fact_id']] = record
    _validate_alias_selection(items)
    alias_audit = []
    for alias_id, canonical_id in PLACE_ALIASES.items():
        alias, canonical = items.get(('place', alias_id)), items.get(('place', canonical_id))
        if not alias or not canonical:
            continue
        for field in ('longitude', 'latitude', 'coordinate_system', 'address', 'external_poi_id',
                      'business_hours', 'price_band', 'description', 'notes'):
            if canonical.get(field) in (None, '', 'unknown') and alias.get(field) not in (None, '', 'unknown'):
                canonical[field] = alias[field]
        canonical['source_ids'] = list(dict.fromkeys(canonical.get('source_ids', []) + alias.get('source_ids', [])))
        canonical['aliases'] = list(dict.fromkeys(canonical.get('aliases', []) + [alias['name']]))
        alias['business_status'] = 'cancelled'
        alias['planning_status'] = 'candidate'
        alias['planning_note'] = '历史重复记录，已由 ' + canonical_id + ' 统一承接。'
        alias['superseded_by'] = canonical_id
        alias_audit.append({'from': alias_id, 'to': canonical_id})
    # Rewrite dependent facts whose park/venue pointer used an old ID.  Basket
    # items and old links remain readable through superseded_by, while facts used
    # by the planner have one canonical parent chain.
    _rewrite_place_references(items)
    for item in items.values():
        if item.get('is_demo') or item.get('rights_status') == 'prohibited':
            excluded_records.append({'kind': item['kind'], 'fact_id': item['fact_id'],
                                     'reason': '演示或明确禁止使用，不作参考准入'})
            continue
        if item['kind'] != 'source' and not item.get('source_ids'):
            excluded_records.append({'kind': item['kind'], 'fact_id': item['fact_id'],
                                     'reason': '缺少来源，保留候选，不开放参考规划'})
            continue
        if item['kind'] == 'place' and item['fact_id'] in PLACE_ALIASES:
            continue
        # The 47 unverified platform-to-menu edges are real sourced catalog
        # evidence. Their evidence_note still says supply is not current or
        # guaranteed; they may now support reference dining, not stock claims.
        item['planning_status'] = 'reference'
        item['planning_note'] = NOTE
    venues_path = OUT / 'show-venues-kaifengfu.json'
    venues = facts.FactBundle.model_validate_json(venues_path.read_text())
    for venue in venues.records:
        items[venue.kind, venue.fact_id] = venue.model_dump(mode='json')
    excluded_keys = {(r['kind'], r['fact_id']) for r in excluded_records}
    records = [r for key, r in items.items() if key not in excluded_keys]
    bundle = facts.FactBundle.model_validate({'schema_version': 'city-facts/1', 'records': records})
    normalized = bundle.model_dump(mode='json')
    facts.check_links({(r.kind, r.fact_id): r for r in bundle.records})
    audit = {
        'generated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'business_date': day.isoformat(),
        'scope': '显式列出的团队与采集 city-facts 包；不把媒体、节目介绍或抓取成功自动变成当日场次。',
        'inputs': input_counts,
        'historical_package': historical_audit,
        'historical_duplicate_matches': historical_duplicates,
        'canonical_place_aliases': alias_audit,
        'show_venue_bundle': str(venues_path.relative_to(ROOT)),
        'materialized_workbook_open_rules': materialized_rules,
        'materialized_vendor_open_rules': vendor_rules,
        'skipped_workbook_open_rules': skipped_rules,
        'excluded_inputs': EXCLUDED,
        'excluded_records': excluded_records,
        'duplicate_or_variant_records': variants,
        'unique_adopted': len(records),
        'by_kind': dict(Counter(r['kind'] for r in records)),
        'planning_status': dict(Counter(r['planning_status'] for r in records)),
        'verification_status': dict(Counter(r['validation_status'] for r in records)),
        'dated_open_rules': dict(Counter(r['business_date'] for r in records if r['kind'] == 'open_rule')),
        'show_sessions': sum(r['kind'] == 'show_session' for r in records),
        'interpretation': 'reference 表示允许参考规划，不表示 verified；营业、供应、入口与节目场次仍按各记录证据边界解释。',
    }
    return normalized, audit


def version_for_target(bundle, database):
    if not database.exists():
        return bundle
    with sqlite3.connect(f'file:{database.resolve()}?mode=ro', uri=True) as db:
        old = {(r[0], r[1]): json.loads(r[2]) for r in db.execute(
            'SELECT kind,id,record_json FROM city_facts')}
    for item in bundle['records']:
        before = old.get((item['kind'], item['fact_id']))
        if before:
            item['version'] = before['version']
            if item != before:
                item['version'] += 1
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    bundle, audit = build_inventory(args.date)
    bundle = version_for_target(bundle, args.database)
    OUT.mkdir(parents=True, exist_ok=True)
    prefix = OUT / f'inventory-{args.date}'
    bundle_path = prefix.with_suffix('.json')
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + '\n')
    command = [sys.executable, str(ROOT / 'scripts/import_city_facts.py'), str(bundle_path),
               '--database', str(args.database)]
    if args.apply:
        command.append('--apply')
    run = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
    if run.returncode:
        print(run.stdout or run.stderr, file=sys.stderr)
        raise SystemExit(run.returncode)
    report = json.loads(run.stdout)
    if args.apply:
        # A maintenance binding is an old UI/demo identity, so it follows the
        # same canonical place map as fact references. Basket rows deliberately
        # keep their old object_id and resolve through superseded_by.
        with sqlite3.connect(args.database) as db:
            _rewrite_place_bindings(db)
    audit['import'] = {k: report[k] for k in ('status', 'received', 'changed', 'unchanged')}
    audit['bundle'] = str(bundle_path.relative_to(ROOT))
    audit['database'] = str(args.database)
    audit_path = OUT / f'inventory-audit-{args.date}.json'
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    (OUT / f'inventory-import-{args.date}.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({**audit['import'], 'by_kind': audit['by_kind'],
                      'audit': str(audit_path), 'excluded_inputs': len(EXCLUDED)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
