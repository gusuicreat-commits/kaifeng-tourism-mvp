"""Fetch policy: rate limiting, robots, retries, blocked statuses, hashing.

Every test uses a stub transport; no test performs real network I/O.
"""
import json
from pathlib import Path

import httpx
import pytest

from collector import fetch as fetch_mod
from collector import ledger


class StubTransport(httpx.BaseTransport):
    """Scripted responses keyed by URL substring."""

    def __init__(self, script):
        self.script = script
        self.requests = []

    def handle_request(self, request):
        self.requests.append(request)
        for needle, response in self.script.items():
            if needle in str(request.url):
                if isinstance(response, Exception):
                    raise response
                status, body, headers = response
                return httpx.Response(status, content=body, headers=headers,
                                      request=request)
        return httpx.Response(404, content=b"not found", request=request)


def make_fetcher(db, runs_dir, run_id="run_test", transport=None, **kwargs):
    fetcher = fetch_mod.Fetcher(run_id=run_id, db=db, runs_dir=runs_dir, sleep=0, **kwargs)
    if transport is not None:
        stub = StubTransport(transport)
        original = fetcher._client

        def client():
            c = original()
            c._transport = stub
            return c
        fetcher._client = client
        fetcher._stub = stub
    return fetcher


def test_success_stores_raw_and_records_sha256(db, runs_dir, tmp_path):
    body = "<html>opening 9:00-17:00</html>".encode("utf-8")
    fetcher = make_fetcher(db, runs_dir, transport={"example.gov.cn": (200, body, {"content-type": "text/html"})})
    out = fetcher.fetch("https://example.gov.cn/page", source_key="src_a")
    assert out.ok and out.status_code == 200
    assert out.raw_path.exists()
    assert out.sha256 == fetch_mod.sha256_bytes(body)
    assert fetcher.raw_dir.joinpath(out.raw_path.name).read_bytes() == body


def test_403_is_recorded_as_waf_block_and_not_retried_around(db, runs_dir):
    transport = {"example.gov.cn": (403, b"forbidden", {})}
    fetcher = make_fetcher(db, runs_dir, transport=transport)
    out = fetcher.fetch("https://example.gov.cn/secret", source_key="src_blocked")
    assert not out.ok
    assert out.error_kind == "waf_block"
    assert out.attempts == 1
    assert len(fetcher._stub.requests) == 1  # 绝不重试绕过
    failures = ledger.list_failures(db)
    assert failures and failures[0]["failure_kind"] == "waf_block"


def test_429_is_rate_limited_and_not_retried_around(db, runs_dir):
    fetcher = make_fetcher(db, runs_dir, transport={"example.gov.cn": (429, b"slow down", {})})
    out = fetcher.fetch("https://example.gov.cn/x", source_key="src_rl")
    assert out.error_kind == "rate_limited"
    assert len(fetcher._stub.requests) == 1


def test_401_records_auth_required(db, runs_dir):
    fetcher = make_fetcher(db, runs_dir, transport={"example.gov.cn": (401, b"auth", {})})
    out = fetcher.fetch("https://example.gov.cn/private", source_key="src_auth")
    assert out.error_kind == "auth_required"
    assert ledger.list_failures(db)[0]["failure_kind"] == "auth_required"


def test_retryable_status_is_retried_up_to_policy(db, runs_dir):
    calls = {"n": 0}

    class Flaky(httpx.BaseTransport):
        def handle_request(self, request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503, content=b"later", request=request)
            return httpx.Response(200, content=b"ok", request=request)

    fetcher = make_fetcher(db, runs_dir)
    fetcher._client = lambda: httpx.Client(transport=Flaky(), timeout=5)
    fetcher.sleeper = lambda s: None
    out = fetcher.fetch("https://example.gov.cn/flaky", source_key="src_f")
    assert out.ok and calls["n"] == 3


def test_network_errors_are_retried_then_recorded(db, runs_dir):
    transport = {"example.gov.cn": httpx.ConnectError("tls reset")}
    fetcher = make_fetcher(db, runs_dir, transport=transport)
    fetcher.sleeper = lambda s: None
    out = fetcher.fetch("https://example.gov.cn/down", source_key="src_down")
    assert not out.ok and out.error_kind == "network_error"
    assert out.attempts > 1
    assert any(f["failure_kind"] == "network_error" for f in ledger.list_failures(db))


def test_robots_disallow_blocks_before_request(db, runs_dir, tmp_path):
    cache = tmp_path / "robots.json"
    robots = fetch_mod.RobotsCache(cache, fetcher=lambda url: "User-agent: *\nDisallow: /private\n")
    fetcher = make_fetcher(db, runs_dir, transport={"example.gov.cn": (200, b"x", {})})
    fetcher.robots = robots
    out = fetcher.fetch("https://example.gov.cn/private/page", source_key="src_r")
    assert not out.ok and out.error_kind == "robots_block"
    assert fetcher._stub.requests == []
    assert ledger.list_failures(db)[0]["failure_kind"] == "robots_block"


def test_robots_allow_passes(db, runs_dir, tmp_path):
    robots = fetch_mod.RobotsCache(tmp_path / "robots.json",
                                   fetcher=lambda url: "User-agent: *\nDisallow: /private\n")
    fetcher = make_fetcher(db, runs_dir, transport={"example.gov.cn": (200, b"ok", {})})
    fetcher.robots = robots
    out = fetcher.fetch("https://example.gov.cn/public", source_key="src_p")
    assert out.ok


def test_robots_cache_round_trips(tmp_path):
    cache = tmp_path / "robots.json"
    robots = fetch_mod.RobotsCache(cache, fetcher=lambda url: "Disallow: /a\n")
    assert robots.allows("https://x.test/a/b")[0] is False
    reloaded = fetch_mod.RobotsCache(cache, fetcher=None)
    assert reloaded.allows("https://x.test/a/b")[0] is False
    assert reloaded.allows("https://x.test/b")[0] is True


def test_non_http_scheme_rejected(db, runs_dir):
    fetcher = make_fetcher(db, runs_dir)
    out = fetcher.fetch("ftp://example.gov.cn/file", source_key="src_ftp")
    assert not out.ok and out.error_kind == "scheme_not_allowed"


def test_oversized_response_is_refused(db, runs_dir):
    from collector.config import FetchPolicy
    body = b"x" * 100
    fetcher = make_fetcher(db, runs_dir, transport={"example.gov.cn": (200, body, {})})
    # 直接替换策略对象，避免触碰共享配置缓存
    fetcher.policy = FetchPolicy(fetcher.policy.timeouts, fetcher.policy.rate_limit,
                                 fetcher.policy.retry, fetcher.policy.robots,
                                 {**fetcher.policy.limits, "max_bytes_per_response": 10},
                                 fetcher.policy.blocked_status, fetcher.policy.storage)
    out = fetcher.fetch("https://example.gov.cn/big", source_key="src_big")
    assert not out.ok and out.error_kind == "too_large"
    assert ledger.list_failures(db)[0]["failure_kind"] == "too_large"


def test_rate_limiter_waits_between_domain_calls():
    slept = []
    # 首次只读一次时钟；后续每次 wait 读两次（判定 + 记录）
    ticks = iter([0.0, 0.5, 0.5, 3.0, 3.0, 3.2, 3.2])
    limiter = fetch_mod.RateLimiter(2.0, clock=lambda: next(ticks), sleep=slept.append)
    limiter.wait("a.test")
    limiter.wait("a.test")  # 距上次 0.5s -> 需再等 1.5s
    limiter.wait("a.test")  # 距上次 3.0s -> 无需等待
    limiter.wait("a.test")  # 距上次 3.2s -> 需再等 1.8s
    assert slept == [pytest.approx(1.5), pytest.approx(1.8)]


def test_rate_limiter_is_per_domain():
    slept = []
    ticks = iter([0.0, 0.1])
    limiter = fetch_mod.RateLimiter(2.0, clock=lambda: next(ticks), sleep=slept.append)
    limiter.wait("a.test")
    limiter.wait("b.test")  # 不同域名互不等待
    assert slept == []


def test_safe_name_is_stable_and_bounded():
    name = fetch_mod._safe_name("https://www.kaifeng.gov.cn/a/b/c.html?x=1", ".html")
    assert name.endswith(".html")
    assert len(name) < 120
    assert fetch_mod._safe_name("https://a.test/") == fetch_mod._safe_name("https://a.test/")


def test_source_key_for_url_is_deterministic():
    assert fetch_mod._source_key_for("https://a.test/x") == fetch_mod._source_key_for("https://a.test/x")
    assert fetch_mod._source_key_for("https://a.test/x") != fetch_mod._source_key_for("https://a.test/y")
