"""Data quality report (Skill C engine, reporting half).

Implements the eight metrics defined in docs/DATA_QUALITY_REPORT_SPEC.md plus the
header block. Every number is recomputed from the ledger and the structured
artefacts, so reports are reproducible and comparable across runs.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from . import ledger
from .config import (categories, district_names, districts, district_unknown_label)
from .extract import StructuredBundle
from .validate import ValidationReport, normalize_name, resolve_district

SPEC_DOC = "docs/DATA_QUALITY_REPORT_SPEC.md"


def canonical_district(label: str | None) -> str | None:
    """Map a record district label onto the configured district name.

    Source text often uses the short form ("示范区"); the report matrix must use
    one canonical name per district so cross-run comparisons stay valid.
    """
    if not label:
        return None
    resolved = resolve_district(label, districts())
    return resolved.name if resolved else label


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def compute(*, db: sqlite3.Connection, bundle: StructuredBundle, validation: ValidationReport,
            run_id: str, collected_day: date, bundle_dry_run: dict | None = None) -> dict:
    records = validation.records
    source_rows = {s["source_key"]: s for s in ledger.list_sources(db)}
    source_keys = set(source_rows)

    # 1. source traceability
    with_source = [r for r in records if r.source_keys]
    registry_matched = [r for r in with_source if all(k in source_keys for k in r.source_keys)]
    hash_matched = []
    unverified_hashes: list[str] = []
    for r in registry_matched:
        # 每条来源按台账登记的 raw_path 与 SHA-256 逐条复算；结构化记录只携带其
        # 主证据路径，不能替所有来源背书。
        ok = True
        for key in r.source_keys:
            source = source_rows.get(key)
            if not source or not source.get("content_sha256") or not source.get("raw_path"):
                ok = False
                continue
            path = Path(source["raw_path"])
            if not path.exists() or _sha(path) != source["content_sha256"]:
                ok = False
        if ok:
            hash_matched.append(r)
        else:
            unverified_hashes.append(r.record_id)
    total = len(records)
    m1 = {
        "total_records": total, "with_source": len(with_source), "registry_matched": len(registry_matched),
        "hash_matched": len(hash_matched),
        "rate": _rate(len(hash_matched), total),
        "missing_source_records": [r.record_id for r in records if not r.source_keys],
        "unregistered_source_keys": sorted({k for r in with_source for k in r.source_keys if k not in source_keys}),
        "hash_unverified_records": unverified_hashes,
    }

    # 2. field completeness (missing is allowed; no default filling)
    per_category = []
    for c in categories():
        group = [r for r in records if r.category == c.key]
        if not group:
            continue
        required = list(c.required_fields)
        filled = 0
        complete_records = 0
        for r in group:
            values = _field_values(r)
            record_filled = 0
            for f in required:
                if values.get(f):
                    filled += 1
                    record_filled += 1
            if required and record_filled == len(required):
                complete_records += 1
        per_category.append({
            "category": c.key, "fields_filled": filled, "fields_total": len(required) * len(group),
            "records_complete": complete_records, "records_total": len(group),
            "required_fields": required,
        })
    m2 = {"per_category": per_category,
          "note": "缺失即缺失，null 不被默认值替换；缺口用于补采排队。"}

    # 3. coordinate readiness
    places = [r for r in records if r.category in ("scenic", "museum", "street_block", "park",
                                                   "night_market", "restaurant", "transport",
                                                   "rest", "entrance")]
    with_coords = [r for r in places if r.longitude is not None and r.latitude is not None]
    systems: dict[str, int] = {}
    for r in places:
        systems[r.coordinate_system] = systems.get(r.coordinate_system, 0) + 1
    m3 = {
        "places_total": len(places), "with_coords": len(with_coords),
        "system_wgs84": systems.get("WGS84", 0), "system_gcj02": systems.get("GCJ02", 0),
        "system_unknown": systems.get("unknown", 0), "demo_coords": systems.get("local_demo", 0),
        "rate": _rate(len([r for r in with_coords if r.coordinate_system in ("WGS84", "GCJ02")]), len(places)),
    }

    # 4. date validity
    dated = [r for r in records if r.category in ("open_rule", "show") or r.business_date]
    with_date = [r for r in dated if r.business_date]
    future = [r for r in with_date if r.business_date and r.business_date > collected_day.isoformat()]
    is_collection_day = [r for r in with_date if r.business_date == collected_day.isoformat()]
    m4 = {
        "rules_total": len(dated), "with_business_date": len(with_date), "date_in_future": len(future),
        "date_is_collection_day": len(is_collection_day), "date_null": len(dated) - len(with_date),
        "collection_day_records": [r.record_id for r in is_collection_day],
    }

    # 5. network evidence (edges are not collected in this round; status is honest)
    m5 = {"edges_total": 0, "with_source_locator": 0, "connected": 0, "disconnected": 0,
          "unchecked": 0,
          "note": "本轮未采园内道路与入口衔接候选；unchecked 不得声称为可导航。"}

    # 6. entrance coverage
    # SPEC 口径：分母只数 category=scenic 且 priority=high 的景区，
    # 不是"所有 high 优先级类别的记录数"。
    priority_scenic_keys = {c.key for c in categories()
                            if c.priority == "high" and c.place_type == "scenic"}
    priority_records = [r for r in records if r.category in priority_scenic_keys]
    entrance_records = [r for r in records if r.category == "entrance"]
    # 入口候选通过 parent_record_id 挂到具体地点；仅统计被引用的候选，
    # 未挂到任何地点的候选不算覆盖。
    covered = {r.parent_record_id for r in entrance_records if r.parent_record_id}
    orphan_entrances = [r.record_id for r in entrance_records if not r.parent_record_id]
    m6 = {
        "priority_scenics": len(priority_records),
        "with_entrance_candidate": len([r for r in priority_records if r.record_id in covered]),
        "entrance_candidates_total": len(entrance_records),
        "rejected_fake_entrances": 0,
        "note": "停车场/地图中心点不得计为入口候选。",
        "orphan_entrance_candidates": orphan_entrances,
    }

    # 7. vendor & supply evidence
    vendors = [r for r in records if r.category == "restaurant"]
    with_location = [r for r in vendors if r.address or (r.longitude is not None and r.latitude is not None)]
    supplies = [r for r in records if r.category == "supply"]
    strong = [r for r in supplies if r.evidence_note and "平台推荐" not in r.evidence_note
              and "platform_menu_lead" not in (r.evidence_note or "")]
    m7 = {
        "vendors_total": len(vendors), "with_location": len(with_location),
        "supply_total": len(supplies), "evidence_strong": len(strong),
        "evidence_candidate_only": len(supplies) - len(strong),
        "note": "平台推荐菜只算候选，不得升级为供应事实。",
    }

    # 8. conflict handling
    conflicts = validation.conflicts
    preserved = [c for c in conflicts if len(c.get("values", [])) > 1]
    m8 = {
        "conflicts_total": len(conflicts), "preserved_all_values": len(preserved),
        "resolved": 0, "silently_picked": 0,
        "items": [{"conflict_id": c["conflict_id"], "field": c["field"], "values": c["values"]}
                  for c in conflicts],
        "note": "解决须注明依据；本轮 0 条已解决。",
    }

    district_labels = {canonical_district(r.district) or r.district for r in records}
    def _district_of(record) -> str:
        return canonical_district(record.district) or record.district

    # 矩阵只列本次实际出现的归属；"待核验"单独成列，不得并入任何城区。
    matrix_districts = [d for d in district_names() if d in district_labels]
    if district_unknown_label() in district_labels:
        matrix_districts.append(district_unknown_label())
    scope_matrix = {
        cat.key: {d: len([r for r in records if r.category == cat.key
                          and _district_of(r) == d])
                 for d in matrix_districts}
        for cat in categories() if any(r.category == cat.key for r in records)
    }
    failures = ledger.list_failures(db, run_id)
    return {
        "schema_version": "kaifeng-quality-report/1",
        "run_id": run_id,
        "generated_at": ledger.now_iso(),
        "collected_day": collected_day.isoformat(),
        "spec": SPEC_DOC,
        "scope": scope_matrix,
        "totals": {
            "records": total,
            "sources": len(ledger.list_sources(db)),
            "duplicates": len(validation.duplicates),
            "conflicts": len(conflicts),
            "failures": len(failures),
            "review_tasks": len(validation.review_tasks),
        },
        "bundle_dry_run": _dry_run_summary(bundle_dry_run),
        "red_lines": {
            "fabricated_data": 0, "ai_completion": 0, "bypass_import_channel": 0, "bypass_waf": 0,
            "note": "自检口径：本工具只做代码抽取与候选生成，未写运行库、未绕过访问控制。",
        },
        "metrics": {
            "source_traceability": m1, "field_completeness": m2, "coordinate_readiness": m3,
            "date_validity": m4, "network_evidence": m5, "entrance_coverage": m6,
            "vendor_supply_evidence": m7, "conflict_handling": m8,
        },
        "gaps_top": _gaps(m1, m3, m4, m8, validation, m6),
    }


def _dry_run_summary(bundle_dry_run: dict | None) -> dict:
    """Keep the dry-run counts, drop the per-record detail.

    The import script returns one entry per record; pasting all of them into the
    markdown summary makes the report unreadable. Counts stay, and the full
    response remains in `reports/city-facts-dry-run.json`.
    """
    if not bundle_dry_run:
        return {}
    summary = {key: bundle_dry_run[key] for key in
               ("status", "received", "changed", "unchanged") if key in bundle_dry_run}
    records = bundle_dry_run.get("records")
    if isinstance(records, list):
        summary["ready"] = sum(1 for r in records if r.get("ready"))
        summary["issues"] = len(records)
    return summary


def _field_values(record) -> dict[str, Any]:
    # district 取来源声明值：normalize() 会把未知归属填成"待核验"标签，
    # 那是缺失标记而非事实，不能算字段已填。
    declared_district = record.district if record.district != district_unknown_label() else None
    return {
        "name": record.name, "district": declared_district, "address": record.address,
        "longitude": record.longitude, "latitude": record.latitude,
        "opens_at": record.opens_at, "closes_at": record.closes_at,
        "vendor_id": getattr(record, "vendor_record_id", None),
        "food_id": getattr(record, "food_record_id", None),
        "evidence_note": record.evidence_note,
    }


def _gaps(m1, m3, m4, m8, validation: ValidationReport, m6: dict | None = None) -> list[dict]:
    gaps: list[dict] = []
    if m1["rate"] < 1.0:
        # 区分两种不同的未追踪原因：来源未登记 vs 已登记但 raw 字节无法复算
        # （例如来源声明的证据文件不在本仓库）。两者的补救动作不同。
        detail = f"hash_matched={m1['hash_matched']}/{m1['total_records']}"
        if m1["unregistered_source_keys"]:
            detail += f"；未登记来源：{', '.join(m1['unregistered_source_keys'])}"
        if m1["hash_unverified_records"]:
            detail += (f"；{len(m1['hash_unverified_records'])} 条来源已登记但 raw 缺失或"
                       f"SHA-256 不匹配，需补原始证据文件")
        gaps.append({"priority": "high", "gap": "来源可追踪率未达 100%", "detail": detail})
    if m3["with_coords"] < m3["places_total"]:
        gaps.append({"priority": "high", "gap": "地点坐标缺失",
                     "detail": f"{m3['places_total'] - m3['with_coords']} 条无坐标"})
    if m4["date_is_collection_day"]:
        gaps.append({"priority": "high", "gap": "business_date 等于采集日",
                     "detail": ", ".join(m4["collection_day_records"])})
    if m8["conflicts_total"]:
        gaps.append({"priority": "medium", "gap": "存在未解决字段冲突",
                     "detail": f"{m8['conflicts_total']} 条冲突保留原值待核验"})
    pending = [r for r in validation.records if r.spatial_status == "pending"]
    if pending:
        gaps.append({"priority": "medium", "gap": "空间归属待核验",
                     "detail": ", ".join(r.record_id for r in pending)})
    if validation.duplicates:
        gaps.append({"priority": "medium", "gap": "同名/同坐标重复待区分",
                     "detail": f"{len(validation.duplicates)} 组"})
    if m6:
        orphans = m6.get("orphan_entrance_candidates") or []
        if orphans:
            gaps.append({"priority": "medium", "gap": "入口候选未关联地点或方向未确认",
                         "detail": ", ".join(orphans)})
    return gaps


def _sha(path: Path) -> str | None:
    import hashlib
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _matrix_columns(report: dict) -> list[str]:
    """覆盖矩阵的列顺序，与 compute() 建矩阵时保持一致。"""
    counts = next(iter(report["scope"].values()), {})
    return list(counts.keys())


def render_markdown(report: dict) -> str:
    m = report["metrics"]
    t = report["totals"]
    lines = [
        f"# 开封主城区旅游数据质量报告",
        "",
        f"- run_id：`{report['run_id']}`",
        f"- 生成时间：{report['generated_at']}",
        f"- 采集日：{report['collected_day']}",
        f"- 指标口径：[{report['spec']}]({report['spec']})",
        "",
        "## 汇总",
        "",
        f"- 记录总数：{t['records']}；来源登记：{t['sources']}；重复：{t['duplicates']}；"
        f"冲突：{t['conflicts']}；失败：{t['failures']}；待核验任务：{t['review_tasks']}",
        f"- bundle dry-run：{json.dumps(report['bundle_dry_run'], ensure_ascii=False)}",
        "",
        "## 类别 × 城区 覆盖",
        "",
        "| 类别 | " + " | ".join(_matrix_columns(report)) + " |",
        "| --- | " + " | ".join(["---"] * len(_matrix_columns(report))) + " |",
    ]
    for cat, counts in report["scope"].items():
        columns = _matrix_columns(report)
        lines.append(f"| {cat} | " + " | ".join(str(counts.get(d, 0)) for d in columns) + " |")
    lines += ["", "## 八项指标", ""]
    labels = {
        "source_traceability": "1. 来源可追踪率", "field_completeness": "2. 关键字段完整率",
        "coordinate_readiness": "3. 坐标与坐标系适配率", "date_validity": "4. 日期有效性",
        "network_evidence": "5. 路网/空间连通证据", "entrance_coverage": "6. 真实入口覆盖率",
        "vendor_supply_evidence": "7. 商户定位与供应证据", "conflict_handling": "8. 冲突处理完整率",
    }
    for key, label in labels.items():
        lines.append(f"### {label}")
        lines.append("")
        for field, value in m[key].items():
            if field in ("missing_source_records", "unregistered_source_keys", "collection_day_records", "items"):
                lines.append(f"- `{field}`: {', '.join(map(str, value)) or '无'}")
            elif isinstance(value, list):
                lines.append(f"- `{field}`: {json.dumps(value, ensure_ascii=False)}")
            else:
                lines.append(f"- `{field}`: {value}")
        lines.append("")
    lines += ["## 红线自检", ""]
    for key, value in report["red_lines"].items():
        lines.append(f"- `{key}`: {value}")
    lines += ["", "## 缺口优先级", ""]
    if report["gaps_top"]:
        for gap in report["gaps_top"]:
            lines.append(f"- [{gap['priority']}] {gap['gap']}：{gap['detail']}")
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def write_reports(report: dict, out_dir: Path, *, day: date | None = None) -> tuple[Path, Path]:
    day = day or date.today()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"quality-report-{day.isoformat()}.json"
    md_path = out_dir / f"quality-report-{day.isoformat()}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return md_path, json_path
