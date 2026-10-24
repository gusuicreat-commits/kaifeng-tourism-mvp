"""Structured extraction (Skill B engine).

Deterministic, code-driven extraction from already-fetched raw evidence:

* HTML   -> BeautifulSoup (text, tables, meta)
* PDF    -> pdfplumber (optional; falls back to a recorded gap)
* tables -> pandas (CSV/TSV)

Web page content is treated strictly as untrusted data: extraction rules live in
this file and in collector/rules, never in the fetched text. An optional LLM
extraction hook exists but is disabled by default; when enabled it receives the
page text as data with a fixed system prompt, and its output must satisfy the
intermediate schema or it is dropped as a parse error.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

INTERMEDIATE_SCHEMA = "kaifeng-collection-structured/1"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<script.*?</script>", " ", value, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def extract_times(text: str) -> list[str]:
    """Return HH:MM tokens found in text. Never invents a time."""
    return re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", text)


def extract_coordinates(text: str) -> list[tuple[float, float]]:
    """Return plausible GCJ02/WGS84 longitude,latitude pairs.

    A pair is only returned when both numbers are present and inside a broad
    China bounding box; the coordinate system itself stays unknown unless the
    source declares it.
    """
    pairs: list[tuple[float, float]] = []
    for match in re.finditer(r"(-?\d{1,3}\.\d{3,7})\s*[,，]\s*(-?\d{1,3}\.\d{3,7})", text):
        lng, lat = float(match.group(1)), float(match.group(2))
        if 73 <= lng <= 136 and 3 <= lat <= 54:
            pairs.append((lng, lat))
    return pairs


def district_from_text(text: str, districts: Iterable[str]) -> str | None:
    for name in districts:
        if name in text:
            return name
    return None


@dataclass
class StructuredRecord:
    """Intermediate schema record. Null means missing, never a default."""

    record_id: str
    category: str
    source_keys: list[str]
    collected_at: str
    extraction_method: str
    name: str | None = None
    district: str | None = None
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    coordinate_system: str = "unknown"
    place_type: str | None = None
    parent_record_id: str | None = None
    opens_at: str | None = None
    closes_at: str | None = None
    last_entry_at: str | None = None
    business_date: str | None = None
    ticket_text: str | None = None
    program_name: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    vendor_record_id: str | None = None
    food_record_id: str | None = None
    # open_rule 落点：city-facts/1 的 OpenRule 需要 place_id 与业务日期。
    place_id: str | None = None
    reentry: str | None = None
    reservation: str | None = None
    ticket_scope: str | None = None
    evidence_note: str | None = None
    evidence_quotes: dict[str, str] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    confidence_note: str | None = None
    raw_path: str | None = None
    # 入口候选的通行方向；city-facts/1 只接受 entry/exit/both，未确认留 None。
    entrance_direction: str | None = None
    # 抽取层直接声明的待核验项。用于跨命名空间去重、坐标候选冲突这类
    # normalize() 自己看不出来的缺口，避免每条都去改 validate 的判据。
    review_items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id, "category": self.category, "source_keys": list(self.source_keys),
            "collected_at": self.collected_at, "extraction_method": self.extraction_method,
            "name": self.name, "district": self.district, "address": self.address,
            "longitude": self.longitude, "latitude": self.latitude,
            "coordinate_system": self.coordinate_system, "place_type": self.place_type,
            "parent_record_id": self.parent_record_id, "opens_at": self.opens_at,
            "closes_at": self.closes_at, "last_entry_at": self.last_entry_at,
            "business_date": self.business_date, "ticket_text": self.ticket_text,
            "program_name": self.program_name, "starts_at": self.starts_at, "ends_at": self.ends_at,
            "vendor_record_id": self.vendor_record_id, "food_record_id": self.food_record_id,
            "place_id": self.place_id, "reentry": self.reentry,
            "reservation": self.reservation, "ticket_scope": self.ticket_scope,
            "evidence_note": self.evidence_note, "evidence_quotes": dict(self.evidence_quotes),
            "conflicts": list(self.conflicts), "confidence_note": self.confidence_note,
            "raw_path": self.raw_path, "entrance_direction": self.entrance_direction,
            "review_items": list(self.review_items),
        }

    @staticmethod
    def from_dict(data: dict) -> "StructuredRecord":
        known = StructuredRecord.__dataclass_fields__
        return StructuredRecord(**{k: v for k, v in data.items() if k in known})


class StructuredBundle:
    """Container for one run's structured records."""

    schema_version = INTERMEDIATE_SCHEMA

    def __init__(self, *, run_id: str, collected_at: str):
        self.run_id = run_id
        self.collected_at = collected_at
        self.records: list[StructuredRecord] = []

    def add(self, record: StructuredRecord) -> StructuredRecord:
        self.records.append(record)
        return record

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "run_id": self.run_id,
                "collected_at": self.collected_at,
                "records": [r.to_dict() for r in self.records]}

    @staticmethod
    def from_dict(data: dict) -> "StructuredBundle":
        bundle = StructuredBundle(run_id=data["run_id"], collected_at=data["collected_at"])
        bundle.records = [StructuredRecord.from_dict(r) for r in data.get("records", [])]
        return bundle

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


# --------------------------------------------------------------------- extractors

def extract_html_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    main = soup.find("div", id="zoomcon") or soup.find("div", class_="article-content") or soup.body or soup
    return clean_text(main.get_text(" ") if main else "")


def extract_html_tables(html: str) -> list[list[list[str]]]:
    """Return tables as rows of cleaned cells."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables: list[list[list[str]]] = []
    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [clean_text(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def extract_meta(html: str) -> dict[str, str]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    if soup.title and soup.title.string:
        out["title"] = soup.title.string.strip()
    for meta in soup.find_all("meta"):
        key = meta.get("name") or meta.get("property")
        if key and meta.get("content"):
            out[key] = meta["content"].strip()
    for pattern in (r"articlePubDate\s*:\s*'([^']+)'", r"发布时间[：:]\s*([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2})"):
        match = re.search(pattern, html)
        if match:
            out["published_at_hint"] = match.group(1)
            break
    return out


def extract_pdf_text(path: Path) -> tuple[str, str]:
    """Return (text, method). Missing pdfplumber is recorded, not worked around."""
    try:
        import pdfplumber
    except ImportError:
        return "", "pdfplumber_unavailable"
    chunks: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks), "pdfplumber"


def extract_csv_rows(path: Path) -> list[dict[str, str]]:
    import pandas as pd
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    return frame.to_dict(orient="records")


# --------------------------------------------------------------- rule-based parsing

TIME_RANGE = re.compile(r"(\d{1,2}:\d{2})\s*[-—~至]\s*(\d{1,2}:\d{2})")


def parse_opening_rule(text: str) -> dict[str, str | None]:
    """Pull opening candidates from text. Absent values stay None.

    Cross-midnight ranges are not invented: only same-day HH:MM-HH:MM pairs are
    returned, and every match is kept in `all_ranges` so conflicts stay visible.
    """
    matches = TIME_RANGE.findall(text)
    ranges = [f"{a}-{b}" for a, b in matches]
    result: dict[str, str | None] = {"opens_at": None, "closes_at": None, "all_ranges": ";".join(ranges) or None}
    if matches:
        result["opens_at"], result["closes_at"] = matches[0]
    stop = re.search(r"(\d{1,2}:\d{2})\s*(?:起)?停止(?:入园|入馆|进馆|派票|售票)", text)
    if stop:
        result["last_entry_at"] = stop.group(1)
    return result


def parse_address(text: str, city: str = "开封市") -> str | None:
    match = re.search(rf"{city}[^，。；,;]{{0,40}}?(?:路|街|大道|巷|胡同|交叉口|广场)[^，。；,;]{{0,20}}", text)
    return match.group(0).strip() if match else None


def looks_like_closed_notice(text: str) -> bool:
    return bool(re.search(r"临时闭(馆|园|市)|停止营业|休市|闭馆|闭园", text))


def quote_evidence(text: str, needle: str, width: int = 60) -> str | None:
    index = text.find(needle)
    if index < 0:
        return None
    start = max(0, index - width)
    return text[start:index + len(needle) + width].strip()


# ------------------------------------------------------------------ LLM hook (off)

LLM_EXTRACTION_ENABLED = False


def llm_extract(page_text: str, *, schema_hint: dict) -> dict | None:
    """Optional model-assisted extraction.

    Disabled by default. Page text is passed as data only; the system prompt is
    fixed and never contains page text. Output must satisfy schema_hint, else it
    is dropped (parse_error) rather than partially trusted.
    """
    if not LLM_EXTRACTION_ENABLED:
        return None
    raise NotImplementedError("LLM extraction hook is intentionally not enabled in this round")
