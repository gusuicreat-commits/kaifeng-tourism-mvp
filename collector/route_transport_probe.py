"""Probe a sample's reference transport without persisting provider POIs or ETAs."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def probe(targets: dict, provider, *, sleeper=time.sleep) -> dict:
    moment = datetime.now(ZoneInfo("Asia/Shanghai"))
    report = {
        "schema_version": "route-transport-probe/1",
        "route_id": targets["route_id"], "queried_at": moment.isoformat(),
        "status": "reference_incomplete", "locations": [], "legs": [],
        "provider_payload_persisted": False, "runtime_database_written": False,
        "formal_operations_generated": False, "navigation_verified": False,
        "target_date_traffic_verified": False,
        "scope": "current provider reference only; no verified entrances or horizon envelope",
    }
    selected = {}

    def call(method, *args, **kwargs):
        if provider.request_count and not provider.halted:
            sleeper(1.05)
        return method(*args, **kwargs)

    for target in targets["locations"]:
        if provider.halted:
            break
        result = call(provider.search_pois, target["query"], version="v3")
        matches = [p for p in result.pois if p.name in target["allowed_names"]
                   and (not target.get("address_contains")
                        or target["address_contains"] in (p.address or ""))]
        report["locations"].append({
            "ref": target["ref"], "provider_status": result.status,
            "matching_candidates": len(matches), "identity_unique": len(matches) == 1,
            "selection_basis": "configured_name_and_address_match",
            "entrance_verified": False,
        })
        if len(matches) == 1:
            selected[target["ref"]] = matches[0]

    for leg in targets["legs"]:
        entry = {"leg_id": leg["leg_id"], "from": leg["from"], "to": leg["to"],
                 "mode": "walking", "formal_admission": "needs_data"}
        if leg["from"] == leg["to"]:
            entry["status"] = "same_place_entry_connection_unverified"
        elif provider.halted:
            entry["status"] = "provider_halted"
        elif leg["from"] not in selected or leg["to"] not in selected:
            entry["status"] = "unresolved_location"
        else:
            quote = call(provider.estimate, selected[leg["from"]], selected[leg["to"]],
                         "walking", moment, moment)
            entry.update(status=quote.status,
                         duration_available=quote.duration_seconds is not None,
                         distance_available=quote.distance_m is not None,
                         geometry_available=quote.geometry_available)
        report["legs"].append(entry)
    report["requests"] = provider.request_count
    report["events"] = [dict(event) for event in provider.events]
    report["halted"] = provider.halted
    if all(leg["status"] == "estimated" for leg in report["legs"]):
        report["status"] = "reference_available_formal_blocked"
    elif any(leg["status"] == "estimated" for leg in report["legs"]):
        report["status"] = "reference_partial_formal_blocked"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT / "backend"))
    from dotenv import load_dotenv
    from app.city.engine.amap import AmapProvider
    load_dotenv(ROOT / ".env")
    def enabled(key):
        return os.getenv(key, "").lower() in {"1", "true", "yes"}
    provider = AmapProvider.from_environment(
        enabled=args.live and enabled("AMAP_LOCATION_SEARCH_ENABLED")
        and enabled("AMAP_REFERENCE_ROUTES_ENABLED"),
        terms_reviewed=enabled("AMAP_TERMS_REVIEWED"),
        max_requests=8, retries=0, qps=1, timeout_seconds=5,
        api_version="v5",
    )
    try:
        report = probe(json.loads(args.targets.read_text()), provider)
    finally:
        provider.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "requests": report["requests"],
                      "report": str(args.out)}, ensure_ascii=False))
    return 0 if any(leg["status"] == "estimated" for leg in report["legs"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
