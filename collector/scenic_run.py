"""Finish a scenic-alignment run: validate, bundle, dry-run, quality report, ledger.

`scenic_align.py` only turns the team workbook's scenic rows into structured
records. This driver closes the loop the same way `run_collection.py` and
`coordinate_run.py` do, so a scenic run produces the same auditable artefact
chain as every other run: `structured/validation.json`,
`bundle/city-facts-candidate.json`, `reports/city-facts-dry-run.json`,
`reports/quality-report-<date>.md` and ledger rows.

The bundle produced here is a scenic-only delta against earlier runs. The team
workbook's `pl_wb_*` place and source records are *carried into the bundle* so
it passes the import script's reference check on its own; they keep their
team-bundle values and are never rewritten here. It never writes the runtime
fact store.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from . import ledger, quality
from .config import DEFAULT_DB_PATH, DEFAULT_RUNS_DIR, REPO_ROOT
from .extract import StructuredBundle
from .validate import build_bundle, normalize, register_review_tasks, write_json

TEAM_BUNDLE = REPO_ROOT / "data" / "team-handoff" / "city-facts-teamhandoff-20261023.json"
WORKBOOK_SHA256 = "ff1138a0efdba85821ffbbaa87cb5454650832391ae33b7cbaa8d5a819f0f8c4"


def register_workbook_sources(db, *, workbook: Path, run_id: str,
                               expected_sha256: str | None = None) -> list[str]:
    """Register the workbook and its declared sources in the ledger.

    The workbook is the one local artefact behind these records, so it can be
    hashed like any other evidence file. Its `18_source_records` rows point at
    evidence files that are NOT in this repository, so they are registered with
    `access_status=missing_evidence` and no hash -- the report then separates
    "declared" from "bytes in hand" instead of collapsing both into 0.
    """
    from .fetch import sha256_file
    digest = sha256_file(workbook)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(f"工作簿 SHA-256 与登记值不符：{digest}")
    keys = []
    ledger.upsert_source(db, source_key="src_teamworkbook_places",
                         name="团队数据工作簿v1.1（成员交接）·places",
                         source_type="other", url=None, publisher="汴梁游记数据小组",
                         access_kind="team_handoff", access_status="reachable",
                         content_sha256=digest, raw_path=str(workbook),
                         license_status="internal_only",
                         notes=f"{SHEET} 表 scenic 行；run={run_id}")
    keys.append("src_teamworkbook_places")
    payload = json.loads(TEAM_BUNDLE.read_text(encoding="utf-8"))
    records = payload["records"] if isinstance(payload, dict) else payload
    for record in records:
        if record.get("kind") != "source" or not record.get("fact_id", "").startswith("src_wb_"):
            continue
        ledger.upsert_source(db, source_key=record["fact_id"], name=record.get("name") or record["fact_id"],
                             source_type=record.get("source_type", "other"),
                             url=record.get("url"), publisher=record.get("publisher"),
                             access_kind="team_handoff",
                             # 证据文件不在本仓库：如实标 missing，不伪造可达。
                             access_status="missing_evidence",
                             content_sha256=None, raw_path=None,
                             license_status="internal_only",
                             notes=f"locator={record.get('locator') or '未登记'}")
        keys.append(record["fact_id"])
    return keys


SHEET = "10_places"


def _find_workbook() -> Path:
    matches = sorted(REPO_ROOT.glob("data/team-handoff/*.xlsx"))
    if not matches:
        raise FileNotFoundError("未找到团队工作簿（data/team-handoff/*.xlsx）")
    return matches[-1]


def _carry_team_records(payload: dict, needed: set[str]) -> list[dict]:
    """Team-bundle records referenced by this run, in their original form.

    open_rules_instantiator.py uses the same pattern: a bundle that references
    records from another namespace must carry them, otherwise the import
    script's link check rejects the whole bundle. Values are not modified here.
    """
    records = payload["records"] if isinstance(payload, dict) else payload
    by_id = {r["fact_id"]: r for r in records}
    return [by_id[key] for key in sorted(needed) if key in by_id]


def _extra_sources(records: list[dict], carried: list[dict], *, run_id: str,
                   collected_at: str) -> list[dict]:
    """Source rows for keys the run references but the ledger does not know.

    The workbook's `src_teamworkbook_*` / `src_wb_src_*` keys were never
    registered in the collector ledger (they come from the team handoff), so
    the bundle needs its own source rows. The locator points at the workbook
    and sheet instead of inventing a URL.
    """
    known = {r["fact_id"] for r in carried if r.get("kind") == "source"}
    out: list[dict] = []
    for record in records:
        for key in record.get("source_keys", []):
            if key in known:
                continue
            known.add(key)
            out.append({"fact_id": key, "version": 1, "source_ids": (),
                        "validation_status": "unverified", "business_status": "active",
                        "is_demo": False, "rights_status": "internal_only", "kind": "source",
                        "name": key, "source_type": "other", "url": None,
                        "locator": f"run:{run_id}", "publisher": None,
                        "collected_at": collected_at})
    return out


def finish(run_dir: Path, *, db_path: Path = DEFAULT_DB_PATH,
           team_bundle: Path = TEAM_BUNDLE, workbook: Path | None = None,
           expected_sha256: str | None = WORKBOOK_SHA256) -> dict:
    run_dir = Path(run_dir)
    structured_path = run_dir / "structured" / "structured.json"
    if not structured_path.exists():
        raise FileNotFoundError(f"缺少结构化产物：{structured_path}")
    bundle = StructuredBundle.from_dict(json.loads(structured_path.read_text(encoding="utf-8")))
    collected_day = date.fromisoformat(bundle.collected_at[:10])
    run_id = run_dir.name
    workbook = Path(workbook) if workbook else _find_workbook()
    (run_dir / "bundle").mkdir(parents=True, exist_ok=True)
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)

    ledger.init_db(db_path)
    with ledger.connect(db_path) as db:
        ledger.ensure_run(db, run_id=run_id, params={"kind": "scenic_alignment"},
                          output_dir=run_dir)
        registered = register_workbook_sources(
            db, workbook=workbook, run_id=run_id,
            # 显式传入 expected_sha256 时才校验；None 表示登记当前文件哈希。
            expected_sha256=expected_sha256)
        validation = normalize(bundle.records, collected_day=collected_day)
        write_json(run_dir / "structured" / "validation.json", validation.to_dict())
        created = register_review_tasks(db, validation)

        bundle_payload = build_bundle(validation, run_id=run_id,
                                      collected_at=bundle.collected_at)
        # build_bundle() 只为本批次生成的 place/source 建行；景区记录引用的
        # 团队工作簿来源不在这批里，需要按引用关系补行，否则 dry-run 会因
        # "引用不存在"整包拒绝。
        needed: set[str] = set()
        for record in bundle_payload["records"]:
            needed.update(record.get("source_ids") or [])
        carried = _carry_team_records(json.loads(Path(team_bundle).read_text(encoding="utf-8")),
                                      needed)
        extra = _extra_sources(bundle_payload["records"], carried, run_id=run_id,
                               collected_at=bundle.collected_at)
        # 去重：build_bundle 已为本批次 source_key 建过行，同 id 不重复追加。
        existing_keys = {r["fact_id"] for r in bundle_payload["records"]}
        bundle_payload["records"].extend(r for r in carried + extra
                                         if r["fact_id"] not in existing_keys)
        bundle_path = write_json(run_dir / "bundle" / "city-facts-candidate.json", bundle_payload)

        failures = ledger.list_failures(db, run_id)
        dry_run = _dry_run(bundle_path)
        if dry_run is not None:
            (run_dir / "reports" / "city-facts-dry-run.json").write_text(
                json.dumps(dry_run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = quality.compute(db=db, bundle=bundle, validation=validation, run_id=run_id,
                                 collected_day=collected_day, bundle_dry_run=dry_run)
        md, js = quality.write_reports(report, run_dir / "reports", day=collected_day)
        ledger.set_report_path(db, run_id, str(md))

        summary = {
            "run_id": run_id, "run_dir": str(run_dir), "kind": "scenic_alignment",
            "records": len(validation.records),
            "scenics": len([r for r in validation.records if r.place_type == "scenic"]),
            "duplicates": len(validation.duplicates),
            "conflicts": len(validation.conflicts),
            "review_tasks_created": created,
            "carried_team_records": len(carried), "extra_sources": len(extra),
            "ledger_sources_registered": len(registered),
            "failures": len(failures),
            "bundle_path": str(bundle_path),
            "dry_run": {k: dry_run.get(k) for k in ("status", "received", "changed")} if dry_run else None,
            "quality_report": str(md),
            "coordinate_systems": validation.spatial["coordinate_systems"],
        }
        (run_dir / "reports" / "run-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ledger.finish_run(db, run_id, status="success", items_new=len(validation.records),
                          report_path=str(md))
    return summary


def _dry_run(bundle_path: Path) -> dict | None:
    script = REPO_ROOT / "scripts" / "import_city_facts.py"
    if not script.exists():
        return None
    result = subprocess.run([sys.executable, str(script), str(bundle_path)],
                            capture_output=True, text=True, cwd=str(REPO_ROOT))
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {"status": "rejected", "error": result.stderr.strip()[:1000] or result.stdout[:1000]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--team-bundle", type=Path, default=TEAM_BUNDLE)
    parser.add_argument("--workbook", type=Path, default=None)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    args = parser.parse_args(argv)
    summary = finish(args.run_dir, db_path=args.database, team_bundle=args.team_bundle,
                     workbook=args.workbook)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
