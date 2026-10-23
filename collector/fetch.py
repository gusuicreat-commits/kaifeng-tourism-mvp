"""Rate-limited, robots-aware HTTP fetching with raw evidence storage.

Every response body is written under data/collection-runs/<run_id>/raw/ and
hashed with SHA-256. 401/403/429 and other blocked statuses are never retried
around: they become failure records. No captcha, login or anti-bot bypass is
attempted.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from . import ledger
from .config import DEFAULT_RUNS_DIR, fetch_policy

USER_AGENT_DEFAULT = "kaifeng-tourism-collector/1.0"


class FetchBlocked(Exception):
    """Raised when a target is disallowed by robots.txt or policy."""

    def __init__(self, message: str, kind: str = "blocked"):
        super().__init__(message)
        self.kind = kind


@dataclass
class FetchOutcome:
    url: str
    ok: bool
    status_code: int | None = None
    raw_path: Path | None = None
    sha256: str | None = None
    content_type: str | None = None
    elapsed_ms: int | None = None
    error_kind: str | None = None
    error_detail: str | None = None
    attempts: int = 0
    text: str | None = field(default=None, repr=False)


def _safe_name(url: str, suffix: str = "") -> str:
    parsed = urlparse(url)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{parsed.netloc}{parsed.path}").strip("_") or "index"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"{stem[:80]}_{digest}{suffix}"


class RateLimiter:
    """Per-domain minimum interval. Deterministic and dependency-free."""

    def __init__(self, per_domain_seconds: float, clock=time.monotonic, sleep=time.sleep):
        self.interval = float(per_domain_seconds)
        self.clock = clock
        self.sleep = sleep
        self._last: dict[str, float] = {}

    def wait(self, domain: str) -> None:
        last = self._last.get(domain)
        if last is None:
            self._last[domain] = self.clock()
            return
        elapsed = self.clock() - last
        remaining = self.interval - elapsed
        if remaining > 0:
            self.sleep(remaining)
        self._last[domain] = self.clock()


class RobotsCache:
    """Minimal robots.txt cache: allow-all unless a matching Disallow exists."""

    def __init__(self, cache_path: Path | None = None, fetcher=None):
        self.cache_path = Path(cache_path) if cache_path else None
        self.fetcher = fetcher
        self._memory: dict[str, list[str]] = {}
        if self.cache_path and self.cache_path.exists():
            try:
                self._memory = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self._memory = {}

    def _load_domain(self, domain: str) -> list[str]:
        if domain in self._memory:
            return self._memory[domain]
        rules: list[str] = []
        if self.fetcher is not None:
            robots_url = urlunparse(("https", domain, "/robots.txt", "", "", ""))
            try:
                text = self.fetcher(robots_url)
            except Exception:
                text = None
            if text:
                for line in text.splitlines():
                    stripped = line.split("#", 1)[0].strip()
                    if not stripped.lower().startswith("disallow"):
                        continue
                    value = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
                    if value:
                        rules.append(value)
        self._memory[domain] = rules
        self._save()
        return rules

    def _save(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._memory, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def allows(self, url: str) -> tuple[bool, str]:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"scheme_not_allowed:{parsed.scheme}"
        rules = self._load_domain(parsed.netloc)
        path = parsed.path or "/"
        for rule in rules:
            if rule == "/" or path.startswith(rule):
                return False, f"robots_disallow:{rule}"
        return True, ""


@dataclass
class Fetcher:
    """HTTP fetcher with policy-driven timeouts, retries, rate limits and storage."""

    run_id: str
    db: sqlite3.Connection
    runs_dir: Path = DEFAULT_RUNS_DIR
    user_agent: str | None = None
    respect_robots: bool = True
    sleep: float | None = None            # test seam; None means use configured limiter
    sleeper=time.sleep
    clock=time.monotonic

    def __post_init__(self):
        policy = fetch_policy()
        self.policy = policy
        self.user_agent = self.user_agent or policy.robots.get("user_agent", USER_AGENT_DEFAULT)
        self.respect_robots = self.respect_robots and bool(policy.robots.get("enabled", True))
        self.limiter = RateLimiter(policy.rate_limit["per_domain_seconds"], clock=self.clock, sleep=self.sleeper)
        self.robots = RobotsCache(
            Path(policy.robots["cache_path"]) if policy.robots.get("cache_path") else None,
            fetcher=self._fetch_robots,
        ) if self.respect_robots else None
        self.raw_dir = Path(self.runs_dir) / self.run_id / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._domain_last: dict[str, float] = {}

    # ------------------------------------------------------------------ internals

    def _throttle(self, url: str) -> None:
        if self.sleep is not None:
            if self.sleep > 0:
                self.sleeper(self.sleep)
            return
        self.limiter.wait(urlparse(url).netloc)

    def _headers(self) -> dict:
        return {"User-Agent": self.user_agent, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}

    def _client(self) -> httpx.Client:
        policy = self.policy
        return httpx.Client(
            timeout=httpx.Timeout(policy.timeouts["total_seconds"], connect=policy.timeouts["connect_seconds"],
                                  read=policy.timeouts["read_seconds"]),
            follow_redirects=True, headers=self._headers(),
        )

    def _fetch_robots(self, robots_url: str) -> str | None:
        try:
            with self._client() as client:
                response = client.get(robots_url)
            if response.status_code == 200:
                return response.text
        except httpx.HTTPError:
            return None
        return None

    def _store(self, url: str, content: bytes, content_type: str | None) -> tuple[Path, str]:
        suffix = ".json" if (content_type or "").startswith("application/json") else ".html"
        path = self.raw_dir / _safe_name(url, suffix)
        path.write_bytes(content)
        return path, hashlib.sha256(content).hexdigest()

    # -------------------------------------------------------------------- public

    def fetch(self, url: str, *, source_key: str | None = None, record: bool = True) -> FetchOutcome:
        policy = self.policy
        parsed = urlparse(url)
        if parsed.scheme not in policy.limits["allowed_schemes"]:
            return FetchOutcome(url=url, ok=False, error_kind="scheme_not_allowed",
                                error_detail=f"scheme={parsed.scheme}")
        if self.robots is not None:
            allowed, reason = self.robots.allows(url)
            if not allowed:
                self._record_failure(source_key, "robots_block", reason)
                return FetchOutcome(url=url, ok=False, error_kind="robots_block", error_detail=reason)

        attempts = 0
        max_attempts = int(policy.retry["max_attempts"])
        backoff = list(policy.retry["backoff_seconds"])
        # 传输层错误（TLS 握手失败、连接重置、超时）与 HTTP 5xx 分开预算：本机出口
        # 存在间歇性握手失败，多给两次退避重试；这仍不构成绕过访问控制。
        max_network_attempts = max_attempts + 2
        network_backoff = backoff + [30.0, 60.0]
        last_error: FetchOutcome | None = None
        with self._client() as client:
            while attempts < max_attempts:
                attempts += 1
                self._throttle(url)
                started = self.clock()
                try:
                    response = client.get(url)
                except httpx.HTTPError as exc:
                    last_error = FetchOutcome(url=url, ok=False, error_kind="network_error",
                                              error_detail=f"{type(exc).__name__}: {exc}"[:500], attempts=attempts)
                    if attempts < max_network_attempts:
                        self.sleeper(network_backoff[min(attempts - 1, len(network_backoff) - 1)])
                    continue
                elapsed_ms = int((self.clock() - started) * 1000)
                status = response.status_code
                content_type = response.headers.get("content-type", "")
                if status in policy.blocked_status_map:
                    kind = policy.blocked_status_map[status]
                    self._record_failure(source_key, kind, f"HTTP {status} {url}")
                    return FetchOutcome(url=url, ok=False, status_code=status, error_kind=kind,
                                        error_detail=f"HTTP {status}", attempts=attempts,
                                        content_type=content_type, elapsed_ms=elapsed_ms)
                if status in policy.retryable_status and attempts < max_attempts:
                    last_error = FetchOutcome(url=url, ok=False, status_code=status, error_kind="http_retryable",
                                              error_detail=f"HTTP {status}", attempts=attempts)
                    self.sleeper(backoff[min(attempts - 1, len(backoff) - 1)])
                    continue
                if status >= 400:
                    self._record_failure(source_key, "http_error", f"HTTP {status} {url}")
                    return FetchOutcome(url=url, ok=False, status_code=status, error_kind="http_error",
                                        error_detail=f"HTTP {status}", attempts=attempts,
                                        content_type=content_type, elapsed_ms=elapsed_ms)
                if len(response.content) > int(policy.limits["max_bytes_per_response"]):
                    self._record_failure(source_key, "too_large", f"{len(response.content)} bytes {url}")
                    return FetchOutcome(url=url, ok=False, status_code=status, error_kind="too_large",
                                        error_detail=f"{len(response.content)} bytes", attempts=attempts)
                path, digest = self._store(url, response.content, content_type)
                if record:
                    ledger.upsert_source(self.db, source_key=source_key or _source_key_for(url), name=url,
                                         source_type="other", url=url, access_kind="platform",
                                         access_status="reachable", content_sha256=digest, raw_path=str(path),
                                         license_status="unknown", notes="collector/fetch auto-registration")
                return FetchOutcome(url=url, ok=True, status_code=status, raw_path=path, sha256=digest,
                                    content_type=content_type, elapsed_ms=elapsed_ms, attempts=attempts,
                                    text=response.text)
        if last_error is not None:
            self._record_failure(source_key, last_error.error_kind or "network_error",
                                 last_error.error_detail or "")
        return last_error or FetchOutcome(url=url, ok=False, error_kind="unknown", attempts=attempts)

    def _record_failure(self, source_key: str | None, kind: str, detail: str) -> None:
        if self.db is None:
            return
        ledger.record_failure(self.db, run_id=self.run_id, source_key=source_key,
                              failure_kind=kind, detail=detail)


def _source_key_for(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"src_url_{digest}"


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
