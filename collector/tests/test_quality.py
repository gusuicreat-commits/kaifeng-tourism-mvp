"""Quality report: eight metrics, header block and markdown rendering.

Every number is recomputed from the ledger plus the structured artefacts; the
tests build a small synthetic run instead of reading the real one.
"""
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from collector import ledger, quality
from collector.extract import StructuredBundle, StructuredRecord
from collector.validate import normalize

STAMP = "2026-10-23T10:00:00+08:00"
DAY = date(2026, 10, 23)
RUN_ID = "run_quality_test"


def _record(record_id, category, name, **kwargs):
    return StructuredRecord(record_id=record_id, category=category,
                            source_keys=kwargs.pop("source_keys", []),
                            collected_at=STAMP, extraction_method="test", name=name, **kwargs)


def _raw_file(directory: Path, name: str, body: bytes) -> tuple[Path, str]:
    """Write raw evidence on disk and return (path, sha256)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(body)
    return path, hashlib.sha256(body).hexdigest()


def _build(db, evidence: Path, *, records, register=True):
    """Register raw evidence in the ledger and return a computed report."""
    bundle = StructuredBundle(run_id=RUN_ID, collected_at=STAMP)
    for item in records:
        bundle.add(item)
    validation = normalize(bundle.records, collected_day=DAY)
    report = quality.compute(db=db, bundle=bundle, validation=validation, run_id=RUN_ID,
                             collected_day=DAY)
    return report, validation


@pytest.fixture
def evidence(tmp_path):
    return tmp_path / "raw"


def test_report_header_and_totals(db, evidence):
    body = b"<html>open 9:00-17:00</html>"
    path, digest = _raw_file(evidence, "a.html", body)
    ledger.upsert_source(db, source_key="src_a", name="页面A", source_type="official",
                         content_sha256=digest, raw_path=str(path))
    report, _ = _build(db, evidence, records=[_record("p1", "museum", "开封市博物馆",
                                                      source_keys=["src_a"], district="鼓楼区")])
    assert report["schema_version"] == "kaifeng-quality-report/1"
    assert report["run_id"] == RUN_ID
    assert report["collected_day"] == DAY.isoformat()
    assert report["spec"] == "docs/DATA_QUALITY_REPORT_SPEC.md"
    assert report["totals"] == {"records": 1, "sources": 1, "duplicates": 0, "conflicts": 0,
                                "failures": 0, "review_tasks": 0}


def test_metric1_source_traceability_counts_hash_matches(db, evidence):
    body = b"<html>open 9:00-17:00</html>"
    path, digest = _raw_file(evidence, "a.html", body)
    ledger.upsert_source(db, source_key="src_a", name="A", source_type="official",
                         content_sha256=digest, raw_path=str(path))
    report, _ = _build(db, evidence, records=[
        _record("p1", "museum", "开封市博物馆", source_keys=["src_a"], district="鼓楼区"),
        _record("p2", "museum", "开封市文化馆", source_keys=["src_missing"], district="鼓楼区"),
        _record("p3", "museum", "无名馆", district="鼓楼区"),
    ])
    m1 = report["metrics"]["source_traceability"]
    assert m1["total_records"] == 3
    assert m1["with_source"] == 2
    assert m1["registry_matched"] == 1
    assert m1["hash_matched"] == 1
    assert m1["rate"] == pytest.approx(0.3333)
    assert m1["missing_source_records"] == ["p3"]
    assert m1["unregistered_source_keys"] == ["src_missing"]


def test_metric1_detects_hash_mismatch(db, evidence):
    path, _ = _raw_file(evidence, "a.html", b"<html>original</html>")
    ledger.upsert_source(db, source_key="src_a", name="A", source_type="official",
                         content_sha256="0" * 64, raw_path=str(path))
    report, _ = _build(db, evidence, records=[
        _record("p1", "museum", "开封市博物馆", source_keys=["src_a"], district="鼓楼区")])
    m1 = report["metrics"]["source_traceability"]
    assert m1["hash_matched"] == 0
    assert m1["hash_unverified_records"] == ["p1"]


def test_metric2_field_completeness_uses_category_config(db):
    report, _ = _build(db, Path("."), records=[
        _record("p1", "museum", "开封市博物馆", district="鼓楼区"),
        _record("p2", "museum", "无名馆"),
    ])
    museum = next(c for c in report["metrics"]["field_completeness"]["per_category"]
                  if c["category"] == "museum")
    assert museum["records_total"] == 2
    assert museum["required_fields"] == ["name", "district"]
    assert museum["fields_total"] == 4
    assert museum["fields_filled"] == 3, "缺失的 district 不填默认值"
    assert museum["records_complete"] == 1


def test_metric3_coordinate_readiness_ignores_unknown_systems(db):
    report, _ = _build(db, Path("."), records=[
        _record("p1", "museum", "甲馆", district="鼓楼区", longitude=114.35, latitude=34.79,
                coordinate_system="GCJ02"),
        _record("p2", "museum", "乙馆", district="鼓楼区", longitude=114.36, latitude=34.80,
                coordinate_system="unknown"),
        _record("p3", "museum", "丙馆", district="鼓楼区"),
    ])
    m3 = report["metrics"]["coordinate_readiness"]
    assert m3["places_total"] == 3 and m3["with_coords"] == 2
    assert m3["system_gcj02"] == 1 and m3["system_unknown"] == 2
    assert m3["rate"] == pytest.approx(0.3333), "未声明坐标系的坐标不计入可用坐标"


def test_metric4_flags_collection_day_as_business_date(db):
    report, _ = _build(db, Path("."), records=[
        _record("r1", "open_rule", "开放规则", business_date="2026-10-23"),
        _record("r2", "open_rule", "开放规则2", business_date="2026-11-01"),
        _record("r3", "open_rule", "开放规则3"),
    ])
    m4 = report["metrics"]["date_validity"]
    assert m4["rules_total"] == 3
    assert m4["with_business_date"] == 2
    assert m4["date_in_future"] == 1
    assert m4["date_is_collection_day"] == 1
    assert m4["collection_day_records"] == ["r1"]
    assert m4["date_null"] == 1


def test_metric5_network_evidence_is_honest_about_gaps(db):
    report, _ = _build(db, Path("."), records=[_record("p1", "museum", "开封市博物馆", district="鼓楼区")])
    m5 = report["metrics"]["network_evidence"]
    assert m5 == {"edges_total": 0, "with_source_locator": 0, "connected": 0,
                  "disconnected": 0, "unchecked": 0,
                  "note": "本轮未采园内道路与入口衔接候选；unchecked 不得声称为可导航。"}


def test_metric6_counts_only_entrances_linked_to_a_place(db):
    report, _ = _build(db, Path("."), records=[
        _record("p1", "scenic", "清明上河园", district="龙亭区"),
        _record("p2", "museum", "开封市博物馆", district="鼓楼区"),
        _record("e1", "entrance", "上善门", place_type="entrance", parent_record_id="p1"),
        _record("e2", "entrance", "西门（候选）", place_type="entrance"),
    ])
    m6 = report["metrics"]["entrance_coverage"]
    assert m6["priority_scenics"] == 2
    assert m6["with_entrance_candidate"] == 1, "未挂到地点的候选不得算覆盖"
    assert m6["entrance_candidates_total"] == 2


def test_metric7_separates_strong_supply_from_platform_leads(db):
    report, _ = _build(db, Path("."), records=[
        _record("v1", "restaurant", "黄家老店", district="鼓楼区", address="开封市鼓楼区"),
        _record("v2", "restaurant", "邢家锅贴", district="鼓楼区"),
        _record("s1", "supply", "小笼包供应", evidence_note="门店菜单照片"),
        _record("s2", "supply", "桶子鸡供应", evidence_note="平台推荐菜"),
    ])
    m7 = report["metrics"]["vendor_supply_evidence"]
    assert m7["vendors_total"] == 2 and m7["with_location"] == 1
    assert m7["supply_total"] == 2
    assert m7["evidence_strong"] == 1
    assert m7["evidence_candidate_only"] == 1, "平台推荐只算候选"


def test_metric8_reports_conflicts_without_resolving_them(db):
    report, _ = _build(db, Path("."), records=[
        _record("p1", "museum", "开封市博物馆", district="鼓楼区", opens_at="9:00"),
        _record("p2", "museum", "开封市博物馆", district="鼓楼区", opens_at="8:30"),
    ])
    m8 = report["metrics"]["conflict_handling"]
    assert m8["conflicts_total"] == 1
    assert m8["preserved_all_values"] == 1
    assert m8["resolved"] == 0
    assert m8["silently_picked"] == 0, "静默单选必须为 0"
    assert m8["items"][0]["values"] == ["8:30", "9:00"]


def test_red_lines_are_zero_and_scope_matrix_lists_present_districts(db):
    report, _ = _build(db, Path("."), records=[
        _record("p1", "museum", "开封市博物馆", district="示范区"),
        _record("p2", "museum", "无名馆"),
    ])
    assert report["red_lines"]["fabricated_data"] == 0
    assert report["red_lines"]["ai_completion"] == 0
    assert report["red_lines"]["bypass_import_channel"] == 0
    assert report["red_lines"]["bypass_waf"] == 0
    scope = report["scope"]["museum"]
    assert scope == {"城乡一体化示范区": 1, "待核验": 1}
    assert "龙亭区" not in scope, "没有记录的城区不出列"


def test_canonical_district_maps_short_names():
    assert quality.canonical_district("示范区") == "城乡一体化示范区"
    assert quality.canonical_district("顺河区") == "顺河回族区"
    assert quality.canonical_district(None) is None
    assert quality.canonical_district("未知标签") == "未知标签"


def test_gaps_are_prioritised(db):
    report, _ = _build(db, Path("."), records=[
        _record("p1", "museum", "无名馆"),
        _record("p2", "museum", "开封市博物馆", district="鼓楼区", opens_at="9:00"),
        _record("p3", "museum", "开封市博物馆", district="鼓楼区", opens_at="8:30"),
    ])
    priorities = [g["priority"] for g in report["gaps_top"]]
    assert priorities[0] == "high"
    assert any("坐标" in g["gap"] for g in report["gaps_top"])
    assert any("冲突" in g["gap"] for g in report["gaps_top"])


def test_render_markdown_matches_json_numbers(db):
    report, _ = _build(db, Path("."), records=[
        _record("p1", "museum", "开封市博物馆", district="鼓楼区"),
        _record("p2", "food", "灌汤小笼包"),
    ])
    text = quality.render_markdown(report)
    assert "run_quality_test" in text
    assert "| 类别 | 鼓楼区 | 待核验 |" in text
    assert "| museum | 1 | 0 |" in text
    assert "| food | 0 | 1 |" in text
    assert "`silently_picked`: 0" in text
    assert "`rate`: 0.0" in text


def test_write_reports_writes_both_formats(db, tmp_path):
    report, _ = _build(db, Path("."), records=[_record("p1", "museum", "开封市博物馆", district="鼓楼区")])
    md, js = quality.write_reports(report, tmp_path, day=DAY)
    assert md.name == "quality-report-2026-10-23.md" and js.name == "quality-report-2026-10-23.json"
    assert md.read_text(encoding="utf-8").startswith("# 开封主城区旅游数据质量报告")
    assert json.loads(js.read_text(encoding="utf-8"))["totals"] == report["totals"]


def test_compute_is_reproducible_for_the_same_inputs(db, evidence):
    body = b"<html>open 9:00-17:00</html>"
    path, digest = _raw_file(evidence, "a.html", body)
    ledger.upsert_source(db, source_key="src_a", name="A", source_type="official",
                         content_sha256=digest, raw_path=str(path))
    records = [_record("p1", "museum", "开封市博物馆", source_keys=["src_a"], district="鼓楼区")]
    first, _ = _build(db, evidence, records=records)
    second, _ = _build(db, evidence, records=records)
    for key in ("scope", "totals", "metrics", "red_lines", "gaps_top"):
        assert first[key] == second[key], f"{key} 必须可复算"
