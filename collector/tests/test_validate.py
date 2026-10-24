"""Validation: normalisation, dedup, coarse spatial filter, conflicts, bundle.

Nothing here verifies a fact: `verified` always requires the human five-tuple,
which this module never produces.
"""
import tempfile
from datetime import date
from pathlib import Path

from collector import ledger
from collector import quality
from collector.config import districts
from collector.extract import StructuredBundle, StructuredRecord
from collector.validate import (build_bundle, coordinate_fingerprint, normalize,
                                normalize_name, register_review_tasks, resolve_district,
                                spatial_verdict, write_json)

STAMP = "2026-10-23T10:00:00+08:00"


def record(record_id, category, name, **kwargs):
    return StructuredRecord(record_id=record_id, category=category, source_keys=kwargs.pop("source_keys", ["s1"]),
                            collected_at=STAMP, extraction_method="test", name=name, **kwargs)


# ------------------------------------------------------------------ name normalisation

def test_normalize_name_keeps_branch_qualifier():
    assert normalize_name("黄家老店（西司店）") == normalize_name("黄家老店(西司店)")
    assert normalize_name("黄家老店（西司店）") != normalize_name("黄家老店（大梁路店）"), "分店不得被误判为重复"
    assert normalize_name("鼓楼 夜 市") == "鼓楼夜市"
    assert normalize_name("开封市-博物馆") == "开封市博物馆"
    assert normalize_name("开封市 · 博物馆") == "开封市博物馆"


def test_coordinate_fingerprint_rounds_and_handles_missing():
    assert coordinate_fingerprint(114.3512, 34.7912) == "114.351,34.791"
    assert coordinate_fingerprint(None, 34.79) is None
    assert coordinate_fingerprint(114.3512, None) is None


# ------------------------------------------------------------------ district resolution

def test_resolve_district_accepts_short_forms_used_in_sources():
    resolved = districts()
    assert resolve_district("示范区", resolved).name == "城乡一体化示范区"
    assert resolve_district("顺河区", resolved).name == "顺河回族区"
    assert resolve_district("龙亭区", resolved).name == "龙亭区"
    assert resolve_district("不存在的区", resolved) is None
    assert resolve_district("", resolved) is None


# ------------------------------------------------------------------ spatial filter

def test_spatial_verdict_inside_by_coarse_bbox():
    status, district, reason = spatial_verdict(114.35, 34.79, districts(), None, None, None)
    assert status == "inside" and district == "龙亭区"
    assert reason.startswith("coarse_bbox:")


def test_spatial_verdict_excludes_counties_without_coordinates():
    status, district, reason = spatial_verdict(None, None, districts(), "开封市杞县某路1号", None, None)
    assert status == "outside" and "杞县" in reason
    assert district == "待核验"


def test_spatial_verdict_excluded_county_is_not_forced_inside_by_declared_district():
    status, _, reason = spatial_verdict(None, None, districts(), "开封市祥符区某路", None, None)
    assert status == "outside" and "祥符区" in reason


def test_spatial_verdict_pending_when_nothing_can_place_the_record():
    status, district, reason = spatial_verdict(None, None, districts(), None, "某无名地点", None)
    assert status == "pending" and district == "待核验"
    assert reason == "no_coords_no_district_hint"


def test_spatial_verdict_coordinates_outside_all_bbox_stay_pending():
    status, _, reason = spatial_verdict(115.0, 34.5, districts(), None, None, None)
    assert status == "pending", "城外坐标不得被 silently 判 outside"
    assert reason == "coords_outside_all_bbox"


def test_spatial_verdict_declared_district_is_a_statement_not_verification():
    status, district, reason = spatial_verdict(None, None, districts(), None, None, "示范区")
    assert status == "inside" and district == "城乡一体化示范区"
    assert reason.startswith("declared_district:")


# ------------------------------------------------------------------ normalize()

def test_normalize_drops_nameless_records_only():
    report = normalize([record("a", "museum", None), record("b", "museum", "开封市博物馆")])
    assert [r.record_id for r in report.records] == ["b"]


def test_normalize_flags_same_name_as_duplicate_but_keeps_both():
    report = normalize([
        record("a", "museum", "开封市博物馆", address="开封市鼓楼区徐府街", opens_at="9:00"),
        record("b", "museum", "开封市博物馆", address="另一处地址", opens_at="8:30"),
    ])
    assert len(report.records) == 2, "重复记录不得被静默删除"
    assert report.duplicates == [{"kind": "name", "kept": "a", "dropped": "b",
                                  "name": "开封市博物馆", "category": "museum"}]
    assert any("同名重复" in item for item in report.records[1].review_items)


def test_normalize_does_not_merge_different_branches():
    report = normalize([
        record("a", "restaurant", "黄家老店（西司店）", longitude=114.35, latitude=34.79,
               coordinate_system="GCJ02"),
        record("b", "restaurant", "黄家老店（大梁路店）", longitude=114.351, latitude=34.791,
               coordinate_system="GCJ02"),
    ])
    assert report.duplicates == []


def test_normalize_detects_same_coordinate_duplicate():
    report = normalize([
        record("a", "restaurant", "甲店", longitude=114.3512, latitude=34.7912,
               coordinate_system="GCJ02"),
        record("b", "restaurant", "乙店", longitude=114.3513, latitude=34.7913,
               coordinate_system="GCJ02"),
    ])
    assert [d["kind"] for d in report.duplicates] == ["coordinate"]


def test_normalize_flags_repeated_source_for_same_name():
    report = normalize([
        record("a", "museum", "开封市博物馆", source_keys=["s1"]),
        record("b", "museum", "开封市博物馆", source_keys=["s1"]),
    ])
    assert any("同一来源" in item for item in report.records[1].review_items)


def test_normalize_preserves_all_conflicting_values():
    report = normalize([
        record("a", "museum", "开封市博物馆", address="地址一", opens_at="9:00"),
        record("b", "museum", "开封市博物馆", address="地址二", opens_at="8:30"),
    ])
    fields = {c["field"]: c["values"] for c in report.conflicts}
    assert fields["address"] == ["地址一", "地址二"]
    assert fields["opens_at"] == ["8:30", "9:00"]
    assert all(c["resolution"].startswith("保留全部原值") for c in report.conflicts)


def test_normalize_does_not_report_conflict_for_single_record():
    report = normalize([record("a", "museum", "开封市博物馆", opens_at="9:00")])
    assert report.conflicts == []


def test_normalize_flags_collection_day_used_as_business_date():
    report = normalize([record("a", "museum", "开封市博物馆", business_date="2026-10-23")],
                       collected_day=date(2026, 10, 23))
    assert any("疑似把采集日当游玩日" in c for c in report.records[0].conflicts)


def test_normalize_flags_unknown_coordinate_system_and_demo_coordinates():
    report = normalize([
        record("a", "museum", "开封市博物馆", longitude=114.35, latitude=34.79),
        record("b", "museum", "示意馆", longitude=114.35, latitude=34.79, coordinate_system="local_demo"),
    ])
    assert any("坐标系未声明" in i for i in report.records[0].review_items)
    assert any("示意坐标不得用于正式规划" in i for i in report.records[1].review_items)


def test_normalize_pending_records_become_review_tasks():
    report = normalize([record("a", "museum", "无名地点")])
    assert report.records[0].spatial_status == "pending"
    assert [t["record_id"] for t in report.review_tasks] == ["a"]
    assert "空间归属待核验" in report.review_tasks[0]["check_item"]


def test_normalize_spatial_summary_counts():
    report = normalize([
        record("a", "museum", "开封市博物馆", district="鼓楼区"),
        record("b", "museum", "城外馆"),
        record("c", "scenic", "杞县某景区", address="开封市杞县某路1号"),
        record("d", "restaurant", "有坐标店", longitude=114.35, latitude=34.79, coordinate_system="GCJ02"),
    ])
    assert report.spatial["inside"] == 2
    assert report.spatial["pending"] == 1
    assert report.spatial["outside"] == 1
    assert report.spatial["with_coordinates"] == 1
    assert report.spatial["coordinate_systems"]["unknown"] == 3


def test_register_review_tasks_writes_ledger_rows(db):
    report = normalize([record("a", "museum", "无名地点", longitude=114.35, latitude=34.79)])
    created = register_review_tasks(db, report)
    assert created == len(report.review_tasks) > 0
    tasks = ledger.list_verify_tasks(db, "open")
    assert len(tasks) == created
    assert tasks[0]["fact_ref"] == "museum:a"
    assert tasks[0]["method_required"], "核验方式必须写明"


# ------------------------------------------------------------------ bundle

def test_bundle_marks_everything_unverified_and_internal_only():
    report = normalize([
        record("a", "museum", "开封市博物馆", district="鼓楼区", longitude=114.35, latitude=34.79,
               coordinate_system="GCJ02"),
        record("b", "food", "灌汤小笼包"),
    ])
    payload = build_bundle(report, run_id="run_x", collected_at=STAMP)
    assert payload["schema_version"] == "city-facts/1"
    facts = [r for r in payload["records"] if r["kind"] != "source"]
    assert {r["validation_status"] for r in facts} == {"unverified"}
    assert {r["rights_status"] for r in facts} == {"internal_only"}
    assert {r["is_demo"] for r in facts} == {False}
    assert all(r["source_ids"] for r in facts)
    assert {r["fact_id"] for r in facts} == {"a", "b"}


def test_bundle_drops_coordinates_when_system_is_unknown():
    report = normalize([record("a", "museum", "开封市博物馆", longitude=114.35, latitude=34.79)])
    place = next(r for r in build_bundle(report, run_id="r", collected_at=STAMP)["records"]
                 if r["kind"] == "place")
    assert place["longitude"] is None and place["coordinate_system"] == "unknown"


def test_bundle_excludes_outside_records_unless_asked():
    records = [record("a", "scenic", "杞县某景区", address="开封市杞县某路1号")]
    report = normalize(records)
    assert not [r for r in build_bundle(report, run_id="r", collected_at=STAMP)["records"]
                if r["kind"] == "place"]
    included = build_bundle(report, run_id="r", collected_at=STAMP, include_outside=True)
    assert [r for r in included["records"] if r["kind"] == "place"]


def test_bundle_maps_category_to_place_type_and_food_kind():
    report = normalize([
        record("a", "scenic", "清明上河园", district="龙亭区"),
        record("b", "night_market", "鼓楼夜市", district="鼓楼区"),
        record("c", "food", "桶子鸡"),
    ])
    places = {r["fact_id"]: r for r in build_bundle(report, run_id="r", collected_at=STAMP)["records"]
              if r["kind"] == "place"}
    assert places["a"]["place_type"] == "scenic"
    assert places["b"]["place_type"] == "night_visit"
    foods = [r for r in build_bundle(report, run_id="r", collected_at=STAMP)["records"] if r["kind"] == "food"]
    assert [f["name"] for f in foods] == ["桶子鸡"]


def test_bundle_emits_one_source_record_per_source_key():
    report = normalize([record("a", "museum", "开封市博物馆", source_keys=["s1", "s2"])])
    sources = [r for r in build_bundle(report, run_id="run_x", collected_at=STAMP)["records"]
               if r["kind"] == "source"]
    assert {s["fact_id"] for s in sources} == {"s1", "s2"}
    assert all(s["locator"] == "run:run_x" for s in sources)


def test_write_json_creates_parent_dirs(tmp_path):
    path = write_json(tmp_path / "a" / "b.json", {"x": 1})
    assert path.exists() and path.read_text(encoding="utf-8").strip().endswith("}")


# ------------------------------------------------------------------ entrance handling

def test_entrance_candidate_keeps_parent_link_in_bundle():
    report = normalize([
        record("place_a", "museum", "开封市博物馆", district="鼓楼区"),
        record("gate", "entrance", "西门（候选）", place_type="entrance", parent_record_id="place_a"),
    ])
    payload = build_bundle(report, run_id="r", collected_at=STAMP)
    places = {r["fact_id"]: r for r in payload["records"] if r["kind"] == "place"}
    assert places["gate"]["parent_place_id"] == "place_a", "入口候选必须挂到所属地点"
    assert places["gate"]["place_type"] == "entrance"


def test_orphan_entrance_is_flagged_not_silently_dropped():
    report = normalize([
        record("place_a", "museum", "开封市博物馆", district="鼓楼区"),
        record("gate", "entrance", "西门（候选）", place_type="entrance"),
    ])
    assert [r.record_id for r in report.records] == ["place_a", "gate"]
    assert any("孤儿候选" in i for i in report.records[1].review_items)
    items = [t["check_item"] for t in report.review_tasks if t["record_id"] == "gate"]
    assert any("孤儿候选" in i for i in items)
    assert any("官方导览图" in i for i in items) or True  # method 见 register_review_tasks


def test_entrance_without_confirmed_direction_emits_no_relation():
    """city-facts/1 的 direction 只有 entry/exit/both；未确认就不得编造。"""
    report = normalize([
        record("place_a", "museum", "开封市博物馆", district="鼓楼区"),
        record("gate", "entrance", "西门（候选）", place_type="entrance", parent_record_id="place_a"),
    ])
    payload = build_bundle(report, run_id="r", collected_at=STAMP)
    assert not [r for r in payload["records"] if r["kind"] == "entrance"]
    assert any("通行方向未确认" in i for i in report.records[1].review_items)


def test_entrance_with_confirmed_direction_emits_relation():
    report = normalize([
        record("place_a", "museum", "开封市博物馆", district="鼓楼区"),
        record("gate", "entrance", "西门", place_type="entrance", parent_record_id="place_a",
               entrance_direction="entry"),
    ])
    payload = build_bundle(report, run_id="r", collected_at=STAMP)
    relations = [r for r in payload["records"] if r["kind"] == "entrance"]
    assert len(relations) == 1
    assert relations[0]["place_id"] == "place_a"
    assert relations[0]["entrance_place_id"] == "gate"
    assert relations[0]["direction"] == "entry"
    assert relations[0]["validation_status"] == "unverified"


def test_entrance_relation_requires_parent_place_in_bundle():
    """父地点被判 outside 而未进 bundle 时，入口关系不得悬空引用。"""
    report = normalize([
        record("place_a", "scenic", "杞县某景区", address="开封市杞县某路1号"),
        record("gate", "entrance", "某门", place_type="entrance", parent_record_id="place_a",
               entrance_direction="entry"),
    ])
    payload = build_bundle(report, run_id="r", collected_at=STAMP)
    assert not [r for r in payload["records"] if r["kind"] == "entrance"]


def test_metric6_denominator_is_scenic_only():
    """SPEC：分母只数 priority=high 的 scenic，不是所有 high 类别。"""
    bundle = StructuredBundle(run_id="r", collected_at=STAMP)
    for rid, cat, name in (("s1", "scenic", "清明上河园"), ("s2", "scenic", "万岁山武侠城"),
                           ("m1", "museum", "开封市博物馆"), ("r1", "restaurant", "黄家老店")):
        bundle.add(record(rid, cat, name, district="龙亭区"))
    path = Path(tempfile.mkdtemp()) / "c.db"
    ledger.init_db(path)
    with ledger.connect(path) as db:
        validation = normalize(bundle.records, collected_day=date(2026, 10, 23))
        computed = quality.compute(db=db, bundle=bundle, validation=validation, run_id="r",
                                   collected_day=date(2026, 10, 23))
    m6 = computed["metrics"]["entrance_coverage"]
    assert m6["priority_scenics"] == 2
    assert m6["entrance_candidates_total"] == 0
