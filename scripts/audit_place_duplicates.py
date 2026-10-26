#!/usr/bin/env python3
"""Audit same-name place records without writing to the runtime database.

The database is opened with SQLite ``mode=ro``.  A JSON report is printed to
stdout; ``--table-output`` writes the same findings as a compact review table.
The 300 m coordinate threshold is a review aid for records in the same
coordinate system, not proof that two entrances or venues are interchangeable.
"""
import argparse
import json
import math
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / 'data' / 'tourism.sqlite3'
COORDINATE_THRESHOLD_M = 300
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
try:
    from scripts.import_mvp_inventory import PLACE_ALIASES
except ImportError:  # Keep the audit usable as a standalone read-only utility.
    PLACE_ALIASES = {}
DEMO_FIXED_REFS = (
    ('scenic', 'kaifengfu', '素材篮景区入口'),
    ('vendor', 'ven_wb_000001', '素材篮第一楼商家'),
    ('place', 'pl_wb_000013', '演示路线鼓楼夜市'),
    ('place', 'pl_wb_000003', '演示路线开封府'),
    ('place', 'pl_wb_vendor_000001', '演示路线第一楼地点'),
)


def _token(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')).casefold())


def _address_token(value):
    value = _token(value)
    value = re.sub(r'[（(][^（）()]*[）)]', '', value)
    for prefix in ('河南省', '开封市', '龙亭区', '鼓楼区', '顺河回族区', '祥符区'):
        value = value.replace(prefix, '')
    return value


def _distance_m(left, right):
    if left.get('coordinate_system') != right.get('coordinate_system'):
        return None
    if left.get('coordinate_system') in (None, 'unknown', 'local_demo'):
        return None
    if None in (left.get('longitude'), left.get('latitude'), right.get('longitude'), right.get('latitude')):
        return None
    lat1, lat2 = math.radians(left['latitude']), math.radians(right['latitude'])
    dy = (right['latitude'] - left['latitude']) * 111_320
    dx = (right['longitude'] - left['longitude']) * 111_320 * math.cos((lat1 + lat2) / 2)
    return math.hypot(dx, dy)


def _shared_address_fragments(left, right):
    stop = {'周边', '附近', '候选', '平台', '第三方', '官网', '路南', '路北'}
    fragments = set()
    for length in (4, 3, 2):
        for start in range(max(0, len(left) - length + 1)):
            part = left[start:start + length]
            if part in stop:
                continue
            if part in right:
                fragments.add(part)
    return fragments


def _read_records(db):
    db.row_factory = sqlite3.Row
    return {(row['kind'], row['id']): json.loads(row['record_json'])
            for row in db.execute('SELECT kind,id,record_json FROM city_facts')}


def _parent_scenic(records, place_id):
    seen = set()
    current = place_id
    while current and current not in seen:
        seen.add(current)
        place = records.get(('place', current))
        if not place:
            return None
        if place.get('place_type') == 'scenic':
            # A scenic record is itself the owner; duplicate scenic records do
            # not become a false "different parent" conflict.
            return None if current == place_id else current
        current = place.get('parent_place_id')
    return None


def _group_places(records):
    parent = {}

    def find(item):
        parent.setdefault(item, item)
        if parent[item] != item:
            parent[item] = find(parent[item])
        return parent[item]

    def union(left, right):
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    by_token = defaultdict(list)
    for (kind, identity), record in records.items():
        if kind != 'place':
            continue
        key = (kind, identity)
        for value in (record.get('name'), *(record.get('aliases') or ())):
            if value:
                by_token[_token(value)].append(key)
    for keys in by_token.values():
        keys = list(dict.fromkeys(keys))
        for key in keys[1:]:
            union(keys[0], key)
    grouped = defaultdict(list)
    for key in parent:
        grouped[find(key)].append(key)
    return [sorted(keys, key=lambda value: value[1]) for keys in grouped.values() if len(keys) > 1]


def _fact_references(records, members):
    member_ids = {identity for _, identity in members}
    refs = defaultdict(list)
    for (kind, identity), record in records.items():
        for field in ('place_id', 'parent_place_id', 'stage_place_id', 'entrance_place_id'):
            if record.get(field) in member_ids:
                refs[record[field]].append(f'{kind}:{identity}.{field}')
        if kind == 'supply':
            vendor = records.get(('vendor', record.get('vendor_id')))
            if vendor and vendor.get('place_id') in member_ids:
                refs[vendor['place_id']].append(f'supply:{identity}.vendor_id→place')
    return refs


def _external_references(db, records, members):
    member_ids = {identity for _, identity in members}
    refs = defaultdict(list)
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'city_object_bindings' in tables:
        for row in db.execute("SELECT kind,object_id,fact_kind,fact_id FROM city_object_bindings"):
            if row['fact_kind'] == 'place' and row['fact_id'] in member_ids:
                refs[row['fact_id']].append(f"binding:{row['kind']}:{row['object_id']}")
    if 'city_basket_items' in tables:
        for row in db.execute('SELECT trip_id,object_key,data_json FROM city_basket_items'):
            try:
                item = json.loads(row['data_json'])
            except (TypeError, ValueError):
                continue
            object_id = item.get('object_id')
            if object_id in member_ids:
                refs[object_id].append(f"basket:{row['trip_id']}:{row['object_key']}")
            if item.get('kind') == 'scenic' and 'city_object_bindings' in tables:
                binding = db.execute("SELECT fact_id FROM city_object_bindings WHERE kind='scenic' AND object_id=?",
                                     (object_id,)).fetchone()
                if binding and binding['fact_id'] in member_ids:
                    refs[binding['fact_id']].append(f"basket:{row['trip_id']}:{row['object_key']}→binding")
    for kind, identity, label in DEMO_FIXED_REFS:
        fact_kind = 'place' if kind in ('scenic', 'place') else kind
        fact_id = identity
        if kind == 'scenic' and 'city_object_bindings' in tables:
            binding = db.execute("SELECT fact_kind,fact_id FROM city_object_bindings WHERE kind='scenic' AND object_id=?",
                                 (identity,)).fetchone()
            if binding:
                fact_kind, fact_id = binding['fact_kind'], binding['fact_id']
        if fact_kind == 'place' and fact_id in member_ids:
            refs[fact_id].append(f'demo:{kind}:{identity} ({label})')
    return refs


def _evidence(records, members):
    rows = [records[key] for key in members]
    evidence = []
    if len({row.get('place_type') for row in rows}) != 1:
        evidence.append({'kind': 'place_type_conflict', 'detail': 'place_type 不一致，不能合并'})
    scenic_owners = {_parent_scenic(records, key[1]) for key in members}
    scenic_owners.discard(None)
    if len(scenic_owners) > 1:
        evidence.append({'kind': 'parent_conflict', 'detail': '所属景区不同，同名入口/园内地点保留独立记录'})
    for index, left in enumerate(rows):
        for right in rows[index + 1:]:
            left_address, right_address = _address_token(left.get('address')), _address_token(right.get('address'))
            shared_parts = _shared_address_fragments(left_address, right_address)
            same_address = (left_address and right_address and
                            (left_address == right_address or left_address in right_address or right_address in left_address))
            same_landmark_area = len(shared_parts) >= 2
            if same_address or same_landmark_area:
                evidence.append({'kind': 'address', 'records': [left['fact_id'], right['fact_id']],
                                 'detail': '标准化地址相同、包含具体地址或共享至少两个地标片区词'})
            distance = _distance_m(left, right)
            if distance is not None and distance <= COORDINATE_THRESHOLD_M:
                evidence.append({'kind': 'coordinate', 'records': [left['fact_id'], right['fact_id']],
                                 'distance_m': round(distance, 1),
                                 'detail': f'同坐标系坐标相距不超过 {COORDINATE_THRESHOLD_M} 米'})
    if not any(item['kind'] in ('address', 'coordinate') for item in evidence):
        evidence.append({'kind': 'missing_identity_evidence', 'detail': '没有同址地址或同坐标系 300 米内坐标证据'})
    return evidence


def _canonical_id(records, identity):
    seen = set()
    while identity not in seen:
        seen.add(identity)
        target = records.get(('place', identity), {}).get('superseded_by')
        if not target:
            return identity
        identity = target
    return identity


def audit_database(database):
    path = Path(database).resolve()
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        records = _read_records(db)
        groups = []
        for number, members in enumerate(_group_places(records), 1):
            fact_refs = _fact_references(records, members)
            external_refs = _external_references(db, records, members)
            evidence = _evidence(records, members)
            canonical_ids = {_canonical_id(records, identity) for _, identity in members}
            merged = len(canonical_ids) == 1 and all(
                identity == next(iter(canonical_ids)) or records['place', identity].get('superseded_by')
                for _, identity in members)
            has_identity_evidence = any(item['kind'] in ('address', 'coordinate') for item in evidence)
            planned = any(PLACE_ALIASES.get(identity) in {member_id for _, member_id in members}
                          for _, identity in members)
            if merged:
                decision = 'merged'
            elif planned and has_identity_evidence:
                decision = 'partial_merge' if any(records['place', identity].get('superseded_by')
                                                  for _, identity in members) else 'planned_merge'
            else:
                decision = 'unmerged'
            rows = []
            for _, identity in members:
                record = records['place', identity]
                rows.append({
                    'id': identity, 'name': record.get('name'), 'aliases': list(record.get('aliases') or ()),
                    'place_type': record.get('place_type'), 'parent_place_id': record.get('parent_place_id'),
                    'parent_scenic_id': _parent_scenic(records, identity), 'address': record.get('address'),
                    'longitude': record.get('longitude'), 'latitude': record.get('latitude'),
                    'coordinate_system': record.get('coordinate_system'), 'source_ids': list(record.get('source_ids') or ()),
                    'superseded_by': record.get('superseded_by'),
                    'references': sorted(set(fact_refs[identity] + external_refs[identity])),
                })
            groups.append({'group_id': number, 'match_tokens': sorted({
                _token(value) for row in rows for value in (row['name'], *row['aliases']) if value}),
                'decision': decision, 'canonical_ids': sorted(canonical_ids), 'evidence': evidence,
                'members': rows})
        return {'schema_version': 'place-duplicate-audit/1', 'database': str(path),
                'coordinate_threshold_m': COORDINATE_THRESHOLD_M,
                'fixed_references': [{'kind': k, 'id': i, 'label': label} for k, i, label in DEMO_FIXED_REFS],
                'groups': groups,
                'summary': {
                    'groups': len(groups), 'merged': sum(g['decision'] == 'merged' for g in groups),
                    'planned_merge': sum(g['decision'] == 'planned_merge' for g in groups),
                    'partial_merge': sum(g['decision'] == 'partial_merge' for g in groups),
                    'unmerged': sum(g['decision'] == 'unmerged' for g in groups),
                }}


def render_table(report):
    lines = [f"地点重复审计：{report['database']}",
             f"坐标阈值：同坐标系 {report['coordinate_threshold_m']} 米；仅作同址复核证据",
             "状态\t地点组\t成员\t类型/所属景区\t地址/坐标\t来源\t引用\t证据/原因"]
    for group in report['groups']:
        evidence = '；'.join(item['detail'] for item in group['evidence'])
        for member in group['members']:
            location = member['address'] or '-'
            if member['longitude'] is not None:
                location += f" | {member['longitude']},{member['latitude']} {member['coordinate_system']}"
            lines.append('\t'.join((group['decision'], str(group['group_id']),
                f"{member['id']} ({member['name']})", f"{member['place_type']}/{member['parent_scenic_id'] or '-'}",
                location, ','.join(member['source_ids']) or '-', str(len(member['references'])))))
        lines.append(f"  证据：{evidence}")
    summary = report['summary']
    lines.append(f"汇总：{summary['groups']} 组，已合并 {summary['merged']}，部分合并 {summary['partial_merge']}，待导入合并 {summary['planned_merge']}，未合并 {summary['unmerged']}")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=DEFAULT_DATABASE)
    parser.add_argument('--json-output', type=Path, help='写 JSON 报告；缺省打印到 stdout')
    parser.add_argument('--table-output', type=Path, help='写可读表；缺省随 JSON 后打印到 stdout')
    args = parser.parse_args()
    report = audit_database(args.database)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    table = render_table(report)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(payload)
    if args.table_output:
        args.table_output.parent.mkdir(parents=True, exist_ok=True)
        args.table_output.write_text(table)
    if not args.json_output and not args.table_output:
        print(payload, end='')
        print(table, end='')
    elif not args.json_output:
        print(payload, end='')
    elif not args.table_output:
        print(table, end='')


if __name__ == '__main__':
    main()
