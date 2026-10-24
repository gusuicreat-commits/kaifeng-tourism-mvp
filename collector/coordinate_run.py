"""Finish a coordinate-backfill run: bundle, dry-run, quality report, ledger.

`geocode_backfill.py` only attaches coordinates. This driver closes the loop the
same way `run_collection.py` does for a collection run: validate the merged
records, build the city-facts/1 candidate bundle, run the import script in
dry-run mode, write the quality report and register review tasks in the ledger.

The bundle produced here is a coordinate-only delta against earlier runs: the
same `record_id`s with coordinates added where a local source defends them. It
never writes the runtime fact store.
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


def finish(run_dir: Path, *, db_path: Path = DEFAULT_DB_PATH) -> dict:
    run_dir = Path(run_dir)
    structured_path = run_dir / "structured" / "structured.json"
    if not structured_path.exists():
        raise FileNotFoundError(f"缺少结构化产物：{structured_path}")
    bundle = StructuredBundle.from_dict(json.loads(structured_path.read_text(encoding="utf-8")))
    collected_day = date.fromisoformat(bundle.collected_at[:10])
    run_id = run_dir.name
    (run_dir / "bundle").mkdir(parents=True, exist_ok=True)
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)

    ledger.init_db(db_path)
    with ledger.connect(db_path) as db:
        ledger.ensure_run(db, run_id=run_id, params={"kind": "coordinate_backfill"},
                          output_dir=run_dir)
        validation = normalize(bundle.records, collected_day=collected_day)
        write_json(run_dir / "structured" / "validation.json", validation.to_dict())
        created = register_review_tasks(db, validation)

        bundle_payload = build_bundle(validation, run_id=run_id,
                                      collected_at=bundle.collected_at)
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
            "run_id": run_id, "run_dir": str(run_dir),
            "records": len(validation.records),
            "duplicates": len(validation.duplicates),
            "conflicts": len(validation.conflicts),
            "review_tasks_created": created,
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
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    args = parser.parse_args(argv)
    summary = finish(args.run_dir, db_path=args.database)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
