"""Batch collection driver for the first real data round.

Reads a JSON job file describing which already-fetched evidence to ingest plus
which URLs to fetch now, runs fetch -> extract -> validate -> bundle -> dry-run
and records every step in the engineering ledger. It never writes the runtime
fact store.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from . import ledger
from .config import DEFAULT_DB_PATH, DEFAULT_RUNS_DIR, REPO_ROOT
from .extract import StructuredBundle, StructuredRecord, extract_html_text
from .fetch import Fetcher
from .validate import build_bundle, normalize, register_review_tasks, write_json


def _resolve_evidence_path(raw: str, *, base_dir: Path) -> Path:
    """Resolve a job-relative evidence path.

    Relative paths are resolved against the job file's directory first, then
    against the repository root (the real job files use repo-relative paths).
    """
    path = Path(raw)
    if path.is_absolute():
        return path
    candidate = (base_dir / path).resolve()
    if candidate.exists():
        return candidate
    return (REPO_ROOT / path).resolve()


def _source_key_for_existing(path: Path, source_id: str) -> str:
    return source_id or f"src_existing_{path.stem}"

def ingest_existing_evidence(job: dict, *, run_id: str, db, fetcher: Fetcher,
                             bundle: StructuredBundle, collected_at: str,
                             base_dir: Path = REPO_ROOT) -> list[dict]:
    """Register already-captured evidence files in the ledger without refetching."""
    ingested: list[dict] = []
    for item in job.get("ingest_existing", []):
        path = _resolve_evidence_path(item["path"], base_dir=base_dir)
        if not path.exists():
            ledger.record_failure(db, run_id=run_id, source_key=item.get("source_key"),
                                  failure_kind="missing_evidence_file",
                                  detail=f"{path} 不存在，跳过登记")
            continue
        raw_path = fetcher.raw_dir / path.name
        if path.resolve() != raw_path.resolve():
            shutil.copy2(path, raw_path)
        from .fetch import sha256_file
        digest = sha256_file(raw_path)
        key = item.get("source_key") or _source_key_for_existing(path, "")
        ledger.upsert_source(db, source_key=key, name=item["name"], source_type=item["source_type"],
                             url=item.get("url"), publisher=item.get("publisher"),
                             access_kind=item.get("access_kind", "platform"),
                             access_status=item.get("access_status", "reachable"),
                             content_sha256=digest, raw_path=str(raw_path),
                             license_status=item.get("license_status", "unknown"),
                             notes=item.get("notes"), last_checked_at=collected_at)
        ingested.append({"source_key": key, "raw_path": str(raw_path), "sha256": digest})
    return ingested


def fetch_targets(job: dict, *, run_id: str, db, fetcher: Fetcher) -> list[dict]:
    outcomes = []
    for target in job.get("fetch", []):
        source_key = target.get("source_key")
        outcome = fetcher.fetch(target["url"], source_key=source_key)
        record = {"url": target["url"], "ok": outcome.ok, "status_code": outcome.status_code,
                  "error_kind": outcome.error_kind, "sha256": outcome.sha256,
                  "raw_path": str(outcome.raw_path) if outcome.raw_path else None,
                  "source_key": source_key, "label": target.get("label")}
        if outcome.ok and target.get("register_as"):
            ledger.upsert_source(db, source_key=source_key or target["register_as"],
                                 name=target["register_as"], source_type=target.get("source_type", "other"),
                                 url=target["url"], publisher=target.get("publisher"),
                                 access_kind=target.get("access_kind", "official_site"),
                                 access_status="reachable", content_sha256=outcome.sha256,
                                 raw_path=str(outcome.raw_path),
                                 license_status=target.get("license_status", "unknown"),
                                 notes=target.get("notes"), last_checked_at=ledger.now_iso())
        outcomes.append(record)
    return outcomes


def apply_extractions(job: dict, *, run_id: str, db, bundle: StructuredBundle,
                      collected_at: str, fetcher: Fetcher,
                      base_dir: Path = REPO_ROOT) -> list[dict]:
    """Turn declared, hand-verified field values into structured records.

    Only values explicitly listed in the job file are written. Each value must
    carry an evidence quote taken from the fetched text; missing values stay
    null. This is the audit seam between raw evidence and structured data.
    """
    applied: list[dict] = []
    raw_by_source = {}
    for item in job.get("ingest_existing", []) + job.get("fetch", []):
        path = (_resolve_evidence_path(item["path"], base_dir=base_dir)
                if item.get("path") else None)
        if item.get("source_key"):
            raw_by_source[item["source_key"]] = path
    for spec in job.get("extract", []):
        source_keys = [k for k in spec.get("source_keys", [])]
        # 让质量报告能按 SHA-256 复算：结构化记录带上其首个来源的原始证据路径
        raw_path = spec.get("raw_path")
        if raw_path is None:
            for key in source_keys:
                candidate = raw_by_source.get(key)
                if candidate is not None and Path(candidate).exists():
                    raw_path = str(Path(candidate).resolve())
                    break
        record = StructuredRecord(
            record_id=spec["record_id"], category=spec["category"], source_keys=source_keys,
            collected_at=collected_at,
            extraction_method=spec.get("extraction_method", "manual_reviewed_extraction"),
            name=spec.get("name"), district=spec.get("district"), address=spec.get("address"),
            longitude=spec.get("longitude"), latitude=spec.get("latitude"),
            coordinate_system=spec.get("coordinate_system", "unknown"),
            place_type=spec.get("place_type"), parent_record_id=spec.get("parent_record_id"),
            opens_at=spec.get("opens_at"), closes_at=spec.get("closes_at"),
            last_entry_at=spec.get("last_entry_at"), business_date=spec.get("business_date"),
            ticket_text=spec.get("ticket_text"), program_name=spec.get("program_name"),
            starts_at=spec.get("starts_at"), ends_at=spec.get("ends_at"),
            vendor_record_id=spec.get("vendor_record_id"), food_record_id=spec.get("food_record_id"),
            evidence_note=spec.get("evidence_note"),
            evidence_quotes=spec.get("evidence_quotes", {}),
            conflicts=list(spec.get("conflicts", [])),
            confidence_note=spec.get("confidence_note"), raw_path=raw_path,
        )
        bundle.add(record)
        applied.append({"record_id": record.record_id, "category": record.category})
    return applied


def run(job_path: Path, *, db_path: Path = DEFAULT_DB_PATH, runs_dir: Path = DEFAULT_RUNS_DIR,
        apply_import: bool = False) -> dict:
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    ledger.init_db(db_path)
    with ledger.connect(db_path) as db:
        task_id = ledger.create_task(db, category=job.get("category", "mixed"),
                                     objective=job.get("objective", "批量采集"),
                                     scope_district=job.get("scope_district"), priority=job.get("priority", "normal"))
        run_id = ledger.start_run(db, task_id=task_id, params={"job": Path(job_path).name},
                                  output_dir=Path(runs_dir))
        run_dir = Path(runs_dir) / run_id
        (run_dir / "raw").mkdir(parents=True, exist_ok=True)
        (run_dir / "structured").mkdir(parents=True, exist_ok=True)
        (run_dir / "reports").mkdir(parents=True, exist_ok=True)
        collected_at = ledger.now_iso()
        bundle = StructuredBundle(run_id=run_id, collected_at=collected_at)

        fetcher = Fetcher(run_id=run_id, db=db, runs_dir=runs_dir, sleep=0.0)
        ingested = ingest_existing_evidence(job, run_id=run_id, db=db, fetcher=fetcher,
                                            bundle=bundle, collected_at=collected_at,
                                            base_dir=Path(job_path).resolve().parent)
        fetched = fetch_targets(job, run_id=run_id, db=db, fetcher=fetcher)
        extracted = apply_extractions(job, run_id=run_id, db=db, bundle=bundle,
                                      collected_at=collected_at, fetcher=fetcher,
                                      base_dir=Path(job_path).resolve().parent)
        bundle.write(run_dir / "structured" / "structured.json")

        validation = normalize(bundle.records, collected_day=date.fromisoformat(collected_at[:10]))
        write_json(run_dir / "structured" / "validation.json", validation.to_dict())
        register_review_tasks(db, validation)

        bundle_payload = build_bundle(validation, run_id=run_id, collected_at=collected_at,
                                      include_outside=bool(job.get("include_outside", False)))
        bundle_path = write_json(run_dir / "bundle" / "city-facts-candidate.json", bundle_payload)

        failures = ledger.list_failures(db, run_id)
        dry_run = _dry_run(bundle_path)
        if dry_run is not None:
            (run_dir / "reports" / "city-facts-dry-run.json").write_text(
                json.dumps(dry_run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = {
            "run_id": run_id, "task_id": task_id, "run_dir": str(run_dir),
            "ingested": ingested, "fetched": fetched, "extracted": extracted,
            "records": len(validation.records), "duplicates": len(validation.duplicates),
            "conflicts": len(validation.conflicts), "review_tasks": len(validation.review_tasks),
            "failures": failures, "bundle_path": str(bundle_path),
            "dry_run": {k: dry_run.get(k) for k in ("status", "received", "changed")} if dry_run else None,
        }
        (run_dir / "reports" / "run-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        status = "success" if not failures else "partial"
        ledger.finish_run(db, run_id, status=status, items_fetched=len(fetched) + len(ingested),
                          items_new=len(validation.records), items_failed=len(failures),
                          error_summary="; ".join(f["failure_kind"] for f in failures[:5]) or None)
        ledger.update_task_status(db, task_id, "done" if not failures else "failed")
        summary["status"] = status
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
    parser.add_argument("job", type=Path)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    args = parser.parse_args(argv)
    summary = run(args.job, db_path=args.database, runs_dir=args.runs_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
