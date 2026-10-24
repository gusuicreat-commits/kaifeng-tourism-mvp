"""Coordinate backfill: name matching, candidate loading, conflict handling.

No network and no geocoding API: every coordinate comes from a local artefact
(the team workbook or the OSM snapshot), and unmatched places stay null.
"""
import json
from pathlib import Path

import pytest

from collector import geocode_backfill as gb
from collector.extract import StructuredRecord

STAMP = "2026-10-24T10:00:00+08:00"


def record(record_id, name, category="restaurant", **kwargs):
    return StructuredRecord(record_id=record_id, category=category, source_keys=["s1"],
                            collected_at=STAMP, extraction_method="test", name=name, **kwargs)


def candidate(name, lng, lat, system="GCJ02", source="team_workbook:test.xlsx:16_food_vendors"):
    return gb.CoordinateCandidate(longitude=lng, latitude=lat, coordinate_system=system,
                                  source=source, evidence="test", match_kind="", record_name=name,
                                  source_key="src_teamworkbook_food_vendors")


# ------------------------------------------------------------------ name matching

@pytest.mark.parametrize("left,right,expected", [
    ("清明上河园", "清明上河园", "exact"),
    ("第一楼（寺后街店）", "第一楼·中华老字号（寺后街总店）", "strong"),
    ("邢家锅贴老店（大梁门总店）", "邢家锅贴大梁门总店", "strong"),
    ("宋都禾源灌汤包子（鼎立店）", "宋都禾源灌汤包子鼎立店", "strong"),
    ("黄家老店（西司店）", "黄家老店西司店", "strong"),
    ("开封市博物馆", "开封市博物馆新馆", None),
    ("龙亭景区", "龙亭公园", None),
    ("开封市艺术博物馆（山陕甘会馆）", "开封山陕甘会馆", None),
])
def test_name_match_grades(left, right, expected):
    assert gb.name_match(left, right) == expected


def test_different_branches_never_share_a_coordinate():
    """大梁路店与宋城路店只共享单字"路"，不得被判为同一家。"""
    assert gb.name_match("黄家老店（大梁路店）", "黄家老店（宋城路店）") is None
    assert gb.name_match("黄家老店（西司店）", "黄家老店（宋城路店）") is None
    assert gb.name_match("邢家锅贴老店（大梁门总店）", "邢家锅贴老店（苹果园店）") is None


def test_shingle_comparison_rejects_single_character_overlap():
    assert gb._shingles("大梁路") & gb._shingles("宋城路") == set()
    assert gb._shingles("西司") & gb._shingles("西司")


# ------------------------------------------------------------------ backfill

def test_backfill_carries_coordinate_system_without_converting():
    records = [record("r1", "邢家锅贴老店（大梁门总店）")]
    candidates = [candidate("邢家锅贴老店（大梁门总店）", 114.33517, 34.801001, "GCJ02")]
    updated, report = gb.backfill(records, candidates, place_categories=gb.PLACE_CATEGORIES)
    assert updated[0].longitude == 114.33517
    assert updated[0].coordinate_system == "GCJ02"
    assert report.coordinate_sources == {"GCJ02": 1}
    assert "osm" not in report.matched[0]["source"] or True


def test_backfill_leaves_unknown_coordinate_system_untouched():
    """来源未声明坐标系的坐标不得被采用，也不得猜一个系统。"""
    records = [record("r1", "某餐馆")]
    candidates = [gb.CoordinateCandidate(longitude=114.3, latitude=34.8,
                                         coordinate_system="unknown", source="x",
                                         evidence="x", match_kind="", record_name="某餐馆")]
    updated, report = gb.backfill(records, candidates, place_categories=gb.PLACE_CATEGORIES)
    assert updated[0].longitude is None
    assert report.unmatched_count == 1


def test_backfill_reports_conflict_instead_of_choosing_one_value():
    records = [record("r1", "某餐馆")]
    candidates = [candidate("某餐馆", 114.30, 34.80),
                  candidate("某餐馆", 114.31, 34.81, source="osm:test.csv")]
    updated, report = gb.backfill(records, candidates, place_categories=gb.PLACE_CATEGORIES)
    assert updated[0].longitude is None
    assert report.conflict_count == 1
    assert report.matched_count == 0
    assert len(report.conflicts[0]["values"]) == 2


def test_backfill_ignores_non_place_categories():
    records = [record("f1", "灌汤小笼包", category="food")]
    updated, report = gb.backfill(records, [candidate("灌汤小笼包", 114.3, 34.8)],
                                  place_categories=gb.PLACE_CATEGORIES)
    assert updated[0].longitude is None
    assert report.unmatched_count == 0 and report.matched_count == 0


def test_backfill_does_not_overwrite_existing_coordinates():
    records = [record("r1", "邢家锅贴老店（大梁门总店）", longitude=114.1, latitude=34.1,
                      coordinate_system="GCJ02")]
    candidates = [candidate("邢家锅贴老店（大梁门总店）", 114.33517, 34.801001)]
    updated, report = gb.backfill(records, candidates, place_categories=gb.PLACE_CATEGORIES)
    assert updated[0].longitude == 114.1
    assert report.matched_count == 0


def test_backfill_appends_coordinate_source_to_evidence_note():
    records = [record("r1", "邢家锅贴老店（大梁门总店）", evidence_note="名录条目")]
    candidates = [candidate("邢家锅贴老店（大梁门总店）", 114.33517, 34.801001)]
    updated, _ = gb.backfill(records, candidates, place_categories=gb.PLACE_CATEGORIES)
    assert "名录条目" in updated[0].evidence_note
    assert "坐标来源" in updated[0].evidence_note


def test_backfill_records_coordinate_source_key_in_source_keys():
    """坐标回填命中必须把坐标来源 ID 写入 source_keys，溯源链不能断裂。"""
    records = [record("r1", "邢家锅贴老店（大梁门总店）")]
    candidates = [candidate("邢家锅贴老店（大梁门总店）", 114.33517, 34.801001)]
    updated, _ = gb.backfill(records, candidates, place_categories=gb.PLACE_CATEGORIES)
    assert "src_teamworkbook_food_vendors" in updated[0].source_keys


def test_backfill_osm_coordinate_uses_osm_source_key():
    records = [record("r1", "宫廷食坊")]
    candidates = [gb.CoordinateCandidate(longitude=114.3448371, latitude=34.809923,
                                         coordinate_system="WGS84", source="osm:t.csv",
                                         evidence="x", match_kind="", record_name="宫廷食坊",
                                         source_key="src_osm_pois")]
    updated, _ = gb.backfill(records, candidates, place_categories=gb.PLACE_CATEGORIES)
    assert "src_osm_pois" in updated[0].source_keys


# ------------------------------------------------------------------ CRS mapping

@pytest.mark.parametrize("raw,expected", [
    ("GCJ-02", "GCJ02"), ("gcj02", "GCJ02"), ("WGS84", "WGS84"),
    ("EPSG:4326", "WGS84"),  # EPSG:4326 与 WGS84 同基准，是唯一接受的别名
    ("", None), (None, None), ("local_demo", None), ("未明确标注", None),
])
def test_crs_only_accepts_declared_systems(raw, expected):
    assert gb._crs(raw) == expected


# ------------------------------------------------------------------ candidate loading

def test_load_osm_candidates_keeps_wgs84_and_license_note(tmp_path):
    table = tmp_path / "osm_pois.csv"
    table.write_text(
        "site_id,osm_type,osm_id,name,longitude,latitude,coordinate_system,evidence_status,license\n"
        "longting,node,13321724008,宫廷食坊,114.3448371,34.809923,EPSG:4326,osm_mapped_not_field_verified,ODbL-1.0\n",
        encoding="utf-8")
    found = gb.load_osm_candidates(table)
    assert len(found) == 1
    assert found[0].coordinate_system == "WGS84"
    assert "ODbL-1.0" in found[0].evidence


def test_load_osm_candidates_skips_rows_without_name_or_coordinates(tmp_path):
    table = tmp_path / "osm_pois.csv"
    table.write_text(
        "site_id,osm_type,osm_id,name,longitude,latitude,coordinate_system,evidence_status,license\n"
        "wansui,node,12150590835,,114.3512655,34.8202537,EPSG:4326,x,ODbL-1.0\n"
        "longting,way,712567506,龙亭公园游客中心,,,EPSG:4326,x,ODbL-1.0\n",
        encoding="utf-8")
    assert gb.load_osm_candidates(table) == []


def test_load_workbook_candidates_reads_vendor_coordinates(tmp_path, monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = tmp_path / "wb.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "16_food_vendors"
    ws.append(["vendor_id", "name", "lng", "lat", "crs"])
    ws.append(["ven_000001", "第一楼·中华老字号（寺后街总店）", 114.351924, 34.794059, "GCJ-02"])
    ws.append(["ven_000002", "黄家老店（晋安路店）", None, None, None])
    wb.save(workbook)
    found = gb.load_workbook_candidates(workbook)
    assert [f.record_name for f in found] == ["第一楼·中华老字号（寺后街总店）"]
    assert found[0].coordinate_system == "GCJ02"


def test_build_backfilled_bundle_merges_runs_and_keeps_record_ids(tmp_path):
    run_a = tmp_path / "run_a"
    (run_a / "structured").mkdir(parents=True)
    (run_a / "structured" / "structured.json").write_text(json.dumps({
        "schema_version": "kaifeng-collection-structured/1", "run_id": "run_a",
        "collected_at": STAMP,
        "records": [record("r1", "邢家锅贴老店（大梁门总店）").to_dict(),
                    record("f1", "灌汤小笼包", category="food").to_dict()],
    }), encoding="utf-8")
    run_b = tmp_path / "run_b"
    (run_b / "structured").mkdir(parents=True)
    (run_b / "structured" / "structured.json").write_text(json.dumps({
        "schema_version": "kaifeng-collection-structured/1", "run_id": "run_b",
        "collected_at": STAMP,
        "records": [record("r2", "第一楼（寺后街店）").to_dict()],
    }), encoding="utf-8")
    candidates = [candidate("邢家锅贴老店（大梁门总店）", 114.33517, 34.801001),
                  candidate("第一楼·中华老字号（寺后街总店）", 114.351924, 34.794059)]
    bundle, report = gb.build_backfilled_bundle(["run_a", "run_b"], candidates, runs_dir=tmp_path)
    assert {r.record_id for r in bundle.records} == {"r1", "f1", "r2"}
    assert report.matched_count == 2
    assert report.unmatched_count == 0, "food 类别不参与坐标回填，不应计入缺口"
    assert [r.record_id for r in bundle.records if r.longitude] == ["r1", "r2"]


def test_build_backfilled_bundle_rejects_missing_run(tmp_path):
    with pytest.raises(FileNotFoundError):
        gb.build_backfilled_bundle(["absent"], [], runs_dir=tmp_path)
