"""Public table adapter. Raw HTML never enters the model or solver."""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

from .models import NormalizedSession, ParsedSchedule

DATE_RE = re.compile(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*(?:更新|发布|修订)")
DATE_PARTS = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
CLOCK_RE = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3])[:：][0-5]\d(?!\d)")


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u3000", " ")).strip()


def parse_source_date(text: str) -> date | None:
    for match in DATE_RE.finditer(text):
        parts = DATE_PARTS.search(match.group(0))
        try:
            return date(*(int(value) for value in parts.groups()))
        except (ValueError, AttributeError):
            continue
    return None


def parse_schedule(html: str, service_date: date) -> ParsedSchedule:
    """Parse visible [name, venue, times] rows; ambiguous markup fails closed."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    source_updated_on = parse_source_date(soup.get_text(" ", strip=True)) or parse_source_date(html)
    issues = []
    sessions: list[NormalizedSession] = []
    seen: set[tuple[str, str, str | None, str]] = set()
    tables = 0
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        headers = [[normalize(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"], recursive=False)] for row in rows]
        header = next((cells for cells in headers if len(cells) >= 3 and cells[:3] == ["展示名称", "地点", "时间"]), None)
        if header is None:
            if any("展示名称" in cell for cells in headers for cell in cells):
                issues.append("table_header_changed")
            continue
        tables += 1
        for row in rows:
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) != 3 or any(cell.has_attr("colspan") for cell in cells):
                continue
            name, venue, times_text = (normalize(cell.get_text(" ", strip=True)) for cell in cells)
            if not name or not venue or [name, venue, times_text] == header:
                continue
            if any(cell.find("tr") for cell in cells):
                issues.append("nested_table")
                continue
            found = list(dict.fromkeys(value.replace("：", ":").zfill(5) for value in CLOCK_RE.findall(times_text)))
            # A malformed or partial clock must not silently turn into an untimed show.
            residual = CLOCK_RE.sub("", times_text).strip(" ,，、;；/ ")
            if residual and not any(note in times_text for note in ("以景区公示为准", "待定", "另行通知")):
                issues.append(f"unparsed_time:{name}")
                continue
            if not found and not any(note in times_text for note in ("以景区公示为准", "待定", "另行通知")):
                issues.append(f"missing_time:{name}")
                continue
            for start in found or [None]:
                session = NormalizedSession(name, venue, service_date, None if start is None else __import__('datetime').time.fromisoformat(start),
                                            schedule_note="" if start else times_text)
                key = (name, venue, start, session.schedule_note)
                if key not in seen:
                    seen.add(key)
                    sessions.append(session)
    if not tables:
        issues.append("schedule_table_not_found")
    return ParsedSchedule(source_updated_on, sessions, not issues and bool(sessions), list(dict.fromkeys(issues)))

