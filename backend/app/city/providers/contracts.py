"""Stable contracts between the workflow and external data providers.

Provider adapters may use any SDK or CLI internally. The workflow only sees
these small, serialisable values and never receives provider-specific JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol


class ProviderStatus(StrEnum):
    ENABLED = 'enabled'
    DISABLED = 'disabled'
    UNAUTHORIZED = 'unauthorized'
    UNAVAILABLE = 'unavailable'
    RATE_LIMITED = 'rate_limited'
    DEGRADED = 'degraded'
    STALE = 'stale'


@dataclass(frozen=True)
class CapabilityRequest:
    capability: str
    params: Mapping[str, Any]
    request_id: str | None = None
    requirements_version: int | None = None
    deadline_monotonic: float | None = None
    requested_scope: str | None = None


@dataclass
class ProviderResult:
    """Normalised result; ``data`` is a capability-owned model/dict."""

    status: str
    data: dict[str, Any] = field(default_factory=dict)
    provider_id: str | None = None
    capability: str = ''
    source: str | None = None
    checked_at: str | None = None
    valid_until: str | None = None
    scope: str | None = None
    evidence_status: str = 'unknown'
    limitations: list[str] = field(default_factory=list)
    error_code: str | None = None
    elapsed_ms: float | None = None
    http_attempts: int | None = None
    cache_hit: bool = False
    transport_basis: str = 'not_reported'

    @property
    def usable(self) -> bool:
        return self.status in ('ok', 'found', 'success', 'empty')

    def public(self) -> dict[str, Any]:
        """Return the trace-safe envelope used by API responses."""
        return {
            'status': self.status,
            'provider_id': self.provider_id,
            'capability': self.capability,
            'source': self.source,
            'checked_at': self.checked_at,
            'valid_until': self.valid_until,
            'scope': self.scope,
            'evidence_status': self.evidence_status,
            'limitations': list(self.limitations),
            'error_code': self.error_code,
        }


class ProviderAdapter(Protocol):
    provider_id: str
    capabilities: frozenset[str]
    priority: int

    def availability(self, capability: str) -> ProviderStatus: ...

    def execute(self, request: CapabilityRequest) -> ProviderResult: ...


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()
