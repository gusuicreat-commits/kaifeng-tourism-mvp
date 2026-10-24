"""Target-day open-rule instantiator (Skill C engine, date half).

The team handoff workbook stores *recurring* opening rules (`12_open_rules`):
`daily` and `date_range` shapes with an ISO weekday set. city-facts/1 refuses
those, because an `OpenRule` must name one concrete `business_date` -- a weekly
rule cannot be used as a fact for a specific day. This module instantiates the
recurring rules for exactly one requested day.

Boundaries that are enforced here, not approximated:

* `--date` is required. There is no default: the collection day must never be
  substituted for a play day.
* A rule only applies when its `effective_from`/`effective_to` window contains
  the requested date, its weekday set contains that weekday, and its
  `validation_status` is not `expired`/`cancelled`/`rejected`.
* A range that crosses midnight (18:30-01:00) is *not* forced into the same
  business_date. It is emitted as a gap with the split the operator would have
  to confirm, because closing time past midnight belongs to the next day.
* Incomplete ranges, unparsable times, missing `place_id` mappings and rules
  whose workbook status is `conflict` are reported as gaps rather than guessed.

The output is a city-facts/1 candidate bundle: every record is `unverified`
with `rights_status=internal_only`, and each one carries the workbook source ids
so the raw evidence stays traceable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
WORKBOOK_GLOB = "data/team-handoff/*.xlsx"
SHEET = "12_open_rules"

# 工作簿 place_id -> 团队候选包 place fact_id 前缀
PLACE_NAMESPACE_PREFIX = "pl_wb_"
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

# 这些状态不得进入候选：到期/取消/弃用的规则不是"待核验"，是不适用。
INAPPLICABLE_STATUS = {"expired", "cancelled", "rejected", "mock"}


@dataclass
class RuleRow:
    rule_id: str
    place_id: str
    rule_name: str
    rule_type: str
    effective_from: str | None
    effective_to: str | None
    weekdays: set[int]
    open_time: str | None
    close_time: str | None
    last_entry_time: str | None
    timezone_text: str
    ticket_scope: str | None
    reservation_required: str | None
    reentry_allowed: str | None
    special_rules: str | None
    source_id: str | None
    validation_status: str
    notes: str | None


@dataclass
class InstantiationReport:
    business_date: str
    applicable: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "business_date": self.business_date,
            "instantiated": len(self.applicable),
            "gaps": len(self.gaps),
            "records": list(self.applicable),
            "gap_items": list(self.gaps),
        }


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value) -> str | None:
    text = _clean(value)
    if not text:
        return None
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return None
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()


def _parse_time(value) -> str | None:
    text = _clean(value)
    if not text:
        return None
    match = TIME_PATTERN.match(text)
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _parse_weekdays(value) -> set[int]:
    text = _clean(value)
    if not text:
        return set()
    out: set[int] = set()
    for part in re.split(r"[;；,，\s]+", text):
        if not part:
            continue
        if part.isdigit() and 1 <= int(part) <= 7:
            out.add(int(part))
    return out


def load_rule_rows(path: Path) -> list[RuleRow]:
    """Read 12_open_rules. The header is row 3 in the real workbook."""
    import openpyxl
    workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        if SHEET not in workbook.sheetnames:
            raise FileNotFoundError(f"工作簿缺少 {SHEET} 表：{path}")
        rows = list(workbook[SHEET].iter_rows(values_only=True))
    finally:
        workbook.close()
    header_row = next((i for i, row in enumerate(rows)
                       if row and "open_rule_id" in [str(h).strip() if h else "" for h in row]), None)
    if header_row is None:
        raise ValueError(f"{SHEET} 表未找到表头行：{path}")
    header = [str(h).strip() if h else "" for h in rows[header_row]]
    index = {h: i for i, h in enumerate(header) if h}
    out: list[RuleRow] = []
    for row in rows[header_row + 1:]:
        if not row or not any(v not in (None, "") for v in row):
            continue
        rule_id = _clean(row[index["open_rule_id"]] if "open_rule_id" in index else None)
        if not rule_id:
            continue
        out.append(RuleRow(
            rule_id=rule_id,
            place_id=_clean(row[index["place_id"]] if "place_id" in index else None) or "",
            rule_name=_clean(row[index["rule_name"]] if "rule_name" in index else None) or rule_id,
            rule_type=_clean(row[index["rule_type"]] if "rule_type" in index else None) or "",
            effective_from=_parse_date(row[index["effective_from"]] if "effective_from" in index else None),
            effective_to=_parse_date(row[index["effective_to"]] if "effective_to" in index else None),
            weekdays=_parse_weekdays(row[index["weekdays"]] if "weekdays" in index else None),
            open_time=_parse_time(row[index["open_time"]] if "open_time" in index else None),
            close_time=_parse_time(row[index["close_time"]] if "close_time" in index else None),
            last_entry_time=_parse_time(row[index["last_entry_time"]] if "last_entry_time" in index else None),
            timezone_text=_clean(row[index["timezone"]] if "timezone" in index else None) or "",
            ticket_scope=_clean(row[index["ticket_scope"]] if "ticket_scope" in index else None),
            reservation_required=_clean(row[index["reservation_required"]] if "reservation_required" in index else None),
            reentry_allowed=_clean(row[index["reentry_allowed"]] if "reentry_allowed" in index else None),
            special_rules=_clean(row[index["special_rules"]] if "special_rules" in index else None),
            source_id=_clean(row[index["source_id"]] if "source_id" in index else None),
            validation_status=_clean(row[index["validation_status"]] if "validation_status" in index else None) or "",
            notes=_clean(row[index["notes"]] if "notes" in index else None),
        ))
    return out


def applies_on(rule: RuleRow, business: date) -> str | None:
    """Return None when the rule applies, otherwise the reason it does not."""
    if rule.validation_status == "conflict":
        # 字段冲突是"待人工裁定"，不是失效规则；不落库，转核验。
        return "规则字段冲突未裁定，需人工核验（不落库）"
    if rule.validation_status in INAPPLICABLE_STATUS:
        return f"规则状态 {rule.validation_status} 不适用"
    if rule.rule_type == "date_range":
        if rule.effective_from and business.isoformat() < rule.effective_from:
            return f"生效区间未开始（{rule.effective_from} 起）"
        if rule.effective_to and business.isoformat() > rule.effective_to:
            return f"生效区间已结束（{rule.effective_to} 止）"
    if rule.weekdays and business.isoweekday() not in rule.weekdays:
        return f"星期不适用（规则适用 {sorted(rule.weekdays)}，目标日为周{business.isoweekday()}）"
    return None


def _map(value: str | None, mapping: dict[str, str], fallback: str = "unknown") -> str:
    return mapping.get((value or "").strip().lower(), fallback)


RESERVATION_MAP = {"true": "required", "false": "not_required", "unknown": "unknown"}
REENTRY_MAP = {"true": "allowed", "false": "forbidden", "unknown": "unknown"}


def instantiate(rule: RuleRow, business: date, *, place_ids: set[str],
                source_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """Build open_rule records for one rule on one business date.

    Returns (records, gaps). An empty `records` list with a populated `gaps`
    list means the rule could not be instantiated without guessing.
    """
    fact_place_id = f"{PLACE_NAMESPACE_PREFIX}{rule.place_id[3:] if rule.place_id.startswith('pl_') else rule.place_id}"
    refs = [f"src_teamworkbook_open_rules"]
    if rule.source_id:
        refs.append(f"src_wb_{rule.source_id}")
    refs = [r for r in refs if r in source_ids]
    if not refs:
        refs = ["src_teamworkbook_open_rules"]

    def gap(reason: str, **extra) -> tuple[list[dict], list[dict]]:
        return [], [{"rule_id": rule.rule_id, "place_id": rule.place_id, "name": rule.rule_name,
                     "reason": reason, **extra}]

    if fact_place_id not in place_ids:
        return gap("地点不在候选包内，open_rule 无法挂靠", mapped_place_id=fact_place_id)
    if not rule.weekdays:
        return gap("规则未声明适用星期，无法判定目标日是否适用")
    opens, closes = rule.open_time, rule.close_time
    if (opens is None) != (closes is None):
        return gap("营业起止不完整", open_time=opens, close_time=closes)
    last_entry = rule.last_entry_time
    if last_entry and opens and closes and not (opens <= last_entry <= closes):
        return gap("停止入园时间不在营业区间内", last_entry_time=last_entry)

    base = {
        "fact_id": f"open_rule_wb_{rule.rule_id[3:] if rule.rule_id.startswith('or_') else rule.rule_id}_{business.isoformat()}",
        "version": 1, "source_ids": sorted(set(refs)),
        "validation_status": "unverified", "business_status": "active", "is_demo": False,
        "rights_status": "internal_only",
        "kind": "open_rule", "place_id": fact_place_id,
        "business_date": business.isoformat(),
        "reentry": _map(rule.reentry_allowed, REENTRY_MAP),
        "reservation": _map(rule.reservation_required, RESERVATION_MAP),
    }
    scope = (rule.ticket_scope or "").strip()
    if len(scope) > 200:
        # city-facts/1 对 ticket_scope 有 200 字上限；超出不静默截断，转核验。
        return gap(f"ticket_scope 超 200 字上限（{len(scope)}），不截断，需人工改写",
                   ticket_scope_len=len(scope))
    if scope:
        base["ticket_scope"] = scope

    if opens and closes:
        if opens >= closes:
            # 跨日营业（如夜市 18:30-01:00）。city-facts/1 要求所有时刻都落在
            # business_date 内，跨日必须拆分；本层不猜第二天的日期归属，
            # 只把需要人工确认的拆法报出来。
            return gap(
                f"营业区间跨日（{opens}-{closes}），city-facts/1 要求按业务日拆分",
                suggested_split=[f"{business.isoformat()} {opens}-23:59",
                                 f"次日 00:00-{closes}"])
        base["opens_at"] = f"{business.isoformat()}T{opens}:00+08:00"
        base["closes_at"] = f"{business.isoformat()}T{closes}:00+08:00"
        if last_entry:
            base["last_entry_at"] = f"{business.isoformat()}T{last_entry}:00+08:00"
    return [base], []


def run(workbook: Path, business: date, *, team_bundle: Path) -> tuple[dict, InstantiationReport]:
    """Instantiate every recurring rule for `business` into a candidate bundle."""
    payload = json.loads(Path(team_bundle).read_text(encoding="utf-8"))
    records = payload["records"] if isinstance(payload, dict) else payload
    place_ids = {r["fact_id"] for r in records if r.get("kind") == "place"}
    by_id = {r["fact_id"]: r for r in records}
    source_ids = {r["fact_id"] for r in records if r.get("kind") == "source"}
    # 12_open_rules 从未进过团队候选包，所以它的来源登记由本模块自带，
    # locator 指向工作簿与工作表（与 import_team_workbook.py 的命名一致）。
    workbook_source = {
        "fact_id": "src_teamworkbook_open_rules", "version": 1, "source_ids": (),
        "validation_status": "unverified", "business_status": "active", "is_demo": False,
        "rights_status": "internal_only", "kind": "source",
        "name": "团队数据工作簿v1.1（成员交接）·open_rules",
        "source_type": "other", "url": None,
        "locator": f"{workbook}#{SHEET}", "publisher": "汴梁游记数据小组",
        "collected_at": "2026-10-23T22:49:00+08:00",
    }
    source_ids.add(workbook_source["fact_id"])
    declared = {r["fact_id"] for r in records if r.get("kind") == "source"}
    extra_sources = [workbook_source]

    report = InstantiationReport(business_date=business.isoformat())
    out: list[dict] = []
    for rule in load_rule_rows(Path(workbook)):
        reason = applies_on(rule, business)
        if reason is not None:
            report.gaps.append({"rule_id": rule.rule_id, "place_id": rule.place_id,
                                "name": rule.rule_name, "reason": reason})
            continue
        records_for_rule, gaps = instantiate(rule, business, place_ids=place_ids,
                                             source_ids=source_ids)
        out.extend(records_for_rule)
        report.gaps.extend(gaps)
        if records_for_rule:
            report.applicable.append({
                "rule_id": rule.rule_id, "place_id": records_for_rule[0]["place_id"],
                "name": rule.rule_name,
                "opens_at": records_for_rule[0].get("opens_at"),
                "closes_at": records_for_rule[0].get("closes_at"),
                "last_entry_at": records_for_rule[0].get("last_entry_at"),
                "reservation": records_for_rule[0]["reservation"],
                "reentry": records_for_rule[0]["reentry"],
                "ticket_scope": records_for_rule[0].get("ticket_scope"),
                "validation_status_in_workbook": rule.validation_status,
            })
    # open_rule 引用团队包里的 place 与 source。为了让候选包能独立通过
    # dry-run（而不是只在与团队包合并时才成立），这里把被引用的 place 及其
    # 来源一并带上；它们保持团队包里的原值，本模块不改写。
    needed: set[str] = set()
    for record in out:
        needed.add(record["place_id"])
        needed.update(record["source_ids"])
    for place_id in [r["place_id"] for r in out]:
        place = by_id.get(place_id)
        if place:
            needed.update(place.get("source_ids") or [])
    included = [by_id[key] for key in sorted(needed) if key in by_id]
    bundle = {"schema_version": "city-facts/1", "records": extra_sources + included + out}
    return bundle, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat,
                        help="目标游玩日 YYYY-MM-DD；不给默认值，采集日不能代替游玩日")
    parser.add_argument("--workbook", type=Path, default=None,
                        help="团队工作簿路径；默认取 data/team-handoff/*.xlsx")
    parser.add_argument("--team-bundle", type=Path,
                        default=Path("data/team-handoff/city-facts-teamhandoff-20261023.json"))
    parser.add_argument("--out", type=Path, default=None, help="候选包输出路径")
    parser.add_argument("--report", type=Path, default=None, help="匹配报告输出路径")
    args = parser.parse_args(argv)

    workbook = args.workbook
    if workbook is None:
        matches = sorted(Path(".").glob(WORKBOOK_GLOB))
        if not matches:
            print(json.dumps({"status": "rejected", "error": "未找到团队工作簿"},
                             ensure_ascii=False), file=sys.stderr)
            return 1
        workbook = matches[-1]
    bundle, report = run(workbook, args.date, team_bundle=args.team_bundle)

    out = args.out or Path(f"data/collection-runs/open_rules_{args.date.isoformat()}/bundle/city-facts-candidate.json")
    report_path = args.report or out.with_name("open-rules-instantiation.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "business_date": report.business_date,
                      "bundle": str(out), "report": str(report_path),
                      "instantiated": len(report.applicable), "gaps": len(report.gaps)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
