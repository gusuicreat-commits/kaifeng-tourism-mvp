"""Scenic alignment run: bring the team workbook's 12 scenics into the pipeline.

The first two collection runs contain museums, restaurants, night markets and a
street block, but no `place_type=scenic` record at all. The team handoff
workbook already carries 12 scenics (`pl_000001`..`pl_000012`, converted into
the team bundle as `pl_wb_000001`..`pl_wb_000012`). This module turns them into
structured records for the collector pipeline so they go through the same
validation, deduplication, spatial filter and review-task machinery as every
other record.

Namespace rule: the team workbook keeps its own `pl_wb_*` fact ids. When the
same real place also exists in a collector run under a different id, the records
are NOT merged -- each namespace keeps its own row and a review task is raised
so a human decides whether they are one place. Merging silently would hide a
real disagreement (for example 山陕甘会馆 is declared 鼓楼区 by the示范区
directory and 龙亭区 by the team workbook).

Fields come only from the team bundle and the workbook: `district`, `address`
and the workbook `description`. Anything absent stays null. Coordinates are
taken from the coordinate-backfill result when it already covers the place,
otherwise they stay `unknown` plus a review task.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import ledger
from .config import DEFAULT_DB_PATH, DEFAULT_RUNS_DIR, REPO_ROOT, districts
from .extract import StructuredBundle, StructuredRecord
from .validate import normalize, normalize_name

WORKBOOK_GLOB = "data/team-handoff/*.xlsx"
TEAM_BUNDLE = REPO_ROOT / "data" / "team-handoff" / "city-facts-teamhandoff-20261023.json"
SHEET = "10_places"
PLACES_SHEET_HEADER = "place_id"


@dataclass
class ScenicSource:
    """One scenic row, taken from the workbook (the richer source)."""
    workbook_place_id: str
    name: str
    address: str | None
    description: str | None
    aliases: str | None
    source_id: str | None
    workbook_status: str
    notes: str | None


@dataclass
class AlignReport:
    scenics: list[dict] = field(default_factory=list)
    cross_namespace: list[dict] = field(default_factory=list)
    coordinates_from_backfill: list[dict] = field(default_factory=list)
    coordinates_missing: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scenics_total": len(self.scenics),
            "scenics": list(self.scenics),
            "cross_namespace_duplicates": list(self.cross_namespace),
            "coordinates_from_backfill": list(self.coordinates_from_backfill),
            "coordinates_missing": list(self.coordinates_missing),
        }


def load_scenic_sources(path: Path) -> list[ScenicSource]:
    """Read the 12 scenic rows out of the workbook's places sheet."""
    import openpyxl
    workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        if SHEET not in workbook.sheetnames:
            raise FileNotFoundError(f"工作簿缺少 {SHEET} 表：{path}")
        rows = list(workbook[SHEET].iter_rows(values_only=True))
    finally:
        workbook.close()
    header_row = next((i for i, row in enumerate(rows)
                       if row and PLACES_SHEET_HEADER in [str(h).strip() if h else "" for h in row]),
                      None)
    if header_row is None:
        raise ValueError(f"{SHEET} 表未找到表头行：{path}")
    header = [str(h).strip() if h else "" for h in rows[header_row]]
    index = {h: i for i, h in enumerate(header) if h}

    def cell(row, key):
        position = index.get(key)
        if position is None or position >= len(row):
            return None
        value = row[position]
        text = str(value).strip() if value is not None else ""
        return text or None

    out: list[ScenicSource] = []
    for row in rows[header_row + 1:]:
        if not row or not any(v not in (None, "") for v in row):
            continue
        if (cell(row, "place_type") or "") != "scenic_spot":
            continue
        place_id = cell(row, "place_id")
        name = cell(row, "name")
        if not (place_id and name):
            continue
        out.append(ScenicSource(
            workbook_place_id=place_id, name=name, address=cell(row, "address"),
            description=cell(row, "description"), aliases=cell(row, "aliases"),
            source_id=cell(row, "source_id"), workbook_status=cell(row, "validation_status") or "",
            notes=cell(row, "notes")))
    return out


def _district_from_address(address: str | None) -> str | None:
    """Read the district the source itself declared in the address text."""
    from .validate import district_for_text
    if not address:
        return None
    return district_for_text(address, districts())


def _contains_match(shorter: str, longer: str) -> bool:
    """Conservative substring test used only to *flag* a possible duplicate."""
    left, right = normalize_name(shorter), normalize_name(longer)
    return len(left) >= 4 and len(right) >= 4 and left != right and (left in right or right in left)


def _other_field(other, key: str):
    """Read one field from either a structured record or an existing-run dict."""
    if isinstance(other, dict):
        return other.get(key)
    return getattr(other, key, None)


def load_existing_place_names(run_ids: list[str], *, runs_dir: Path) -> list[dict]:
    """Names of place-like records already produced by other runs.

    Used only to *flag* a possible cross-namespace duplicate; nothing is merged.
    """
    out: list[dict] = []
    for run_id in run_ids:
        path = Path(runs_dir) / run_id / "structured" / "structured.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.get("records", []):
            name = item.get("name")
            if name:
                out.append({"run_id": run_id, "record_id": item["record_id"], "name": name,
                            "address": item.get("address")})
    return out


def district_hints_from_existing(existing_places: list[dict]) -> dict[str, str]:
    """District declarations that other runs already recorded for the same site.

    The 示范区 public-venue directory lists several museums "inside 清明上河园"
    with an explicit 龙亭区 prefix, so it is a usable address-level hint for the
    scenic itself. It is still a *source statement*, not a verification.
    """
    from .validate import district_for_text
    hints: dict[str, str] = {}
    district_names = [d.name for d in districts()]
    for item in existing_places:
        address = item.get("address") or ""
        if not address:
            continue
        district = district_for_text(address, districts())
        if not district:
            continue
        for name in re.findall(r"[\u4e00-\u9fa5]{2,8}景区", address):
            # 正则容易把城区前缀一起吃进来（"龙亭区清明上河园景区"），
            # 先剥掉已知城区名，否则键匹配不上景区名。
            for district_name in district_names:
                name = name.replace(district_name, "")
            # 同时登记"…景区"与去掉"景区"后缀的键，两种写法都能命中。
            if len(name) >= 3:
                hints.setdefault(normalize_name(name), district)
                if name.endswith("景区"):
                    hints.setdefault(normalize_name(name[:-2]), district)
    return hints


def build_records(sources: list[ScenicSource], *, team_bundle: Path,
                  collected_at: str, backfill: dict | None = None,
                  existing_places: list[dict] | None = None) -> tuple[list[StructuredRecord], AlignReport]:
    """Turn workbook scenics into structured records plus an alignment report."""
    payload = json.loads(Path(team_bundle).read_text(encoding="utf-8"))
    records = payload["records"] if isinstance(payload, dict) else payload
    team_places = {r["fact_id"]: r for r in records if r.get("kind") == "place"}
    team_sources = {r["fact_id"] for r in records if r.get("kind") == "source"}

    backfilled = {}
    for item in (backfill or {}).get("matched", []):
        backfilled[item["record_id"]] = item

    report = AlignReport()
    out: list[StructuredRecord] = []
    existing_places = list(existing_places or [])
    district_hints = district_hints_from_existing(existing_places)
    for source in sources:
        suffix = source.workbook_place_id[3:] if source.workbook_place_id.startswith("pl_") else source.workbook_place_id
        fact_id = f"pl_wb_{suffix}"
        team_place = team_places.get(fact_id)
        source_keys = ["src_teamworkbook_places"]
        if source.source_id:
            source_keys.append(f"src_wb_{source.source_id}")
        source_keys = [k for k in source_keys if k in team_sources] or ["src_teamworkbook_places"]

        district = _district_from_address(source.address)
        district_hint_note = None
        if district is None:
            # 工作簿地址未写城区：不猜，只看既有 run 是否已在同景区地址上声明过。
            hint = district_hints.get(normalize_name(source.name))
            if hint:
                district = hint
                district_hint_note = (f"城区取自既有 run 的同景区地址声明（{hint}），"
                                      f"属来源声明而非核验")
        coordinate_system = "unknown"
        longitude = latitude = None
        coordinate_note = None
        hit = backfilled.get(fact_id)
        if hit and hit.get("coordinate_system") in ("WGS84", "GCJ02"):
            longitude = float(hit["longitude"])
            latitude = float(hit["latitude"])
            coordinate_system = hit["coordinate_system"]
            coordinate_note = f"坐标来源：{hit['source']}（{hit.get('evidence', '')}）"
            report.coordinates_from_backfill.append({"fact_id": fact_id, "name": source.name,
                                                    "source": hit["source"]})
        else:
            report.coordinates_missing.append({
                "fact_id": fact_id, "name": source.name,
                "reason": "坐标回填未覆盖该景区；团队工作簿与 OSM 快照均无该景区坐标",
                "workbook_notes": source.notes})

        record = StructuredRecord(
            record_id=fact_id, category="scenic", source_keys=source_keys,
            collected_at=collected_at, extraction_method="team_workbook_places_sheet",
            name=source.name, district=district, address=source.address,
            longitude=longitude, latitude=latitude, coordinate_system=coordinate_system,
            place_type="scenic",
            evidence_quotes={"address": source.address or "", "description": source.description or ""},
            evidence_note=coordinate_note,
            confidence_note=(f"工作簿 validation_status={source.workbook_status or '未标注'}；"
                             f"来源 {source.source_id or '未标注'}；证据文件不在本仓库，未复核原文"
                             + (f"；{district_hint_note}" if district_hint_note else "")),
            review_items=[], raw_path=None)
        out.append(record)

        report.scenics.append({
            "fact_id": fact_id, "name": source.name, "district": district,
            "district_source": district_hint_note or "工作簿地址文本",
            "address": source.address, "description": source.description,
            "aliases": source.aliases, "source_keys": source_keys,
            "coordinate_system": coordinate_system,
            "workbook_validation_status": source.workbook_status,
            "team_bundle_present": team_place is not None,
            "team_bundle_address": (team_place or {}).get("address"),
        })

        # 跨命名空间重复：同一现实地点在另一命名空间已存在时不合并，
        # 只生成核验任务。这里用保守的子串判定，宁多报不漏报。
        for other in out[:-1] + existing_places:
            other_name = other.name if hasattr(other, "name") else other.get("name", "")
            if not other_name or not _contains_match(other_name, source.name):
                continue
            other_id = _other_field(other, "record_id")
            other_run = _other_field(other, "run_id") or "本次"
            report.cross_namespace.append({
                "kept": [other_id, fact_id],
                "names": [other_name, source.name],
                "existing_run": other_run,
                "resolution": "命名空间不同，两套记录都保留；由人工比对地址与经营主体后裁定是否同一地点",
            })
            record.review_items.append(
                f"跨命名空间潜在重复：与 {other_id}（{other_name}，{other_run}）名称互相包含，"
                f"需人工裁定是否同一地点")
            if other_run == "本次" and hasattr(other, "review_items"):
                other.review_items.append(
                    f"跨命名空间潜在重复：与 {fact_id}（{source.name}）名称互相包含，"
                    f"需人工裁定是否同一地点")

        if district is None:
            record.review_items.append("地址未声明城区，且无坐标可做粗判定，城区归属待核验")
        if longitude is None:
            record.review_items.append("景区坐标缺失，本地来源无候选")
        if source.workbook_status in ("conflict", "pending_review"):
            record.review_items.append(f"工作簿标记 {source.workbook_status}，字段存在未裁定冲突")

    return out, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=None)
    parser.add_argument("--team-bundle", type=Path, default=TEAM_BUNDLE)
    parser.add_argument("--backfill", type=Path, default=None,
                        help="坐标回填报告 JSON（coordinate-backfill.json）")
    parser.add_argument("--existing-runs", nargs="*", default=[],
                        help="用于跨命名空间重复比对的历史 run_id")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--out", type=Path, required=True, help="结构化产物输出目录")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)

    workbook = args.workbook
    if workbook is None:
        matches = sorted(Path(".").glob(WORKBOOK_GLOB))
        if not matches:
            print(json.dumps({"status": "rejected", "error": "未找到团队工作簿"},
                             ensure_ascii=False), file=sys.stderr)
            return 1
        workbook = matches[-1]
    backfill = None
    if args.backfill and Path(args.backfill).exists():
        backfill = json.loads(Path(args.backfill).read_text(encoding="utf-8"))

    sources = load_scenic_sources(workbook)
    existing = load_existing_place_names(args.existing_runs, runs_dir=args.runs_dir)
    records, report = build_records(sources, team_bundle=args.team_bundle,
                                    collected_at=ledger.now_iso(), backfill=backfill,
                                    existing_places=existing)
    out_dir = Path(args.out)
    (out_dir / "structured").mkdir(parents=True, exist_ok=True)
    bundle = StructuredBundle(run_id=out_dir.name, collected_at=ledger.now_iso())
    bundle.records = records
    bundle.write(out_dir / "structured" / "structured.json")
    report_path = args.report or (out_dir / "reports" / "scenic-alignment.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": "ok", "scenics": len(records),
                      "cross_namespace": len(report.cross_namespace),
                      "coordinates_from_backfill": len(report.coordinates_from_backfill),
                      "coordinates_missing": len(report.coordinates_missing),
                      "structured": str(out_dir / "structured" / "structured.json"),
                      "report": str(report_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
