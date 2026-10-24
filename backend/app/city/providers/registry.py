"""Small in-process Provider Registry used by the city workflow."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

from .contracts import CapabilityRequest, ProviderAdapter, ProviderResult, ProviderStatus


@dataclass
class ProviderExecution:
    result: ProviderResult
    provider_trace: list[dict] = field(default_factory=list)


class ProviderRegistry:
    def __init__(self, adapters: Iterable[ProviderAdapter] = ()):
        self._adapters: dict[str, ProviderAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ProviderAdapter) -> None:
        if not adapter.provider_id or not adapter.capabilities:
            raise ValueError('provider_id and capabilities are required')
        if adapter.provider_id in self._adapters:
            raise ValueError(f'duplicate provider: {adapter.provider_id}')
        self._adapters[adapter.provider_id] = adapter

    def providers(self, capability: str) -> list[ProviderAdapter]:
        return sorted(
            (a for a in self._adapters.values() if capability in a.capabilities),
            key=lambda a: (a.priority, a.provider_id),
        )

    def capabilities(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for adapter in self._adapters.values():
            for capability in adapter.capabilities:
                result.setdefault(capability, []).append(adapter.provider_id)
        return {key: sorted(value) for key, value in sorted(result.items())}

    def statuses(self) -> dict[str, list[dict[str, str]]]:
        """Expose configuration health without exposing credentials."""
        result: dict[str, list[dict[str, str]]] = {}
        for adapter in self._adapters.values():
            for capability in sorted(adapter.capabilities):
                result.setdefault(capability, []).append({
                    'provider_id': adapter.provider_id,
                    'status': adapter.availability(capability).value,
                })
        return {key: sorted(value, key=lambda row: row['provider_id']) for key, value in sorted(result.items())}

    def execute(self, request: CapabilityRequest) -> ProviderExecution:
        trace: list[dict] = []
        candidates = self.providers(request.capability)
        if not candidates:
            return ProviderExecution(
                ProviderResult(
                    status='provider_unavailable', capability=request.capability,
                    evidence_status='unknown', error_code='NO_PROVIDER_REGISTERED',
                ), trace,
            )

        last_result: ProviderResult | None = None
        for index, adapter in enumerate(candidates):
            available = adapter.availability(request.capability)
            if available is not ProviderStatus.ENABLED:
                trace.append({
                    'capability': request.capability,
                    'provider_id': adapter.provider_id,
                    'provider_status': available.value,
                    'attempted': False,'http_attempts':0,'cache_hit':False,'transport_basis':'blocked_before_adapter',
                    'fallback_reason': 'provider_not_eligible',
                })
                continue
            started = time.monotonic()
            result = adapter.execute(request)
            result.provider_id = result.provider_id or adapter.provider_id
            result.capability = result.capability or request.capability
            result.elapsed_ms = result.elapsed_ms if result.elapsed_ms is not None else round((time.monotonic() - started) * 1000, 2)
            last_result = result
            trace.append({
                'capability': request.capability,
                'provider_id': adapter.provider_id,
                'provider_status': result.status,
                'attempted': True,'http_attempts':result.http_attempts,'cache_hit':result.cache_hit,
                'transport_basis':result.transport_basis,'candidate_count':len(result.data.get('items',[])),
                'elapsed_ms': result.elapsed_ms,
                'error_code': result.error_code,
                'fallback_reason': None if result.usable else ('try_next_provider' if index + 1 < len(candidates) else 'no_eligible_fallback'),
                'evidence_scope': result.scope,
            })
            if result.usable:
                return ProviderExecution(result, trace)

        # Preserve the adapter's typed failure (timeout, rate limit, invalid
        # response, ...). Collapsing it to a generic unavailable status would
        # break the workflow's retry and user explanation policy.
        result = last_result or ProviderResult(
            status='provider_unavailable', capability=request.capability,
            evidence_status='unknown', error_code='ALL_PROVIDERS_FAILED',
        )
        return ProviderExecution(result, trace)


def default_registry() -> ProviderRegistry:
    from .adapters import AmapPoiAdapter, FlyAIHotelAdapter, FutureWeatherAdapter

    return ProviderRegistry((
        FlyAIHotelAdapter(),
        AmapPoiAdapter(),
        FutureWeatherAdapter(),
    ))
