"""Target-day open-rule instantiation.

The workbook stores recurring rules; city-facts/1 needs one concrete
business_date. These tests pin the boundaries: no default date, weekday and
effective-window filtering, cross-midnight ranges left as gaps, and unknown
enums never becoming false.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from collector import open_rules_instantiator as ori


def rule(rule_id="or_000001", place_id="pl_000001", **kwargs):
    base = dict(rule_id=rule_id, place_id=place_id, rule_name="常规开放时间", rule_type="daily",
                effective_from=None, effective_to=None, weekdays={1, 2, 3, 4, 5, 6, 7},
                open_time="09:00", close_time="18:00", last_entry_time=None,
                timezone_text="Asia/Shanghai (+08:00)", ticket_scope=None,
                reservation_required="unknown", reentry_allowed="unknown",
                special_rules=None, source_id="src_000002", validation_status="candidate",
                notes=None)
    base.update(kwargs)
    return ori.RuleRow(**base)


# ------------------------------------------------------------------ parsing

@pytest.mark.parametrize("raw,expected", [
    ("09:00", "09:00"), ("9:00", "09:00"), ("23:59", "23:59"),
    ("24:00", None), ("9点", None), ("", None), (None, None), ("09:00:00", None),
])
def test_parse_time_rejects_unparsable_values(raw, expected):
    assert ori._parse_time(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("1;2;3", {1, 2, 3}), ("1,2", {1, 2}), ("1 2 3", {1, 2, 3}), ("1;2;8", {1, 2}),
    ("", set()), (None, set()), ("0;7", {7}),
])
def test_parse_weekdays(raw, expected):
    assert ori._parse_weekdays(raw) == expected


def test_parse_date_normalises_and_rejects_junk():
    assert ori._parse_date("2026-11-01") == "2026-11-01"
    assert ori._parse_date("2026-11-1") == "2026-11-01"
    assert ori._parse_date("11月1日") is None
    assert ori._parse_date(None) is None


# ------------------------------------------------------------------ applicability

def test_daily_rule_applies_on_any_weekday():
    assert ori.applies_on(rule(), date(2026, 10, 24)) is None
    assert ori.applies_on(rule(), date(2026, 10, 25)) is None


def test_weekday_filter_excludes_non_matching_days():
    weekday_rule = rule(weekdays={6, 7})
    assert ori.applies_on(weekday_rule, date(2026, 10, 24)) is None  # Saturday
    reason = ori.applies_on(weekday_rule, date(2026, 10, 26))       # Monday
    assert reason and "星期不适用" in reason


def test_date_range_window_is_enforced():
    seasonal = rule(rule_type="date_range", effective_from="2026-11-01", effective_to="2027-03-31")
    assert "未开始" in ori.applies_on(seasonal, date(2026, 10, 24))
    assert ori.applies_on(seasonal, date(2026, 12, 1)) is None
    assert "已结束" in ori.applies_on(seasonal, date(2027, 4, 1))


@pytest.mark.parametrize("status", ["expired", "cancelled", "rejected", "mock"])
def test_inapplicable_statuses_are_not_instantiated(status):
    assert ori.applies_on(rule(validation_status=status), date(2026, 10, 24))


def test_conflict_status_is_a_gap_not_instantiated():
    """字段冲突的规则不是失效，而是待人工裁定；不得落库，转核验。"""
    assert ori.applies_on(rule(validation_status="conflict"), date(2026, 10, 24))


def test_missing_weekday_declaration_blocks_instantiation():
    records, gaps = ori.instantiate(rule(weekdays=set()), date(2026, 10, 24),
                                    place_ids={"pl_wb_000001"}, source_ids={"src_teamworkbook_open_rules"})
    assert records == []
    assert gaps and "适用星期" in gaps[0]["reason"]


# ------------------------------------------------------------------ instantiation

def test_instantiation_emits_concrete_business_date_and_times():
    records, gaps = ori.instantiate(rule(), date(2026, 10, 24),
                                    place_ids={"pl_wb_000001"},
                                    source_ids={"src_wb_src_000002"})
    assert gaps == []
    assert len(records) == 1
    record = records[0]
    assert record["kind"] == "open_rule"
    assert record["business_date"] == "2026-10-24"
    assert record["opens_at"] == "2026-10-24T09:00:00+08:00"
    assert record["closes_at"] == "2026-10-24T18:00:00+08:00"
    assert record["place_id"] == "pl_wb_000001"
    assert record["validation_status"] == "unverified"
    assert record["rights_status"] == "internal_only"
    assert record["is_demo"] is False


def test_last_entry_must_fall_inside_the_range():
    _, gaps = ori.instantiate(rule(last_entry_time="19:00"), date(2026, 10, 24),
                              place_ids={"pl_wb_000001"}, source_ids=set())
    assert gaps and "停止入园" in gaps[0]["reason"]


def test_cross_midnight_range_is_a_gap_not_a_guess():
    """夜市 18:30-01:00 必须留缺口，不得塞进同一个业务日期。"""
    records, gaps = ori.instantiate(rule(open_time="18:30", close_time="01:00"),
                                    date(2026, 10, 24), place_ids={"pl_wb_000001"},
                                    source_ids=set())
    assert records == []
    assert gaps and "跨日" in gaps[0]["reason"]
    assert gaps[0]["suggested_split"]


def test_incomplete_range_is_a_gap():
    _, gaps = ori.instantiate(rule(open_time="09:00", close_time=None), date(2026, 10, 24),
                              place_ids={"pl_wb_000001"}, source_ids=set())
    assert gaps and "起止不完整" in gaps[0]["reason"]


def test_unknown_place_is_a_gap():
    records, gaps = ori.instantiate(rule(), date(2026, 10, 24),
                                    place_ids={"pl_wb_000002"}, source_ids=set())
    assert records == []
    assert gaps and gaps[0]["reason"]


@pytest.mark.parametrize("raw,expected", [
    ("true", "required"), ("false", "not_required"), ("unknown", "unknown"),
    (None, "unknown"), ("TRUE", "required"), ("", "unknown"),
])
def test_reservation_mapping_never_defaults_to_false(raw, expected):
    records, _ = ori.instantiate(rule(reservation_required=raw), date(2026, 10, 24),
                                 place_ids={"pl_wb_000001"}, source_ids=set())
    assert records[0]["reservation"] == expected


@pytest.mark.parametrize("raw,expected", [
    ("true", "allowed"), ("false", "forbidden"), ("unknown", "unknown"), (None, "unknown"),
])
def test_reentry_mapping_never_defaults_to_false(raw, expected):
    records, _ = ori.instantiate(rule(reentry_allowed=raw), date(2026, 10, 24),
                                 place_ids={"pl_wb_000001"}, source_ids=set())
    assert records[0]["reentry"] == expected


def test_ticket_scope_is_clipped_and_optional():
    records, _ = ori.instantiate(rule(ticket_scope="日场票"), date(2026, 10, 24),
                                 place_ids={"pl_wb_000001"}, source_ids=set())
    assert records[0]["ticket_scope"] == "日场票"
    records, _ = ori.instantiate(rule(ticket_scope=None), date(2026, 10, 24),
                                 place_ids={"pl_wb_000001"}, source_ids=set())
    assert "ticket_scope" not in records[0]


def test_ticket_scope_over_200_is_a_gap_not_truncated():
    """超 200 字上限不得静默截断，应转核验。"""
    long_scope = "票" * 201
    records, gaps = ori.instantiate(rule(ticket_scope=long_scope), date(2026, 10, 24),
                                    place_ids={"pl_wb_000001"}, source_ids=set())
    assert records == []
    assert gaps and "ticket_scope 超 200 字" in gaps[0]["reason"]
    assert gaps[0]["ticket_scope_len"] == 201


def test_source_ids_fall_back_to_the_workbook_table_source():
    records, _ = ori.instantiate(rule(source_id=None), date(2026, 10, 24),
                                 place_ids={"pl_wb_000001"}, source_ids=set())
    assert records[0]["source_ids"] == ["src_teamworkbook_open_rules"]


# ------------------------------------------------------------------ workbook loading

def _write_workbook(path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = ori.SHEET
    sheet.append(["12_open_rules 说明行", None])
    sheet.append(["表头在下一行", None])
    sheet.append(["open_rule_id", "place_id", "rule_name", "rule_type", "effective_from",
                  "effective_to", "weekdays", "open_time", "close_time", "last_entry_time",
                  "timezone", "ticket_scope", "reservation_required", "reentry_allowed",
                  "special_rules", "source_id", "validation_status", "verified_at", "version", "notes"])
    sheet.append(["or_000001", "pl_000001", "常规开放时间", "daily", None, None, "1;2;3;4;5;6;7",
                  "09:00", "18:00", None, "Asia/Shanghai (+08:00)", "日场票", "true", "unknown",
                  None, "src_000002", "candidate", None, 1, None])
    sheet.append(["or_000002", "pl_000002", "冬季时间", "date_range", "2026-11-01", "2027-03-31",
                  "1;2;3;4;5;6;7", "08:00", "17:30", "17:00", "Asia/Shanghai (+08:00)", None,
                  "unknown", "unknown", None, "src_000023", "pending_review", None, 1, None])
    sheet.append([None] * 20)
    workbook.save(path)


def test_load_rule_rows_finds_header_row_and_skips_blank_rows(tmp_path):
    workbook = tmp_path / "wb.xlsx"
    _write_workbook(workbook)
    rows = ori.load_rule_rows(workbook)
    assert [r.rule_id for r in rows] == ["or_000001", "or_000002"]
    assert rows[0].weekdays == {1, 2, 3, 4, 5, 6, 7}
    assert rows[0].open_time == "09:00"
    assert rows[1].effective_from == "2026-11-01"
    assert rows[1].last_entry_time == "17:00"


def test_run_builds_bundle_with_dependency_subset(tmp_path):
    team = {
        "schema_version": "city-facts/1",
        "records": [
            {"fact_id": "src_teamworkbook_places", "kind": "source", "version": 1, "source_ids": [],
             "validation_status": "unverified", "business_status": "active", "is_demo": False,
             "rights_status": "internal_only", "name": "places", "source_type": "other",
             "url": None, "locator": "wb#places", "publisher": "p", "collected_at": "2026-10-23T22:49:00+08:00"},
            {"fact_id": "src_wb_src_000002", "kind": "source", "version": 1, "source_ids": [],
             "validation_status": "unverified", "business_status": "active", "is_demo": False,
             "rights_status": "internal_only", "name": "s", "source_type": "official",
             "url": None, "locator": "wb#s", "publisher": "p", "collected_at": "2026-10-23T22:49:00+08:00"},
            {"fact_id": "pl_wb_000001", "kind": "place", "version": 1, "source_ids": ["src_wb_src_000002"],
             "validation_status": "unverified", "business_status": "active", "is_demo": False,
             "rights_status": "internal_only", "name": "清明上河园", "place_type": "scenic",
             "parent_place_id": None, "address": None, "longitude": None, "latitude": None,
             "coordinate_system": "unknown"},
        ],
    }
    team_path = tmp_path / "team.json"
    team_path.write_text(json.dumps(team), encoding="utf-8")
    workbook = tmp_path / "wb.xlsx"
    _write_workbook(workbook)

    bundle, report = ori.run(workbook, date(2026, 10, 24), team_bundle=team_path)
    kinds = {}
    for record in bundle["records"]:
        kinds[record["kind"]] = kinds.get(record["kind"], 0) + 1
    assert kinds.get("open_rule") == 1
    assert kinds.get("source") >= 2, "被引用的来源必须自带，dry-run 才不会报引用缺失"
    assert kinds.get("place") == 1, "open_rule 挂靠的 place 必须自带"
    assert report.business_date == "2026-10-24"
    assert len(report.applicable) == 1
    assert len(report.gaps) == 1  # or_000002 的生效区间未开始
    assert report.applicable[0]["opens_at"] == "2026-10-24T09:00:00+08:00"
