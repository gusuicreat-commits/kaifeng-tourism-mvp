"""Inventory merge protects provenance and identity before MVP adoption."""
import json
from datetime import date
from pathlib import Path

from app.city import facts
from scripts.import_mvp_inventory import EXCLUDED, PLACE_ALIASES, _rewrite_place_bindings, build_inventory


def test_inventory_deduplicates_and_keeps_real_sourced_supply():
    bundle, audit = build_inventory(date(2026, 10, 25))
    rows = bundle['records']
    keys = [(r['kind'], r['fact_id']) for r in rows]
    assert len(keys) == len(set(keys))
    assert all(r['planning_status'] == 'reference' for r in rows if r['kind'] != 'place' or r['fact_id'] not in PLACE_ALIASES)
    assert all(r['business_status'] == 'cancelled' and r['superseded_by'] == PLACE_ALIASES[r['fact_id']]
               for r in rows if r['kind'] == 'place' and r['fact_id'] in PLACE_ALIASES)
    assert all(r['validation_status'] == 'unverified' for r in rows)
    assert not any(r['is_demo'] or r['rights_status'] == 'prohibited' for r in rows)
    assert sum(r['kind'] == 'supply' for r in rows) == 48
    assert sum(r['kind'] == 'show_session' for r in rows) == 0
    assert audit['unique_adopted'] == len(rows)
    facts.check_links({(r['kind'], r['fact_id']): facts.RECORD.validate_python(r) for r in rows})


def test_route_override_does_not_reassign_qingming_entrances():
    bundle, _ = build_inventory(date(2026, 10, 25))
    items = {(r['kind'], r['fact_id']): r for r in bundle['records']}
    assert items['place', 'pl_wb_ent_000001']['name'] == '迎宾门'
    assert items['place', 'pl_wb_ent_000001']['parent_place_id'] == 'pl_wb_000001'
    assert items['entrance', 'ent_wb_000002']['place_id'] == 'pl_wb_000001'
    assert items['entrance', 'ent_wb_000002']['direction'] == 'entry'
    assert any('v2-20261025' in item['path'] for item in EXCLUDED)


def test_place_aliases_keep_demo_facts_and_rewrite_old_parent_references():
    bundle, _ = build_inventory(date(2026, 10, 25))
    items = {(r['kind'], r['fact_id']): r for r in bundle['records']}
    for alias, canonical in PLACE_ALIASES.items():
        assert items['place', alias]['superseded_by'] == canonical
    assert items['place', 'kaifengfu']['superseded_by'] == 'pl_wb_000003'
    assert items['place', 'daxiangguosi']['superseded_by'] == 'pl_wb_000005'
    assert items['place', 'gulou_night']['superseded_by'] == 'pl_wb_000013'
    assert items['open_rule', 'open_mvp_gulou_20261025']['place_id'] == 'pl_wb_000013'
    assert items['vendor', 'ven_wb_000001']['place_id'] == 'pl_wb_vendor_000001'
    assert items['supply', 'vf_wb_000001']['vendor_id'] == 'ven_wb_000001'
    assert all(r.get('parent_place_id') not in PLACE_ALIASES for r in bundle['records'])
    assert all(r.get('place_id') not in PLACE_ALIASES for r in bundle['records'])


def test_place_alias_bindings_follow_canonical_id_without_touching_other_kinds():
    import sqlite3
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE city_object_bindings (kind TEXT, object_id TEXT, fact_kind TEXT, fact_id TEXT)')
        db.executemany('INSERT INTO city_object_bindings VALUES (?,?,?,?)', [
            ('amap_poi', 'legacy', 'place', 'kaifengfu'),
            ('food', 'legacy-food', 'food', 'kaifengfu'),
        ])
        _rewrite_place_bindings(db)
        rows = db.execute('SELECT fact_kind,fact_id FROM city_object_bindings ORDER BY kind').fetchall()
    assert rows == [('place', 'pl_wb_000003'), ('food', 'kaifengfu')]


def test_inventory_is_repeatable_and_programs_are_not_dated_sessions():
    first, _ = build_inventory(date(2026, 10, 25))
    second, _ = build_inventory(date(2026, 10, 25))
    assert first == second
    assert any(r['kind'] == 'program' for r in first['records'])
    assert any(r['kind'] == 'open_rule' and r['business_date'] == '2026-10-24'
               for r in first['records'])


def test_kaifengfu_stage_bundle_keeps_poi_identity_and_archived_source_links():
    bundle, audit = build_inventory(date(2026, 10, 25))
    items = {(r['kind'], r['fact_id']): r for r in bundle['records']}
    root = Path(__file__).resolve().parents[2]
    venues = facts.FactBundle.model_validate_json((root / audit['show_venue_bundle']).read_text())
    archived = json.loads((root / 'data/mvp/source/historical-catalog-20261020.json').read_text())['records']
    assert {items['place', r.parent_place_id]['name'] for r in venues.records} == {'梅花堂', '明镜湖', '明礼院', '英武楼', '天庆观'}
    for venue in venues.records:
        assert items['place', venue.fact_id] == venue.model_dump(mode='json')
        poi = items['place', venue.parent_place_id]
        assert poi['place_type'] == 'poi' and venue.name == poi['name'] + '演出点'
        assert poi['parent_place_id'] == 'pl_wb_000003'
        assert venue.longitude is None and venue.latitude is None and not venue.is_demo
        assert venue.validation_status == 'unverified' and venue.planning_status == 'reference'
        programs = [p for p in archived if p['kind'] == 'program' and p.get('stage_name') == poi['name']
                    and p.get('place_id') == poi['parent_place_id']]
        assert any(set(p['source_ids']) & set(venue.source_ids) for p in programs)
        assert all(items['source', sid].get('url') for sid in venue.source_ids)


def test_workbook_fields_highlights_and_historical_catalog_are_adopted():
    bundle, audit = build_inventory(date(2026, 10, 25))
    items = {(r['kind'], r['fact_id']): r for r in bundle['records']}
    vendor = items['place', 'pl_wb_vendor_000001']
    assert vendor['business_hours'] == '10:30-14:00；17:30-21:00'
    assert vendor['price_band'] == '60-70元/人'
    assert vendor['external_poi_id'] == 'B017A00HWY'
    assert len([r for r in bundle['records'] if r['kind'] == 'highlight']) == 48
    assert items['highlight', 'hl_wb_000001']['description']
    assert items['program', 'prg_wb_000001']['place_id'] == 'pl_wb_000001'
    assert items['program', 'prg_wb_000001']['description']
    assert sum(r['kind'] == 'facility' for r in bundle['records']) == 26
    assert audit['historical_package']['status'] == 'converted'
    assert audit['materialized_workbook_open_rules']
    assert all(r['kind'] != 'show_session' for r in bundle['records'])


def test_sourced_stay_is_used_before_generic_planning_default(monkeypatch):
    from datetime import datetime, timedelta
    from app.city.engine.context import PlanningContext
    from app.city.engine.models import OperationalBundle, Requirements, Goal
    from app.city.engine.prepare import prepare
    from app.city.schema import TZ
    assessed = datetime(2026, 10, 25, 9, tzinfo=TZ)
    day = assessed.date()
    ends = assessed + timedelta(hours=4)
    starts = assessed + timedelta(minutes=1)
    monkeypatch.setattr(facts, 'now', lambda: assessed)
    source = facts.Source(kind='source', fact_id='src-stay', name='资料来源', source_type='other', locator='test',
                          collected_at='2026-10-24T12:00:00+08:00', planning_status='reference', rights_status='internal_only',
                          planning_note='合成测试')
    place = facts.Place(kind='place', fact_id='place-stay', name='测试地点', place_type='night_visit',
                        source_ids=('src-stay',), suggested_stay_min=50, longitude=114.35, latitude=34.79,
                        coordinate_system='GCJ02', rights_status='internal_only',
                        planning_status='reference', planning_note='合成测试')
    rule = facts.OpenRule(kind='open_rule', fact_id='open-stay', place_id=place.fact_id, source_ids=('src-stay',),
                          business_date=day, opens_at=assessed,
                          closes_at=ends, planning_status='reference', rights_status='internal_only', planning_note='合成测试')
    context = PlanningContext({('source', source.fact_id): source, ('place', place.fact_id): place,
                               ('open_rule', rule.fact_id): rule},
                              OperationalBundle(), assessed)
    req = Requirements.model_validate({'business_date': day, 'starts_at': starts,
        'ends_at': ends, 'origin_place_id': place.fact_id, 'destination_place_id': place.fact_id,
        'goals': [{'goal_id': 'sourced-stay', 'target_kind': 'place', 'target_id': place.fact_id, 'kind': 'visit'}]})
    problem = prepare(req, context)
    activity = next(a for a in problem.activities if a.goal_id == 'sourced-stay')
    assert activity.duration == place.suggested_stay_min * 60
    assert problem.defaults['sourced-stay']['basis'] == 'sourced_reference'
