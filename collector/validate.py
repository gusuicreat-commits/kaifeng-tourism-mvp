"""Quality control (Skill C engine).

Normalises structured records, deduplicates, runs the coarse spatial filter,
detects conflicts, emits review tasks and produces a city-facts/1 candidate
bundle. Nothing here verifies a fact: `verified` requires the human five-tuple
(reviewer, method, verified_at, effective_from, effective_to) which is supplied
by the verification workflow, never inferred from a page.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

from . import ledger
from .config import (District, category, districts, district_unknown_label,
                     excluded_names)
from .extract import StructuredBundle, StructuredRecord

COORD_SYSTEMS = {"WGS84", "GCJ02"}


@dataclass
class NormalizedRecord:
    record_id: str
    category: str
    name: str
    district: str
    source_keys: list[str]
    parent_record_id: str | None = None
    place_type: str | None = None
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    coordinate_system: str = "unknown"
    business_date: str | None = None
    opens_at: str | None = None
    closes_at: str | None = None
    last_entry_at: str | None = None
    evidence_note: str | None = None
    evidence_quotes: dict[str, str] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    spatial_status: str = "pending"
    spatial_reason: str = ""
    review_items: list[str] = field(default_factory=list)
    raw_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id, "category": self.category, "name": self.name,
            "district": self.district, "source_keys": list(self.source_keys),
            "parent_record_id": self.parent_record_id,
            "place_type": self.place_type, "address": self.address,
            "longitude": self.longitude, "latitude": self.latitude,
            "coordinate_system": self.coordinate_system, "business_date": self.business_date,
            "opens_at": self.opens_at, "closes_at": self.closes_at, "last_entry_at": self.last_entry_at,
            "evidence_note": self.evidence_note, "evidence_quotes": dict(self.evidence_quotes),
            "conflicts": list(self.conflicts), "spatial_status": self.spatial_status,
            "spatial_reason": self.spatial_reason, "review_items": list(self.review_items),
            "raw_path": self.raw_path,
        }


@dataclass
class ValidationReport:
    records: list[NormalizedRecord]
    duplicates: list[dict]
    conflicts: list[dict]
    spatial: dict
    review_tasks: list[dict]

    def to_dict(self) -> dict:
        return {
            "records": [r.to_dict() for r in self.records],
            "duplicates": list(self.duplicates),
            "conflicts": list(self.conflicts),
            "spatial": dict(self.spatial),
            "review_tasks": list(self.review_tasks),
        }


def normalize_name(name: str) -> str:
    """Canonical comparison key.

    Only spacing, dashes and bracket style are normalised. The bracketed part is
    kept because it usually carries the distinguishing branch or qualifier
    ("黄家老店（西司店）" and "黄家老店（大梁路店）" are different shops).
    """
    text = (name or "").replace("(", "（").replace(")", "）")
    text = re.sub(r"[\s·・\-—_]+", "", text)
    return text.strip().lower()


def coordinate_fingerprint(longitude: float | None, latitude: float | None,
                           decimals: int = 3) -> str | None:
    if longitude is None or latitude is None:
        return None
    return f"{round(longitude, decimals):.3f},{round(latitude, decimals):.3f}"


def district_for_text(text: str, districts_list: Iterable[District]) -> str | None:
    for d in districts_list:
        if d.name in (text or ""):
            return d.name
    return None


def _district_key(name: str) -> str:
    """Match both full and short district names ("顺河回族区"/"顺河区")."""
    return (name or "").replace("回族", "").replace("城乡一体化", "")


def resolve_district(declared: str, districts_list: Iterable[District]) -> District | None:
    """Resolve a source-declared district name against the configured districts.

    Accepts the short form used in source text ("示范区" for
    "城乡一体化示范区", "顺河区" for "顺河回族区"). Returns None when the name
    cannot be resolved, so the record stays pending instead of being placed by
    guesswork.
    """
    declared = (declared or "").strip()
    if not declared:
        return None
    for d in districts_list:
        if d.name == declared:
            return d
    key = _district_key(declared)
    for d in districts_list:
        if _district_key(d.name) == key:
            return d
    for d in districts_list:
        if key and key in _district_key(d.name):
            return d
    return None


def spatial_verdict(longitude: float | None, latitude: float | None,
                    districts_list: Iterable[District], address: str | None,
                    text_hint: str | None = None,
                    declared_district: str | None = None) -> tuple[str, str, str]:
    """Coarse spatial filter.

    Returns (status, district, reason). `pending` keeps the record and routes it
    to a review task; excluded-county text is the only condition that returns
    `outside` without coordinates. An explicitly declared district is treated as
    a source-provided statement, not as verification.
    """
    districts_list = list(districts_list)
    excluded = [e.replace("（非主城区部分）", "") for e in excluded_names()]
    hint = f"{address or ''} {text_hint or ''}"
    hit_excluded = next((e for e in excluded if e and e in hint), None)
    if hit_excluded and longitude is None and not declared_district:
        return "outside", district_unknown_label(), f"address_outside_scope:{hit_excluded}"
    if longitude is not None and latitude is not None:
        for d in districts_list:
            if d.contains(longitude, latitude):
                return "inside", d.name, f"coarse_bbox:{d.key}"
        if hit_excluded:
            return "outside", district_unknown_label(), f"coords_outside_all_bbox:{hit_excluded}"
        return "pending", district_unknown_label(), "coords_outside_all_bbox"
    for d in districts_list:
        if _district_key(d.name) and _district_key(d.name) in _district_key(hint):
            return "inside", d.name, f"address_district:{d.key}"
    if declared_district:
        matched = resolve_district(declared_district, districts_list)
        if matched is not None:
            return "inside", matched.name, f"declared_district:{matched.key}"
    if hit_excluded:
        return "outside", district_unknown_label(), f"address_outside_scope:{hit_excluded}"
    return "pending", district_unknown_label(), "no_coords_no_district_hint"


def normalize(records: Iterable[StructuredRecord], *, collected_day: date | None = None
              ) -> ValidationReport:
    """Normalise, deduplicate, spatially filter and flag conflicts."""
    districts_list = districts()
    normalized: list[NormalizedRecord] = []
    duplicates: list[dict] = []
    seen_name: dict[tuple[str, str], str] = {}
    seen_coord: dict[tuple[str, str], str] = {}
    seen_source: dict[tuple[str, str, str], str] = {}

    for record in records:
        name = (record.name or "").strip()
        if not name:
            continue
        district = (record.district or district_for_text(f"{name} {record.address or ''}", districts_list)
                   or district_unknown_label())
        status, spatial_district, reason = spatial_verdict(
            record.longitude, record.latitude, districts_list, record.address, name,
            declared_district=record.district if record.district != district_unknown_label() else None)
        if status == "inside" and district == district_unknown_label():
            district = spatial_district
        entry = NormalizedRecord(
            record_id=record.record_id, category=record.category, name=name, district=district,
            source_keys=list(record.source_keys), parent_record_id=record.parent_record_id,
            place_type=record.place_type,
            address=record.address, longitude=record.longitude, latitude=record.latitude,
            coordinate_system=record.coordinate_system, business_date=record.business_date,
            opens_at=record.opens_at, closes_at=record.closes_at, last_entry_at=record.last_entry_at,
            evidence_note=record.evidence_note, evidence_quotes=dict(record.evidence_quotes),
            conflicts=list(record.conflicts), spatial_status=status, spatial_reason=reason,
            raw_path=record.raw_path,
        )
        name_key = (normalize_name(name), record.category)
        if name_key in seen_name:
            duplicates.append({"kind": "name", "kept": seen_name[name_key], "dropped": record.record_id,
                               "name": name, "category": record.category})
            entry.review_items.append(f"同名重复：与 {seen_name[name_key]} 名称归一后相同，需人工区分是否同址")
            normalized.append(entry)
        else:
            seen_name[name_key] = record.record_id
            normalized.append(entry)
            coord = coordinate_fingerprint(record.longitude, record.latitude)
            if coord:
                coord_key = (coord, record.category)
                if coord_key in seen_coord:
                    duplicates.append({"kind": "coordinate", "kept": seen_coord[coord_key],
                                       "dropped": record.record_id, "name": name,
                                       "category": record.category})
                else:
                    seen_coord[coord_key] = record.record_id
        for key in record.source_keys:
            source_key = (key, record.category, name)
            if source_key in seen_source:
                entry.review_items.append(f"同一来源 {key} 支持多条同类记录，需确认是否重复登记")
            seen_source[source_key] = record.record_id

        if status == "pending":
            entry.review_items.append(f"空间归属待核验：{reason}")
        if entry.coordinate_system == "unknown" and (entry.longitude is not None):
            entry.review_items.append("坐标存在但坐标系未声明")
        if entry.coordinate_system == "local_demo":
            entry.review_items.append("示意坐标不得用于正式规划")
        if record.business_date and collected_day and record.business_date == collected_day.isoformat():
            entry.conflicts.append("business_date 等于采集日，疑似把采集日当游玩日")

    conflicts = _detect_conflicts(normalized)
    review = _review_tasks(normalized)
    spatial = {
        "inside": sum(1 for r in normalized if r.spatial_status == "inside"),
        "pending": sum(1 for r in normalized if r.spatial_status == "pending"),
        "outside": sum(1 for r in normalized if r.spatial_status == "outside"),
        "with_coordinates": sum(1 for r in normalized if r.longitude is not None and r.latitude is not None),
        "coordinate_systems": {
            system: sum(1 for r in normalized if r.coordinate_system == system)
            for system in sorted({r.coordinate_system for r in normalized}) or ["unknown"]
        },
    }
    return ValidationReport(records=normalized, duplicates=duplicates, conflicts=conflicts,
                            spatial=spatial, review_tasks=review)


def _detect_conflicts(records: list[NormalizedRecord]) -> list[dict]:
    """Same-name records whose declared fields disagree. Values are preserved, never merged."""
    grouped: dict[str, list[NormalizedRecord]] = {}
    for r in records:
        grouped.setdefault(normalize_name(r.name), []).append(r)
    conflicts: list[dict] = []
    for key, group in grouped.items():
        if len(group) < 2:
            continue
        for field_name in ("opens_at", "closes_at", "last_entry_at", "address", "district"):
            values = {getattr(r, field_name) for r in group if getattr(r, field_name)}
            if len(values) > 1:
                conflicts.append({
                    "conflict_id": f"c_{key}_{field_name}",
                    "field": field_name, "name": group[0].name,
                    "values": sorted(values),
                    "records": [r.record_id for r in group],
                    "resolution": "保留全部原值；按适用日期与核验结果解决，不静默选值",
                })
    return conflicts


def _review_tasks(records: list[NormalizedRecord]) -> list[dict]:
    tasks: list[dict] = []
    for r in records:
        for item in r.review_items:
            tasks.append({"record_id": r.record_id, "name": r.name, "category": r.category,
                          "check_item": item})
    return tasks


def register_review_tasks(db: sqlite3.Connection, report: ValidationReport) -> int:
    """Write review tasks into the ledger. Returns the number of open tasks created."""
    created = 0
    for task in report.review_tasks:
        ledger.add_verify_task(
            db, fact_ref=f"{task['category']}:{task['record_id']}",
            check_item=task["check_item"], method_required=_method_for(task["check_item"]),
            note=f"{task['name']}")
        created += 1
    return created


def _method_for(check_item: str) -> str:
    if "坐标" in check_item or "空间" in check_item:
        return "地图服务或现场核验坐标与所属城区"
    if "营业" in check_item or "开放" in check_item:
        return "运营方官网/电话按目标日确认"
    if "同名" in check_item or "重复" in check_item:
        return "人工比对地址与经营主体"
    if "供应" in check_item:
        return "带门店名菜单或现场点单记录"
    return "人工核验"


# ------------------------------------------------------------------ bundle building

FACT_KIND_FOR_CATEGORY = {
    "scenic": "place", "museum": "place", "street_block": "place", "park": "place",
    "night_market": "place", "restaurant": "place", "transport": "place", "rest": "place",
    "entrance": "place", "food": "food",
}
PLACE_TYPE_FOR_CATEGORY = {
    "scenic": "scenic", "museum": "poi", "street_block": "poi", "park": "poi",
    "night_market": "night_visit", "restaurant": "restaurant", "transport": "transport",
    "rest": "rest", "entrance": "entrance",
}


def build_bundle(report: ValidationReport, *, run_id: str, collected_at: str,
                 include_outside: bool = False) -> dict:
    """Build a city-facts/1 candidate bundle from validated records.

    Every record is emitted as `unverified` with `rights_status=internal_only`
    and no coordinates unless a coordinate system was actually declared. The
    bundle is a candidate for human review; it never enters the runtime store.
    """
    records: list[dict] = []
    source_ids: set[str] = set()
    for r in report.records:
        if r.spatial_status == "outside" and not include_outside:
            continue
        for key in r.source_keys:
            source_ids.add(key)
        fact_kind = FACT_KIND_FOR_CATEGORY.get(r.category)
        if fact_kind is None:
            continue
        base = {
            "fact_id": r.record_id, "version": 1, "source_ids": sorted(r.source_keys),
            "validation_status": "unverified", "business_status": "active", "is_demo": False,
            "rights_status": "internal_only",
        }
        if fact_kind == "place":
            coords = r.longitude is not None and r.latitude is not None
            system = r.coordinate_system if r.coordinate_system in COORD_SYSTEMS else "unknown"
            records.append({**base, "kind": "place", "name": r.name,
                            "place_type": PLACE_TYPE_FOR_CATEGORY.get(r.category, "poi"),
                            "parent_place_id": None, "address": r.address,
                            "longitude": r.longitude if coords and system != "unknown" else None,
                            "latitude": r.latitude if coords and system != "unknown" else None,
                            "coordinate_system": system if coords else "unknown"})
        elif fact_kind == "food":
            records.append({**base, "kind": "food", "name": r.name})
    for key in sorted(source_ids):
        records.append({"fact_id": key, "version": 1, "source_ids": (), "validation_status": "unverified",
                        "business_status": "active", "is_demo": False, "rights_status": "internal_only",
                        "kind": "source", "name": key, "source_type": "other", "url": None,
                        "locator": f"run:{run_id}", "publisher": None, "collected_at": collected_at})
    return {"schema_version": "city-facts/1", "records": records}


def write_json(path: Path, payload: dict) -> Path:
    import json
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
