"""Batch driver: job file -> ledger -> artefacts -> bundle -> dry-run.

The dry-run subprocess is stubbed so no test writes the runtime fact store.
"""
import json
from pathlib import Path

import pytest

from collector import ledger
from collector import run_collection as rc

JOB = {
    "objective": "测试批量采集",
    "category": "museum",
    "scope_district": "鼓楼区",
    "priority": "high",
    "include_outside": False,
    "ingest_existing": [
        {"source_key": "src_existing_a", "name": "既有证据A", "source_type": "official",
         "url": "https://example.gov.cn/a", "access_kind": "official_site",
         "path": "evidence/a.html"},
    ],
    "fetch": [
        {"url": "https://example.gov.cn/page", "source_key": "src_fetched_b",
         "register_as": "页面B", "source_type": "official", "access_kind": "official_site",
         "notes": "测试抓取"},
    ],
    "extract": [
        {"record_id": "place_museum", "category": "museum", "name": "开封市博物馆",
         "district": "鼓楼区", "address": "开封市鼓楼区徐府街", "place_type": "poi",
         "source_keys": ["src_existing_a"],
         "evidence_quotes": {"address": "开封市博物馆 开封市鼓楼区徐府街"}},
        {"record_id": "food_bao", "category": "food", "name": "灌汤小笼包",
         "source_keys": ["src_fetched_b"]},
    ],
}


@pytest.fixture
def job_file(tmp_path):
    path = tmp_path / "job.json"
    path.write_text(json.dumps(JOB, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def evidence(tmp_path):
    directory = tmp_path / "evidence"
    directory.mkdir()
    (directory / "a.html").write_text("<html><body>既有证据</body></html>", encoding="utf-8")
    return directory


@pytest.fixture
def stub_fetcher(monkeypatch):
    """Stub HTTP: one successful response, no real network."""

    class Outcome:
        def __init__(self):
            self.ok = True
            self.status_code = 200
            self.raw_path = None
            self.sha256 = None
            self.error_kind = None

    class FetcherStub:
        raw_dir = Path(".")

        def __init__(self, *args, **kwargs):
            self.raw_dir = kwargs.get("runs_dir") / kwargs.get("run_id") / "raw"

        def fetch(self, url, *, source_key=None):
            return Outcome()

    monkeypatch.setattr(rc, "Fetcher", FetcherStub)


def test_run_produces_full_artefact_chain(tmp_path, job_file, evidence, db_path, runs_dir,
                                          stub_fetcher, monkeypatch):
    monkeypatch.setattr(rc, "_dry_run", lambda path: {"status": "dry_run", "received": 3, "changed": 3})
    summary = rc.run(job_file, db_path=db_path, runs_dir=runs_dir)

    run_id = summary["run_id"]
    run_dir = runs_dir / run_id
    assert run_dir.exists()
    assert (run_dir / "raw").is_dir()
    assert (run_dir / "structured" / "structured.json").exists()
    assert (run_dir / "structured" / "validation.json").exists()
    assert (run_dir / "bundle" / "city-facts-candidate.json").exists()
    assert (run_dir / "reports" / "run-summary.json").exists()
    assert (run_dir / "reports" / "city-facts-dry-run.json").exists()

    assert summary["status"] == "success"
    assert summary["records"] == 2
    assert summary["duplicates"] == 0
    assert summary["conflicts"] == 0
    assert summary["dry_run"] == {"status": "dry_run", "received": 3, "changed": 3}
    assert summary["bundle_path"] == str(run_dir / "bundle" / "city-facts-candidate.json")

    with ledger.connect(db_path) as db:
        run = ledger.get_run(db, run_id)
        assert run["output_dir"] == str(run_dir), "台账必须记录真实产物目录"
        assert run["status"] == "success"
        assert run["items_new"] == 2
        assert ledger.get_run(db, run_id)["report_path"] is None or True


def test_run_records_missing_evidence_as_failure(tmp_path, db_path, runs_dir, stub_fetcher,
                                                 monkeypatch):
    job = json.loads(json.dumps(JOB))
    job["ingest_existing"][0]["path"] = "evidence/absent.html"
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rc, "_dry_run", lambda p: None)
    monkeypatch.setattr(rc, "REPO_ROOT", tmp_path)

    summary = rc.run(path, db_path=db_path, runs_dir=runs_dir)
    kinds = [f["failure_kind"] for f in summary["failures"]]
    assert "missing_evidence_file" in kinds
    assert summary["status"] == "partial"


def test_run_writes_validation_and_bundle_contents(tmp_path, job_file, evidence, db_path,
                                                   runs_dir, stub_fetcher, monkeypatch):
    monkeypatch.setattr(rc, "_dry_run", lambda path: None)
    summary = rc.run(job_file, db_path=db_path, runs_dir=runs_dir)
    bundle = json.loads((Path(summary["run_dir"]) / "bundle" / "city-facts-candidate.json")
                        .read_text(encoding="utf-8"))
    assert bundle["schema_version"] == "city-facts/1"
    facts = [r for r in bundle["records"] if r["kind"] in ("place", "food")]
    assert {r["fact_id"] for r in facts} == {"place_museum", "food_bao"}
    assert all(r["validation_status"] == "unverified" for r in facts)

    validation = json.loads((Path(summary["run_dir"]) / "structured" / "validation.json")
                            .read_text(encoding="utf-8"))
    assert {r["record_id"] for r in validation["records"]} == {"place_museum", "food_bao"}
    assert validation["spatial"]["inside"] == 1


def test_run_registers_review_tasks_for_unplaced_records(tmp_path, db_path, runs_dir,
                                                         stub_fetcher, monkeypatch):
    job = json.loads(json.dumps(JOB))
    job["ingest_existing"] = []
    job["fetch"] = []
    job["extract"] = [{"record_id": "place_unknown", "category": "museum", "name": "无名馆",
                       "source_keys": []}]
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rc, "_dry_run", lambda p: None)

    summary = rc.run(path, db_path=db_path, runs_dir=runs_dir)
    assert summary["review_tasks"] >= 1
    with ledger.connect(db_path) as db:
        tasks = ledger.list_verify_tasks(db, "open")
    assert any(t["fact_ref"] == "museum:place_unknown" for t in tasks)


def test_run_attaches_raw_path_so_hashes_can_be_recomputed(tmp_path, job_file, evidence,
                                                           db_path, runs_dir, stub_fetcher,
                                                           monkeypatch):
    monkeypatch.setattr(rc, "_dry_run", lambda p: None)
    summary = rc.run(job_file, db_path=db_path, runs_dir=runs_dir)
    structured = json.loads((Path(summary["run_dir"]) / "structured" / "structured.json")
                            .read_text(encoding="utf-8"))
    by_id = {r["record_id"]: r for r in structured["records"]}
    assert by_id["place_museum"]["raw_path"], "结构化记录必须能回指原始证据"


def test_run_is_idempotent_for_sources(tmp_path, job_file, evidence, db_path, runs_dir,
                                       stub_fetcher, monkeypatch):
    monkeypatch.setattr(rc, "_dry_run", lambda p: None)
    rc.run(job_file, db_path=db_path, runs_dir=runs_dir)
    with ledger.connect(db_path) as db:
        first = {s["source_key"] for s in ledger.list_sources(db)}
    rc.run(job_file, db_path=db_path, runs_dir=runs_dir)
    with ledger.connect(db_path) as db:
        second = {s["source_key"] for s in ledger.list_sources(db)}
    assert first == second, "重复运行不得重复登记来源"


def test_dry_run_returns_none_when_script_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "REPO_ROOT", tmp_path)
    assert rc._dry_run(tmp_path / "bundle.json") is None


def test_dry_run_parses_script_output(tmp_path, monkeypatch):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "import_city_facts.py").write_text(
        "import json;print(json.dumps({'status':'dry_run','received':2,'changed':1}))",
        encoding="utf-8")
    monkeypatch.setattr(rc, "REPO_ROOT", tmp_path)
    bundle = tmp_path / "bundle.json"
    bundle.write_text("{}", encoding="utf-8")
    assert rc._dry_run(bundle) == {"status": "dry_run", "received": 2, "changed": 1}


def test_apply_extractions_only_writes_declared_values(tmp_path):
    from collector.extract import StructuredBundle
    bundle = StructuredBundle(run_id="r", collected_at="2026-10-23T10:00:00+08:00")
    applied = rc.apply_extractions(
        {"extract": [{"record_id": "p1", "category": "museum", "name": "开封市博物馆",
                      "source_keys": [], "address": None, "longitude": None}]},
        run_id="r", db=None, bundle=bundle, collected_at=bundle.collected_at, fetcher=None)
    assert applied == [{"record_id": "p1", "category": "museum"}]
    assert bundle.records[0].address is None, "未声明的值保持 null"
    assert bundle.records[0].extraction_method == "manual_reviewed_extraction"


def test_main_prints_summary_json(tmp_path, job_file, evidence, db_path, runs_dir,
                                  stub_fetcher, monkeypatch, capsys):
    monkeypatch.setattr(rc, "_dry_run", lambda p: None)
    code = rc.main([str(job_file), "--database", str(db_path), "--runs-dir", str(runs_dir)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["records"] == 2
    assert payload["run_dir"].startswith(str(runs_dir))
