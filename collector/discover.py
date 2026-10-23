"""Source discovery (Skill A engine).

Turns the configured 城区 x 类别 x 关键词 matrix into discovery tasks and
resolves them through an injected search backend. The toolkit never calls a
search provider by itself: the caller passes a callable that returns results in
the common shape, so the mechanism is testable without network access and the
access method is recorded explicitly.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import ledger
from .config import DiscoveryTask, categories, districts, discovery_tasks

SearchBackend = Callable[[str, str], Iterable[dict]]


@dataclass
class DiscoveryHit:
    title: str
    url: str
    snippet: str = ""
    published_at: str | None = None
    access_method: str = "web_search"


@dataclass
class DiscoveryResult:
    task: DiscoveryTask
    hits: list[DiscoveryHit] = field(default_factory=list)
    source_keys: list[str] = field(default_factory=list)
    error_kind: str | None = None
    error_detail: str | None = None


def _normalize(raw: dict, access_method: str) -> DiscoveryHit:
    return DiscoveryHit(
        title=str(raw.get("title") or "").strip() or "(untitled)",
        url=str(raw.get("url") or "").strip(),
        snippet=str(raw.get("text") or raw.get("snippet") or "").strip(),
        published_at=raw.get("published_at"),
        access_method=access_method,
    )


def classify_access_kind(url: str, title: str = "") -> str:
    """Map a URL to a registry access_kind without asserting its reliability."""
    lowered = url.lower()
    if lowered.startswith("search://"):
        return "search_snapshot"
    if any(host in lowered for host in ("gov.cn", ".gov.", "amap.com", "gaode.com")):
        return "official_site"
    if any(host in lowered for host in ("ctrip.com", "qunar.com", "bendibao.com", "meituan.com",
                                        "dianping.com", "zhihu.com", "weibo.com", "mp.weixin.qq.com")):
        return "platform"
    if any(host in lowered for host in ("openstreetmap.org", "osm.org")):
        return "map_service"
    return "platform"


def infer_source_type(url: str, publisher_hint: str = "") -> str:
    lowered = f"{url} {publisher_hint}".lower()
    if "gov.cn" in lowered or "人民政府" in publisher_hint or "文旅厅" in publisher_hint:
        return "official"
    if "amap" in lowered or "高德" in publisher_hint:
        return "map_service"
    if "现场" in publisher_hint or "走查" in publisher_hint:
        return "field"
    return "other"


def run_discovery(db: sqlite3.Connection, *, run_id: str, backend: SearchBackend,
                  tasks: list[DiscoveryTask] | None = None,
                  access_method: str = "web_search") -> list[DiscoveryResult]:
    """Execute discovery tasks and register every hit as a source.

    Duplicate URLs are deduplicated inside the ledger; nothing is fetched here.
    """
    tasks = tasks or discovery_tasks()
    results: list[DiscoveryResult] = []
    for task in tasks:
        query = f"{task.district} {task.keyword}".strip()
        result = DiscoveryResult(task=task)
        try:
            raw_hits = list(backend(query, task.category))
        except Exception as exc:  # discovery backend failure is data, not a crash
            result.error_kind = "discovery_backend_error"
            result.error_detail = f"{type(exc).__name__}: {exc}"[:400]
            ledger.record_failure(db, run_id=run_id, source_key=None,
                                  failure_kind=result.error_kind,
                                  detail=f"{query} :: {result.error_detail}")
            results.append(result)
            continue
        seen: set[str] = set()
        for raw in raw_hits:
            hit = _normalize(raw, access_method)
            if not hit.url or hit.url in seen:
                continue
            seen.add(hit.url)
            result.hits.append(hit)
            key = _source_key_for(task, hit.url)
            ledger.upsert_source(
                db, source_key=key, name=hit.title, url=hit.url,
                publisher=None, source_type=infer_source_type(hit.url, hit.title),
                access_kind=classify_access_kind(hit.url, hit.title),
                access_status="unchecked", license_status="unknown",
                notes=f"discovery task={task.category}/{task.district} keyword={task.keyword}",
                last_checked_at=None,
            )
            result.source_keys.append(key)
        results.append(result)
    return results


def _source_key_for(task: DiscoveryTask, url: str) -> str:
    import hashlib
    digest = hashlib.sha256(f"{task.category}|{url}".encode("utf-8")).hexdigest()[:12]
    return f"src_{task.category}_{digest}"


def discovery_summary(results: list[DiscoveryResult]) -> dict:
    urls = {hit.url for r in results for hit in r.hits}
    return {
        "tasks": len(results),
        "hits": sum(len(r.hits) for r in results),
        "unique_urls": len(urls),
        "errors": sum(1 for r in results if r.error_kind),
        "by_category": {
            cat.key: sum(1 for r in results if r.task.category == cat.key for _ in r.hits)
            for cat in categories()
        },
        "by_district": {
            d.name: sum(1 for r in results if r.task.district == d.name for _ in r.hits)
            for d in districts()
        },
    }
