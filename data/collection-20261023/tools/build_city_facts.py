#!/usr/bin/env python3
"""Emit a deliberately non-executable city-facts/1 candidate bundle."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "structured"
OUT = ROOT / "city-facts-candidate.json"

manifest = json.loads((S / "source-manifest.json").read_text())
places = json.loads((S / "places.json").read_text())["places"]
entrances = json.loads((S / "entrance-candidates.json").read_text())["entrances"]
programs = json.loads((S / "program-catalog.json").read_text())["programs"]
foods = json.loads((S / "foods.json").read_text())["foods"]

records = []
for s in manifest["sources"]:
    source_type = s["source_type"]
    url = s["url"] if s["url"].startswith(("http://", "https://")) else None
    records.append({
        "fact_id": s["source_id"], "version": 1, "validation_status": "unverified",
        "business_status": "active", "is_demo": False, "rights_status": "internal_only",
        "kind": "source", "name": s["name"], "source_type": source_type,
        "url": url, "locator": s["locator"], "publisher": s["publisher"],
        # The manifest keeps date-only publication precision; city-facts/1 requires timezone-aware instants.
        "collected_at": s["captured_at"], "published_at": None,
    })

for p in places:
    records.append({
        "fact_id": p["place_id"], "version": 1, "source_ids": p["source_ids"],
        "validation_status": "unverified", "business_status": "active", "is_demo": False,
        "rights_status": "internal_only", "kind": "place", "name": p["name"],
        "place_type": p["place_type"], "parent_place_id": p["parent_place_id"],
        "address": p["address"], "longitude": None, "latitude": None,
        "coordinate_system": "unknown",
    })

for e in entrances:
    records.append({
        "fact_id": e["entrance_relation_id"], "version": 1, "source_ids": e["source_ids"],
        "validation_status": "unverified", "business_status": "active", "is_demo": False,
        "rights_status": "internal_only", "kind": "entrance",
        "place_id": e["place_id"], "entrance_place_id": e["entrance_place_id"],
        "direction": e["direction"],
    })

# Program is an identity only; the catalog remains separate because no target date exists.
seq = 0
program_ids = {}
for p in programs:
    key = (p["program_name"], p["stage_label"])
    if key in program_ids:
        continue
    seq += 1
    fid = f"program_qm_{seq:03d}"
    program_ids[key] = fid
    records.append({
        "fact_id": fid, "version": 1, "source_ids": ["src_qmsyun_program_20261023"],
        "validation_status": "unverified", "business_status": "active", "is_demo": False,
        "rights_status": "internal_only", "kind": "program", "name": p["program_name"],
    })

for f in foods:
    records.append({
        "fact_id": f["food_id"], "version": 1, "source_ids": f["source_ids"],
        "validation_status": "unverified", "business_status": "active", "is_demo": False,
        "rights_status": "internal_only", "kind": "food", "name": f["name"],
    })

bundle = {"schema_version": "city-facts/1", "records": records}
OUT.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"records": len(records), "path": str(OUT)}, ensure_ascii=False))
