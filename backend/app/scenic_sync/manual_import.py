"""Dated, reviewed manual evidence import for Wansui; never calls mini-program APIs."""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime
from pathlib import Path

from .models import NormalizedSession, optional_date, parse_clock

REQUIRED = {"service_date", "area", "program_name", "venue_name", "start_time", "source_updated_on", "evidence_sha256"}
OPTIONAL = {"duration_minutes", "end_time", "entry_deadline", "requires_booking", "weather_sensitive", "effective_from", "effective_to", "schedule_note", "operating_status"}


def boolean(value: str) -> bool | None:
    if not value.strip() or value.strip().lower() == "unknown":
        return None
    if value.strip().lower() in ("true", "1", "yes"):
        return True
    if value.strip().lower() in ("false", "0", "no"):
        return False
    raise ValueError("布尔字段只能为 true/false/unknown")


def evidence_file(base: Path, name: str) -> tuple[bytes, str]:
    if not name:
        raise ValueError("必须提供官方截图/PDF附件")
    path = (base / name).resolve() if not Path(name).is_absolute() else Path(name).resolve()
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".pdf") or not path.is_file():
        raise ValueError("附件须为本地官方截图或PDF")
    if not path.is_relative_to(base.resolve()):
        raise ValueError("证据附件必须位于受保护的指定目录内")
    if path.stat().st_size > 10_000_000:
        raise ValueError("证据附件过大")
    raw = path.read_bytes()
    return raw, hashlib.sha256(raw).hexdigest()


def parse_csv(content: bytes, evidence: bytes, evidence_sha256: str) -> tuple[date, date, list[NormalizedSession]]:
    try:
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError("CSV编码或格式无效") from error
    if not rows or not REQUIRED.issubset(rows[0]) or set(rows[0]) - REQUIRED - OPTIONAL:
        raise ValueError("CSV字段不符合人工核验模板")
    dates = {row["service_date"].strip() for row in rows}
    updated = {row["source_updated_on"].strip() for row in rows}
    if len(dates) != 1 or len(updated) != 1:
        raise ValueError("每批只能有一个服务日期和来源日期")
    service_date, source_updated_on = date.fromisoformat(dates.pop()), date.fromisoformat(updated.pop())
    sessions = []
    for row in rows:
        if not row["program_name"].strip() or not row["venue_name"].strip():
            raise ValueError("节目和文本场地不能为空")
        if row["area"] not in ("东区", "中区", "西区", "未明确"):
            raise ValueError("区域须为东区/中区/西区/未明确")
        if row["evidence_sha256"] != evidence_sha256:
            raise ValueError("CSV证据哈希与实际附件不一致")
        duration = int(row["duration_minutes"]) if row.get("duration_minutes", "").strip() else None
        if duration is not None and not 0 < duration <= 720:
            raise ValueError("持续时长无效")
        effective_from, effective_to = optional_date(row.get("effective_from", "")), optional_date(row.get("effective_to", ""))
        if effective_from and effective_from > service_date or effective_to and effective_to < service_date:
            raise ValueError("有效期不覆盖服务日期")
        if row.get("operating_status", "scheduled") not in ("scheduled", "cancelled", "unknown"):
            raise ValueError("节目运营状态无效")
        sessions.append(NormalizedSession(row["program_name"].strip(), row["venue_name"].strip(), service_date,
            parse_clock(row["start_time"]), row.get("schedule_note", "").strip(), row["area"], duration,
            parse_clock(row.get("end_time", "")), parse_clock(row.get("entry_deadline", "")),
            boolean(row.get("requires_booking", "")), boolean(row.get("weather_sensitive", "")), effective_from, effective_to,
            row.get("operating_status", "scheduled")))
    if not sessions:
        raise ValueError("没有可导入的节目")
    return service_date, source_updated_on, sessions

