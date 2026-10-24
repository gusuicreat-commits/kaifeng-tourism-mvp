#!/usr/bin/env python3
"""Convert the team handoff workbook (v1.1) into a city-facts/1 candidate bundle.

The workbook is a team handoff artifact: every record it contains is treated as
a *candidate* (city-facts/1 ``validation_status='unverified'``,
``rights_status='internal_only'``), regardless of the workbook's own status
column. Workbook evidence files are not present in this repository, so nothing
here may become 'verified'.

Sheets converted: 18_source_records, 10_places, 11_place_entrances,
13_programs, 15_food_items, 16_food_vendors, 17_vendor_foods, 24_highlights.
Sheets intentionally skipped:
- 12_open_rules: the MVP inventory materializes these for the selected day.
- 14_show_sessions: empty in v1.1.
- 19_change_log / 22/23 media: existing frontend media pipeline owns display.

Usage:
    .venv/bin/python scripts/import_team_workbook.py \
        data/team-handoff/汴梁智游_最新数据模板_v1.1_成员1媒体试点_20261022_v17.xlsx \
        --out data/team-handoff/city-facts-teamhandoff-20261023.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TZ = ZoneInfo("Asia/Shanghai")
HANDOFF_COLLECTED_AT = "2026-10-23T22:49:00+08:00"  # file copied into repo at this time
WORKBOOK_SHA256 = "ff1138a0efdba85821ffbbaa87cb5454650832391ae33b7cbaa8d5a819f0f8c4"
WORKBOOK_LOCATOR = (
    "data/team-handoff/汴梁智游_最新数据模板_v1.1_成员1媒体试点_20261022_v17.xlsx"
    f"#{WORKBOOK_SHA256[:16]}"
)
WORKBOOK_PATH = ROOT / 'data/team-handoff/汴梁智游_最新数据模板_v1.1_成员1媒体试点_20261022_v17.xlsx'

SOURCE_TYPE_MAP = {"official": "official", "field": "field", "field_check": "field"}
PLACE_TYPE_MAP = {
    "scenic_spot": "scenic",
    "night_market": "night_visit",
    "food_street": "night_visit",
    "museum": "poi",
    "park": "poi",
    "street": "poi",
    "historic_site": "poi",
}
CRS_MAP = {"GCJ-02": "GCJ02", "GCJ02": "GCJ02", "WGS84": "WGS84", "WGS-84": "WGS84"}


def load_sheet_rows(wb, name: str, header_row: int = 3) -> list[dict]:
    ws = wb[name]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() if h else "" for h in rows[header_row - 1]]
    out = []
    for row in rows[header_row:]:
        if not any(v not in (None, "") for v in row):
            continue
        out.append({header[i]: row[i] for i in range(len(header)) if header[i]})
    return out


def clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clip(value, limit: int):
    text = clean(value)
    if text is None:
        return None
    return text[:limit]


def handoff_source(table: str) -> dict:
    """One stable provenance record per workbook sheet."""
    return {
        "kind": "source",
        "fact_id": f"src_teamworkbook_{table}",
        "version": 1,
        "source_ids": [],
        "validation_status": "unverified",
        "business_status": "active",
        "is_demo": False,
        "rights_status": "internal_only",
        "name": f"团队数据工作簿v1.1（成员交接）·{table}",
        "source_type": "other",
        "url": None,
        "locator": f"{WORKBOOK_LOCATOR}::{table}",
        "publisher": "汴梁游记数据小组",
        "collected_at": HANDOFF_COLLECTED_AT,
    }


def declared_source(row: dict) -> dict | None:
    """Convert one 18_source_records row."""
    sid = clean(row.get("source_id"))
    if not sid:
        return None
    url = clean(row.get("source_url"))
    if url and not url.startswith(("http://", "https://")):
        url = None
    return {
        "kind": "source",
        "fact_id": f"src_wb_{sid}",
        "version": 1,
        "source_ids": [],
        "validation_status": "unverified",
        "business_status": "active",
        "is_demo": False,
        "rights_status": "internal_only",
        "name": clip(row.get("title"), 200) or sid,
        "source_type": SOURCE_TYPE_MAP.get(clean(row.get("source_type")) or "", "other"),
        "url": url,
        "locator": clip(row.get("evidence_path"), 500),
        "publisher": clip(row.get("publisher"), 200),
        "collected_at": HANDOFF_COLLECTED_AT,
    }


def source_refs(row: dict, table: str) -> list[str]:
    refs = [f"src_teamworkbook_{table}"]
    sid = clean(row.get("source_id")) or clean(row.get("intro_source_id")) or clean(row.get("evidence_source_id"))
    if sid:
        refs.append(f"src_wb_{sid}")
    return refs


def split_text(value) -> list[str]:
    import re
    return [x for x in re.split(r'[；;]', clean(value) or '') if x][:20]


def enrich_records(records: list[dict], wb) -> None:
    """Restore MVP fields dropped by the original minimal converter."""
    places = load_sheet_rows(wb, '10_places')
    places += [dict(r, place_id='pl_vendor_'+(clean(r.get('vendor_id')) or '')[4:]) for r in load_sheet_rows(wb, '16_food_vendors')]
    for row in places:
        pid=clean(row.get('place_id')) or ''
        item=next((r for r in records if r['kind']=='place' and r['fact_id']=='pl_wb_'+pid[3:]),None)
        if not item:continue
        item.update(aliases=split_text(row.get('aliases')),external_poi_id=clean(row.get('external_poi_id')),
                    description=clip(row.get('description') if row.get('description') else (None if row.get('vendor_id') else row.get('notes')),2000),
                    suggested_stay_min=int(row['suggested_stay_min']) if row.get('suggested_stay_min') not in (None,'') else None,
                    business_hours=clip(row.get('business_hours'),300),price_band=clip(row.get('price_band'),100),notes=clip(row.get('notes'),2000))
        vendor=next((r for r in records if r['kind']=='vendor' and r['place_id']==item['fact_id']),None)
        if vendor:vendor.update(external_poi_id=item['external_poi_id'],business_hours=item['business_hours'],price_band=item['price_band'])
    for row in load_sheet_rows(wb,'13_programs'):
        item=next((r for r in records if r['kind']=='program' and r['fact_id']=='prg_wb_'+(clean(row.get('program_id')) or '')[4:]),None)
        if item:item.update(place_id='pl_wb_'+(clean(row.get('park_place_id')) or '')[3:],description=clip(row.get('description'),2000),notes=clip(row.get('notes'),2000))
    for row in load_sheet_rows(wb,'15_food_items'):
        item=next((r for r in records if r['kind']=='food' and r['fact_id']=='food_wb_'+(clean(row.get('food_id')) or '')[5:]),None)
        if item:item.update(description=clip(row.get('intro'),2000),aliases=split_text(row.get('aliases')))
    for row in load_sheet_rows(wb,'24_highlights'):
        hid=clean(row.get('highlight_id'));pid=clean(row.get('place_id'))
        if hid and pid:records.append({'kind':'highlight',**base_fact('hl_wb_'+hid[3:],source_refs(row,'highlights')),
            'place_id':'pl_wb_'+pid[3:],'name':clip(row.get('name'),120),'category':clip(row.get('category'),100),
            'description':clip(row.get('description'),2000),'sort_order':int(row.get('sort_order') or 0)})


def base_fact(row_id: str, refs: list[str]) -> dict:
    return {
        "fact_id": row_id,
        "version": 1,
        "source_ids": refs,
        "validation_status": "unverified",
        "business_status": "active",
        "is_demo": False,
        "rights_status": "internal_only",
    }


def convert_places(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        pid = clean(row.get("place_id"))
        name = clean(row.get("name"))
        if not pid or not name:
            continue
        suffix = pid[3:] if pid.startswith("pl_") else pid
        record = {
            "kind": "place",
            **base_fact(f"pl_wb_{suffix}", source_refs(row, "places")),
            "name": clip(name, 120),
            "place_type": PLACE_TYPE_MAP.get(clean(row.get("place_type")) or "", "poi"),
            "parent_place_id": None,
            "address": clip(row.get("address"), 300),
            "longitude": None,
            "latitude": None,
            "coordinate_system": "unknown",
        }
        lng, lat = row.get("lng"), row.get("lat")
        crs = CRS_MAP.get(clean(row.get("crs")) or "")
        if lng not in (None, "") and lat not in (None, "") and crs:
            record["longitude"] = float(lng)
            record["latitude"] = float(lat)
            record["coordinate_system"] = crs
        out.append(record)
    return out


def convert_food_items(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        fid = clean(row.get("food_id"))
        name = clean(row.get("name"))
        if not fid or not name:
            continue
        suffix = fid[5:] if fid.startswith("food_") else fid
        out.append({
            "kind": "food",
            **base_fact(f"food_wb_{suffix}", source_refs(row, "food_items")),
            "name": clip(name, 120),
        })
    return out


def convert_vendors(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Each vendor becomes a place(restaurant) plus a vendor edge."""
    places, vendors = [], []
    for row in rows:
        vid = clean(row.get("vendor_id"))
        name = clean(row.get("name"))
        if not vid or not name:
            continue
        suffix = vid[4:] if vid.startswith("ven_") else vid
        refs = source_refs(row, "food_vendors")
        record = {
            "kind": "place",
            **base_fact(f"pl_wb_vendor_{suffix}", refs),
            "name": clip(name, 120),
            "place_type": "restaurant",
            "parent_place_id": None,
            "address": clip(row.get("address"), 300),
            "longitude": None,
            "latitude": None,
            "coordinate_system": "unknown",
        }
        lng, lat = row.get("lng"), row.get("lat")
        crs = CRS_MAP.get(clean(row.get("crs")) or "")
        if lng not in (None, "") and lat not in (None, "") and crs:
            record["longitude"] = float(lng)
            record["latitude"] = float(lat)
            record["coordinate_system"] = crs
        places.append(record)
        vendors.append({
            "kind": "vendor",
            **base_fact(f"ven_wb_{suffix}", refs),
            "place_id": f"pl_wb_vendor_{suffix}",
        })
    return places, vendors


def convert_supplies(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        rid = clean(row.get("relation_id"))
        vid, fid = clean(row.get("vendor_id")), clean(row.get("food_id"))
        note = clean(row.get("evidence_note"))
        if not (rid and vid and fid and note):
            continue
        out.append({
            "kind": "supply",
            **base_fact(f"vf_wb_{rid[3:] if rid.startswith('vf_') else rid}", source_refs(row, "vendor_foods")),
            "vendor_id": f"ven_wb_{vid[4:] if vid.startswith('ven_') else vid}",
            "food_id": f"food_wb_{fid[5:] if fid.startswith('food_') else fid}",
            "evidence_note": clip(note, 500),
        })
    return out


def convert_entrances(rows: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """Each entrance becomes a place(entrance) plus an entrance relation."""
    places, relations, warnings = [], [], []
    for row in rows:
        eid = clean(row.get("entrance_id"))
        pid = clean(row.get("place_id"))
        name = clean(row.get("name"))
        if not (eid and pid and name):
            continue
        esuffix = eid[4:] if eid.startswith("ent_") else eid
        psuffix = pid[3:] if pid.startswith("pl_") else pid
        entrance_place_id = f"pl_wb_ent_{esuffix}"
        places.append({
            "kind": "place",
            **base_fact(entrance_place_id, source_refs(row, "place_entrances")),
            "name": clip(name, 120),
            "place_type": "entrance",
            "parent_place_id": f"pl_wb_{psuffix}",
            "address": clip(row.get("access_rules"), 300),
            "longitude": None,
            "latitude": None,
            "coordinate_system": "unknown",
        })
        relations.append({
            "kind": "entrance",
            **base_fact(f"ent_wb_{esuffix}", source_refs(row, "place_entrances")),
            "place_id": f"pl_wb_{psuffix}",
            "entrance_place_id": entrance_place_id,
            "direction": "entry",
        })
        if not clean(row.get("lng")):
            warnings.append(f"{eid} {name}: 无坐标，保持候选")
    return places, relations, warnings


def convert_programs(rows: list[dict], stage_place_ids: set[str]) -> tuple[list[dict], list[str]]:
    out, warnings = [], []
    for row in rows:
        gid = clean(row.get("program_id"))
        name = clean(row.get("name"))
        if not (gid and name):
            continue
        stage = clean(row.get("stage_place_id"))
        if stage and stage not in stage_place_ids:
            warnings.append(f"{gid} {name}: stage_place_id={stage} 不在工作簿地点表")
        suffix = gid[4:] if gid.startswith("prg_") else gid
        out.append({
            "kind": "program",
            **base_fact(f"prg_wb_{suffix}", source_refs(row, "programs")),
            "name": clip(name, 120),
        })
    return out, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    try:
        import openpyxl
    except ImportError:
        print("需要 openpyxl：.venv/bin/python -m pip install openpyxl", file=sys.stderr)
        return 2

    digest = hashlib.sha256(args.workbook.read_bytes()).hexdigest()
    if digest != WORKBOOK_SHA256:
        print(f"警告：工作簿 SHA256 {digest[:16]} 与登记值 {WORKBOOK_SHA256[:16]} 不一致，"
              f"按新文件继续，但请在报告中注明。", file=sys.stderr)

    wb = openpyxl.load_workbook(args.workbook, data_only=True, read_only=True)

    sources = [handoff_source(t) for t in
               ("source_records", "places", "place_entrances", "food_items", "food_vendors", "vendor_foods", "programs", "highlights")]
    declared = [s for s in (declared_source(r) for r in load_sheet_rows(wb, "18_source_records")) if s]

    records: list[dict] = sources + declared
    warnings: list[str] = []

    places = convert_places(load_sheet_rows(wb, "10_places"))
    place_ids = {p["fact_id"] for p in places}
    records += places

    vendor_places, vendors = convert_vendors(load_sheet_rows(wb, "16_food_vendors"))
    records += vendor_places + vendors

    foods = convert_food_items(load_sheet_rows(wb, "15_food_items"))
    records += foods

    supplies = convert_supplies(load_sheet_rows(wb, "17_vendor_foods"))
    food_ids = {f["fact_id"] for f in foods}
    vendor_ids = {v["fact_id"] for v in vendors}
    supplies = [s for s in supplies
                if s["vendor_id"] in vendor_ids and s["food_id"] in food_ids]
    records += supplies

    ent_places, ent_relations, ent_warnings = convert_entrances(load_sheet_rows(wb, "11_place_entrances"))
    warnings += ent_warnings
    ent_places = [p for p in ent_places if p["parent_place_id"] in place_ids]
    ent_relations = [e for e in ent_relations if e["place_id"] in place_ids]
    records += ent_places + ent_relations

    programs, prog_warnings = convert_programs(
        load_sheet_rows(wb, "13_programs"), place_ids | {p["fact_id"] for p in ent_places})
    warnings += prog_warnings
    records += programs

    if args.workbook.resolve() == WORKBOOK_PATH.resolve():enrich_records(records,wb)

    # Validate through the real contract before writing anything.
    from app.city.facts import FactBundle
    bundle = FactBundle.model_validate({"schema_version": "city-facts/1", "records": records})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

    summary = {
        "records_total": len(bundle.records),
        "by_kind": {},
        "declared_sources": len(declared),
        "warnings_count": len(warnings),
        "warnings_sample": warnings[:8],
        "workbook_sha256": digest,
        "output": str(args.out),
    }
    for rec in bundle.records:
        summary["by_kind"][rec.kind] = summary["by_kind"].get(rec.kind, 0) + 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
