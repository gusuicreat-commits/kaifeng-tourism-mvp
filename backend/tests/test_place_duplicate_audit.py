import json
import sqlite3

from scripts.audit_place_duplicates import audit_database, render_table


def _record(kind, fact_id, name, **extra):
    base = {
        'kind': kind, 'fact_id': fact_id, 'name': name, 'place_type': 'scenic',
        'source_ids': ['src-test'], 'aliases': [], 'parent_place_id': None,
        'address': None, 'longitude': None, 'latitude': None,
        'coordinate_system': 'unknown',
    }
    base.update(extra)
    return base


def _database(tmp_path):
    path = tmp_path / 'tourism.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE city_facts (kind TEXT, id TEXT, record_json TEXT)')
        records = [
            _record('place', 'pl_wb_000013', '鼓楼夜市', place_type='night_visit',
                    address='河南省开封市鼓楼区鼓楼南广场', longitude=114.35, latitude=34.79,
                    coordinate_system='GCJ02'),
            _record('place', 'gulou_night', '鼓楼夜市', place_type='night_visit',
                    address='开封市鼓楼广场周边'),
            _record('place', 'north-a', '北门', place_type='entrance', parent_place_id='scenic-a'),
            _record('place', 'north-b', '北门', place_type='entrance', parent_place_id='scenic-b'),
            _record('place', 'scenic-a', '甲景区'),
            _record('place', 'scenic-b', '乙景区'),
            _record('place', 'poi-a', '同处', place_type='poi', address='开封市鼓楼区同处路1号'),
            _record('place', 'scenic-c', '同处', place_type='scenic', address='开封市鼓楼区同处路1号'),
            {'kind': 'open_rule', 'fact_id': 'open-a', 'place_id': 'pl_wb_000013'},
            {'kind': 'vendor', 'fact_id': 'vendor-a', 'place_id': 'pl_wb_000013'},
            {'kind': 'supply', 'fact_id': 'supply-a', 'vendor_id': 'vendor-a', 'food_id': 'food-a'},
            {'kind': 'food', 'fact_id': 'food-a', 'name': '测试菜', 'source_ids': ['src-test']},
        ]
        db.executemany('INSERT INTO city_facts VALUES (?,?,?)',
                       [(row['kind'], row['fact_id'], json.dumps(row, ensure_ascii=False)) for row in records])
        db.execute('CREATE TABLE city_object_bindings (kind TEXT, object_id TEXT, fact_kind TEXT, fact_id TEXT)')
        db.execute("INSERT INTO city_object_bindings VALUES ('amap_poi','demo','place','pl_wb_000013')")
        db.execute('CREATE TABLE city_basket_items (trip_id TEXT, object_key TEXT, data_json TEXT)')
        db.execute("INSERT INTO city_basket_items VALUES ('trip','place:pl_wb_000013',?)",
                   (json.dumps({'kind': 'place', 'object_id': 'pl_wb_000013'}),))
    return path


def test_audit_collects_identity_evidence_and_all_reference_surfaces(tmp_path):
    report = audit_database(_database(tmp_path))
    gulou = next(group for group in report['groups'] if 'gulou_night' in [m['id'] for m in group['members']])
    assert gulou['decision'] == 'planned_merge'
    canonical = next(member for member in gulou['members'] if member['id'] == 'pl_wb_000013')
    assert any('open_rule:open-a.place_id' in ref for ref in canonical['references'])
    assert any('supply:supply-a.vendor_id' in ref for ref in canonical['references'])
    assert any(ref.startswith('binding:') for ref in canonical['references'])
    assert any(ref.startswith('basket:') for ref in canonical['references'])
    assert any(item['kind'] == 'address' for item in gulou['evidence'])

    entrance = next(group for group in report['groups'] if 'north-a' in [m['id'] for m in group['members']])
    assert entrance['decision'] == 'unmerged'
    assert any(item['kind'] == 'parent_conflict' for item in entrance['evidence'])

    typed = next(group for group in report['groups'] if 'poi-a' in [m['id'] for m in group['members']])
    assert any(item['kind'] == 'place_type_conflict' for item in typed['evidence'])


def test_audit_is_read_only_and_table_is_reviewable(tmp_path):
    path = _database(tmp_path)
    before = path.stat().st_mtime_ns
    report = audit_database(path)
    assert path.stat().st_mtime_ns == before
    table = render_table(report)
    assert '坐标阈值：同坐标系 300 米' in table
    assert 'gulou_night' in table
