"""Report entry point: reading a run directory, recomputing, writing artefacts."""
import json
from datetime import date
from pathlib import Path

import pytest

from collector import ledger, report as report_mod


def _write_run(run_dir: Path, *, run_id="run_report_test", day="2026-10-23"):
    (run_dir / "structured").mkdir(parents=True, exist_ok=True)
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "kaifeng-collection-structured/1", "run_id": run_id,
        "collected_at": f"{day}T10:00:00+08:00",
        "records": [
            {"record_id": "p1", "category": "museum", "source_keys": ["src_a"], "collected_at":
             f"{day}T10:00:00+08:00", "extraction_method": "manual_reviewed_extraction",
             "name": "开封市博物馆", "district": "鼓楼区", "place_type": "poi",
             "raw_path": None},
        ],
    }
    (run_dir / "structured" / "structured.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def test_load_structured_round_trips(runs_dir):
    run_dir = runs_dir / "run_report_test"
    _write_run(run_dir)
    bundle = report_mod.load_structured(run_dir)
    assert bundle.run_id == "run_report_test"
    assert [r.name for r in bundle.records] == ["开封市博物馆"]


def test_load_structured_missing_artefact_is_a_clear_error(runs_dir):
    with pytest.raises(FileNotFoundError, match="缺少结构化产物"):
        report_mod.load_structured(runs_dir / "run_absent")


def test_load_dry_run_summarises_counts(runs_dir):
    run_dir = runs_dir / "run_report_test"
    (run_dir / "reports").mkdir(parents=True)
    (run_dir / "reports" / "city-facts-dry-run.json").write_text(json.dumps({
        "status": "dry_run", "received": 71, "changed": 71,
        "records": [{"ready": False, "issues": ["UNVERIFIED"]}, {"ready": False, "issues": []}],
    }), encoding="utf-8")
    summary = report_mod.load_dry_run(run_dir)
    assert summary == {"status": "dry_run", "received": 71, "changed": 71, "ready": 0, "issues": 2}


def test_load_dry_run_returns_none_when_missing_or_broken(runs_dir):
    run_dir = runs_dir / "run_report_test"
    (run_dir / "reports").mkdir(parents=True)
    assert report_mod.load_dry_run(run_dir) is None
    (run_dir / "reports" / "city-facts-dry-run.json").write_text("not json", encoding="utf-8")
    assert report_mod.load_dry_run(run_dir) is None


def test_generate_keeps_collection_counters_intact(tmp_path, db_path, runs_dir):
    run_id = "run_report_test"
    _write_run(runs_dir / run_id, run_id=run_id)
    ledger.init_db(db_path)
    with ledger.connect(db_path) as db:
        ledger.ensure_run(db, run_id=run_id, params={}, output_dir=runs_dir)
        ledger.finish_run(db, run_id, status="success", items_fetched=7, items_new=5,
                          items_failed=1, error_summary="waf_block")
    before = None
    with ledger.connect(db_path) as db:
        before = ledger.get_run(db, run_id)

    report_mod.generate(run_id=run_id, db_path=db_path, runs_dir=runs_dir,
                        collected_day=date(2026, 10, 23))

    with ledger.connect(db_path) as db:
        after = ledger.get_run(db, run_id)
    assert after["items_fetched"] == before["items_fetched"] == 7
    assert after["items_new"] == before["items_new"] == 5
    assert after["items_failed"] == before["items_failed"] == 1
    assert after["status"] == before["status"] == "success"
    assert after["error_summary"] == "waf_block"
    assert after["report_path"].endswith("quality-report-2026-10-23.md")


def test_generate_writes_reports_and_updates_ledger(tmp_path, db_path, runs_dir):
    run_id = "run_report_test"
    _write_run(runs_dir / run_id, run_id=run_id)
    evidence = tmp_path / "raw"
    evidence.mkdir()
    body = b"<html>open 9:00-17:00</html>"
    (evidence / "a.html").write_bytes(body)
    import hashlib
    ledger.init_db(db_path)
    with ledger.connect(db_path) as db:
        ledger.upsert_source(db, source_key="src_a", name="页面A", source_type="official",
                             content_sha256=hashlib.sha256(body).hexdigest(),
                             raw_path=str(evidence / "a.html"))

    report, md, js = report_mod.generate(run_id=run_id, db_path=db_path, runs_dir=runs_dir,
                                        collected_day=date(2026, 10, 23))
    assert md == runs_dir / run_id / "reports" / "quality-report-2026-10-23.md"
    assert js.exists()
    assert report["totals"]["records"] == 1
    assert report["metrics"]["source_traceability"]["hash_matched"] == 1
    assert "`silently_picked`: 0" in md.read_text(encoding="utf-8")

    with ledger.connect(db_path) as db:
        run = ledger.get_run(db, run_id)
    assert run["report_path"] == str(md)
    assert run["status"] == "running", "重跑报告不得改写采集运行的状态与统计"


def test_generate_is_idempotent(tmp_path, db_path, runs_dir):
    run_id = "run_report_test"
    _write_run(runs_dir / run_id, run_id=run_id)
    ledger.init_db(db_path)
    first, _, _ = report_mod.generate(run_id=run_id, db_path=db_path, runs_dir=runs_dir,
                                     collected_day=date(2026, 10, 23))
    second, _, _ = report_mod.generate(run_id=run_id, db_path=db_path, runs_dir=runs_dir,
                                       collected_day=date(2026, 10, 23))
    for key in ("scope", "totals", "metrics", "red_lines", "gaps_top"):
        assert first[key] == second[key], "报告必须幂等"


def test_generate_derives_day_from_structured_artefact(db_path, runs_dir):
    run_id = "run_report_test"
    _write_run(runs_dir / run_id, run_id=run_id, day="2026-11-05")
    ledger.init_db(db_path)
    report, _, _ = report_mod.generate(run_id=run_id, db_path=db_path, runs_dir=runs_dir)
    assert report["collected_day"] == "2026-11-05"


def test_main_reports_rejection_for_missing_run(tmp_path, capsys):
    code = report_mod.main(["--run-id", "run_absent", "--database", str(tmp_path / "c.db"),
                            "--runs-dir", str(tmp_path / "runs")])
    assert code == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "rejected"
    assert "结构化产物" in payload["error"]


def test_main_prints_ok_summary(tmp_path, capsys, db_path, runs_dir):
    run_id = "run_report_test"
    _write_run(runs_dir / run_id, run_id=run_id)
    ledger.init_db(db_path)
    code = report_mod.main(["--run-id", run_id, "--database", str(db_path),
                            "--runs-dir", str(runs_dir)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["totals"]["records"] == 1
    assert payload["markdown"].endswith("quality-report-2026-10-23.md")
