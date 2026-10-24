"""Single-purpose public HTML provider; no redirects, retries or private APIs."""
from __future__ import annotations

import threading
import time
from datetime import datetime

import httpx

from .models import SyncSettings
from .store import TZ


class FetchRejected(RuntimeError):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


class QmsyunProvider:
    def __init__(self, settings: SyncSettings, transport=None):
        self.settings = settings
        self.transport = transport
        self._lock = threading.Lock()
        self._last_attempt = 0.0
        self._blocked = False

    def fetch(self, now: datetime | None = None) -> dict:
        if self._blocked:
            raise FetchRejected("provider_stopped_after_403_or_429")
        with self._lock:
            remaining = self.settings.minimum_interval_minutes * 60 - (time.monotonic() - self._last_attempt)
            if self._last_attempt and remaining > 0:
                raise FetchRejected("minimum_request_interval", 429)
            self._last_attempt = time.monotonic()
        try:
            with httpx.Client(transport=self.transport, follow_redirects=False,
                              timeout=httpx.Timeout(self.settings.read_timeout_seconds, connect=self.settings.connect_timeout_seconds),
                              headers={"User-Agent": "BianliangScenicSync/1.0 public-schedule-check",
                                       "Accept": "text/html,application/xhtml+xml"}) as client:
                response = client.get(self.settings.source_url)
        except httpx.HTTPError as error:
            raise FetchRejected("network_error") from error
        if response.status_code in (403, 429):
            self._blocked = True
            raise FetchRejected(f"http_{response.status_code}_stop", response.status_code)
        if response.status_code != 200:
            raise FetchRejected(f"http_{response.status_code}", response.status_code)
        if "html" not in response.headers.get("content-type", "").lower():
            raise FetchRejected("unexpected_content_type", response.status_code)
        if len(response.content) > self.settings.maximum_bytes:
            raise FetchRejected("response_too_large", response.status_code)
        return {"raw": response.content, "http_status": response.status_code,
                "content_type": response.headers["content-type"], "fetched_at": now or datetime.now(TZ)}

