"""Report entry point used by the quality-report skill.

Reads a run's structured artefact plus the engineering ledger and writes the
markdown and JSON quality report. Numbers are recomputed, never hand-edited.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

from . import ledger, quality
from .config import DEFAULT_DB_PATH, DEFAULT_RUNS_DIR
from .extract import StructuredBundle
from .validate import normalize


def load_structured(run_dir: Path) -> StructuredBundle:
    path = Path(run_dir) / "structured" / "structured.json"
    if not path.exists():
        raise FileNotFoundError(f"缺少结构化产物：{path}")
    return StructuredBundle.from_dict(json.loads(path.read_text(encoding="utf-8")))


def load_dry_run(run_dir: Path) -> dict | None:
    path = Path(run_dir) / "reports" / "city-facts-dry-run.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return {"status": data.get("status"), "received": data.get("received"),
            "changed": data.get("changed"),
            "ready": sum(1 for r in data.get("records", []) if r.get("ready")),
            "issues": len(data.get("records", []))}


def generate(*, run_id: str, db_path: Path = DEFAULT_DB_PATH, runs_dir: Path = DEFAULT_RUNS_DIR,
             collected_day: date | None = None) -> tuple[dict, Path, Path]:
    run_dir = Path(runs_dir) / run_id
    bundle = load_structured(run_dir)
    day = collected_day or date.fromisoformat(bundle.collected_at[:10])
    validation = normalize(bundle.records, collected_day=day)
    report_path = run_dir / "reports" / f"quality-report-{day.isoformat()}.md"
    ledger.init_db(db_path)
    with ledger.connect(db_path) as db:
        if ledger.get_run(db, run_id) is None:
            # 台账里没有这条运行（例如换库重跑报告）时补一条记录，
            # 否则 finish_run 会静默更新 0 行，台账与产物脱节。
            ledger.ensure_run(db, run_id=run_id, params={"report_only": True}, output_dir=run_dir)
        report = quality.compute(db=db, bundle=bundle, validation=validation, run_id=run_id,
                                 collected_day=day, bundle_dry_run=load_dry_run(run_dir))
        # 只补报告路径：采集统计（items_*）由采集运行写入，重跑报告不得清零。
        ledger.set_report_path(db, run_id, str(report_path))
    md, js = quality.write_reports(report, run_dir / "reports", day=day)
    return report, md, js


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    try:
        report, md, js = generate(run_id=args.run_id, db_path=args.database,
                                  runs_dir=args.runs_dir, collected_day=args.day)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", "markdown": str(md), "json": str(js),
                      "totals": report["totals"], "gaps_top": report["gaps_top"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
