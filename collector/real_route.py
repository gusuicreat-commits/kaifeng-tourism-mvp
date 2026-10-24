"""Assess real candidate evidence through existing imports in a temporary database."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def _import(script: str, payload: Path, database: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), str(payload.resolve()),
         "--database", str(database), "--apply"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def assess(manifest: dict, facts_path: Path, *, operations_path: Path | None = None,
           requirements_path: Path | None = None) -> dict:
    # Only the maintained CLI imports write facts, and only to this disposable DB.
    if str(ROOT / "backend") not in sys.path:
        sys.path.insert(0, str(ROOT / "backend"))
    from app.city import facts
    from app.city.engine.context import PlanningContext
    from app.city.engine.models import Requirements, OperationalBundle
    from app.city.engine.storage import load_operations
    from app.city.engine.tools import plan_trip

    moment = datetime.now(ZoneInfo("Asia/Shanghai"))
    requirements = (Requirements.model_validate_json(requirements_path.read_text())
                    if requirements_path else None)
    with tempfile.TemporaryDirectory(prefix="kaifeng-real-route-") as folder:
        database = Path(folder) / "acceptance.sqlite3"
        imported = _import("import_city_facts.py", facts_path, database)
        operation_import = (_import("import_city_operations.py", operations_path, database)
                            if operations_path else None)
        with sqlite3.connect(database) as db:
            db.row_factory = sqlite3.Row
            catalog = facts.records(db)
            operations = load_operations(db) if operations_path else OperationalBundle()

        refs = set()
        blockers = []
        for step in manifest["sequence"]:
            for key in ("fact_ref", "required_rule_ref", "vendor_ref"):
                if step.get(key):
                    refs.add(step[key])
            refs.update(step.get("required_supply_refs", []))
            if not step.get("fact_ref"):
                blockers.append({"code": "MISSING_ENDPOINT", "step": step["step"]})
        assessments = []
        for ref in sorted(refs):
            kind, identity = ref.split(":", 1)
            item = catalog.get((kind, identity))
            state = (facts.status(item, catalog, requirements.starts_at if requirements else moment) if item else
                     {"ready": False, "issues": ["FACT_MISSING"]})
            assessments.append({"ref": ref, **state})
            fact_date = getattr(item, "business_date", None)
            if requirements and fact_date:
                if fact_date != requirements.business_date:
                    blockers.append({"code": "FACT_DATE_MISMATCH", "ref": ref,
                                     "fact_date": fact_date.isoformat(),
                                     "requested_date": requirements.business_date.isoformat()})
        if any(not entry["ready"] for entry in assessments):
            blockers.append({"code": "FACTS_NOT_READY"})
        if not requirements:
            blockers.append({"code": "REQUIREMENTS_REQUIRED",
                             "fields": ["business_date", "starts_at", "ends_at",
                                        "origin_place_id", "destination_place_id"]})
        if not operations.journeys:
            blockers.append({"code": "JOURNEYS_REQUIRED"})

        planning = None
        if requirements:
            if requirements.starts_at < moment:
                blockers.append({"code": "START_TIME_IN_PAST"})
            if manifest.get("visitor_date") != requirements.business_date.isoformat():
                blockers.append({"code": "VISITOR_DATE_NOT_CONFIRMED"})
            # A successful run must retain every selected place and food goal.
            required_places = {s["fact_ref"].split(":", 1)[1]
                               for s in manifest["sequence"]
                               if (s.get("fact_ref") or "").startswith("place:")
                               and s["kind"] != "return_endpoint"}
            requested_places = {g.target_id for g in requirements.goals
                                if g.required and g.target_kind == "place"}
            if not required_places <= requested_places:
                blockers.append({"code": "REQUIRED_GOALS_MISSING",
                                 "ids": sorted(required_places - requested_places)})
            for step in manifest["sequence"]:
                if step["kind"] == "return_endpoint" and step.get("fact_ref"):
                    if requirements.destination_place_id != step["fact_ref"].split(":", 1)[1]:
                        blockers.append({"code": "RETURN_ENDPOINT_MISMATCH"})
            context = PlanningContext(catalog, operations, moment, synthetic=False)
            planning = plan_trip(requirements, context).model_dump(mode="json")
            if planning["status"] != "feasible":
                blockers.append({"code": "ENGINE_NOT_FEASIBLE",
                                 "status": planning["status"]})
            expected = [s["fact_ref"].split(":", 1)[1] for s in manifest["sequence"]
                        if s.get("fact_ref") and s["kind"] != "return_endpoint"]
            if planning["itineraries"] and not any(
                [a["place_id"] for a in plan["activities"]
                 if a["place_id"] in required_places] == expected
                for plan in planning["itineraries"]
            ):
                blockers.append({"code": "SEQUENCE_NOT_SATISFIED"})

    return {
        "schema_version": "kaifeng-real-route-readiness/1",
        "route_id": manifest["route_id"], "assessed_at": moment.isoformat(),
        "status": "blocked" if blockers else "software_ready_pending_field_acceptance",
        "facts_import": imported, "operations_import": operation_import,
        "requirements_basis": manifest.get("requirements_basis", "unspecified"),
        "demand_assumptions": manifest.get("demand_assumptions", {}),
        "required_facts": assessments, "blockers": blockers, "planning": planning,
        "runtime_database_written": False, "field_acceptance": "not_performed",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--operations", type=Path)
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = assess(json.loads(args.manifest.read_text()), args.facts,
                        operations_path=args.operations, requirements_path=args.requirements)
    except (ValueError, OSError, sqlite3.Error) as exc:
        report = {"status": "rejected", "error": str(exc), "runtime_database_written": False}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "report": str(args.out)}, ensure_ascii=False))
    return 0 if report["status"] == "software_ready_pending_field_acceptance" else 2


if __name__ == "__main__":
    raise SystemExit(main())
