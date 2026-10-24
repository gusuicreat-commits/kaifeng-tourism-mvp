"""coordinate_run: closing the artefact chain for a coordinate-backfill run.

The dry-run subprocess is stubbed so no test touches the runtime fact store.
"""
import json

import pytest

from collector import ledger
from collector import coordinate_run as cr
from collector.extract import StructuredRecord

STAMP = "2026-10-24T10:00:00+08:00"


@pytest.fixture
def run_dir(tmp_path):
    directory = tmp_path / "coord_backfill_20261024"
    (directory / "structured").mkdir(parents=True)
    bundle = {
        "schema_version": "kaifeng-collection-structured/1",
        "run_id": "coord_backfill_20261024", "collected_at": STAMP,
        "records": [
            StructuredRecord(record_id="place_a", category="restaurant", source_keys=["src_a"],
                             collected_at=STAMP, extraction_method="test", name="邢家锅贴老店（大梁门总店）",
                             longitude=114.33517, latitude=34.801001,
                             coordinate_system="GCJ02", district="龙亭区").to_dict(),
            StructuredRecord(record_id="place_b", category="museum", source_keys=["src_a"],
                             collected_at=STAMP, extraction_method="test", name="开封市博物馆",
                             district="待核验").to_dict(),
        ],
    }
    (directory / "structured" / "structured.json").write_text(
        json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return directory


def test_finish_writes_full_artefact_chain(run_dir, db_path, monkeypatch):
    monkeypatch.setattr(cr, "_dry_run", lambda path: {"status": "dry_run", "received": 3, "changed": 3})
    summary = cr.finish(run_dir, db_path=db_path)
    assert summary["records"] == 2
    assert summary["dry_run"] == {"status": "dry_run", "received": 3, "changed": 3}
    assert (run_dir / "structured" / "validation.json").exists()
    assert (run_dir / "bundle" / "city-facts-candidate.json").exists()
    assert (run_dir / "reports" / "city-facts-dry-run.json").exists()
    assert (run_dir / "reports" / "quality-report-2026-10-24.md").exists()
    assert (run_dir / "reports" / "run-summary.json").exists()
    assert summary["coordinate_systems"] == {"GCJ02": 1, "unknown": 1}


def test_finish_keeps_coordinates_and_their_system(run_dir, db_path, monkeypatch):
    monkeypatch.setattr(cr, "_dry_run", lambda path: None)
    cr.finish(run_dir, db_path=db_path)
    payload = json.loads((run_dir / "bundle" / "city-facts-candidate.json").read_text(encoding="utf-8"))
    placed = [r for r in payload["records"] if r.get("fact_id") == "place_a"]
    assert placed[0]["longitude"] == 114.33517
    assert placed[0]["coordinate_system"] == "GCJ02"
    unplaced = [r for r in payload["records"] if r.get("fact_id") == "place_b"]
    assert unplaced[0]["longitude"] is None
    assert unplaced[0]["coordinate_system"] == "unknown"


def test_finish_registers_run_and_review_tasks(run_dir, db_path, monkeypatch):
    monkeypatch.setattr(cr, "_dry_run", lambda path: None)
    summary = cr.finish(run_dir, db_path=db_path)
    with ledger.connect(db_path) as db:
        run = ledger.get_run(db, "coord_backfill_20261024")
        assert run["status"] == "success"
        assert run["output_dir"] == str(run_dir)
        assert run["report_path"].endswith("quality-report-2026-10-24.md")
        open_tasks = ledger.list_verify_tasks(db, status="open")
    assert summary["review_tasks_created"] == len(open_tasks)


def test_finish_is_idempotent_for_review_tasks(run_dir, db_path, monkeypatch):
    monkeypatch.setattr(cr, "_dry_run", lambda path: None)
    cr.finish(run_dir, db_path=db_path)
    cr.finish(run_dir, db_path=db_path)
    with ledger.connect(db_path) as db:
        assert len(ledger.list_verify_tasks(db, status="open")) == 1


def test_finish_rejects_missing_structured_artefact(tmp_path, db_path):
    with pytest.raises(FileNotFoundError):
        cr.finish(tmp_path / "absent", db_path=db_path)


def test_dry_run_reports_rejection(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "import_city_facts.py").write_text(
        "import json,sys;print(json.dumps({'status':'rejected','error':'x'}))", encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)
    result = cr._dry_run(tmp_path / "bundle.json")
    assert result == {"status": "rejected", "error": "x"}


def test_dry_run_returns_none_when_script_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)
    assert cr._dry_run(tmp_path / "bundle.json") is None
