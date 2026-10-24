"""Provider registry contract tests use only deterministic fake adapters."""
from app.city.providers.contracts import CapabilityRequest, ProviderResult, ProviderStatus
from app.city.providers.registry import ProviderRegistry


class FakeProvider:
    capabilities = frozenset({'search_hotels'})

    def __init__(self, provider_id, priority, status=ProviderStatus.ENABLED, result_status='ok'):
        self.provider_id = provider_id
        self.priority = priority
        self.status = status
        self.result_status = result_status
        self.calls = 0

    def availability(self, capability):
        return self.status

    def execute(self, request):
        self.calls += 1
        return ProviderResult(
            status=self.result_status,
            data={'items': []} if self.result_status != 'ok' else {'items': [{'id': self.provider_id}]},
            provider_id=self.provider_id,
            capability=request.capability,
            evidence_status='provider_candidate' if self.result_status == 'ok' else 'unknown',
        )


def request():
    return CapabilityRequest(capability='search_hotels', params={'check_in': '2030-01-01', 'check_out': '2030-01-02'})


def test_registry_prefers_priority_and_keeps_trace():
    first = FakeProvider('first', priority=10)
    second = FakeProvider('second', priority=20)
    execution = ProviderRegistry((second, first)).execute(request())
    assert execution.result.data['items'][0]['id'] == 'first'
    assert first.calls == 1 and second.calls == 0
    assert execution.provider_trace[0]['provider_id'] == 'first'
    assert execution.provider_trace[0]['attempted'] is True


def test_registry_falls_back_only_after_typed_failure():
    failed = FakeProvider('failed', priority=10, result_status='timeout')
    backup = FakeProvider('backup', priority=20)
    execution = ProviderRegistry((failed, backup)).execute(request())
    assert execution.result.provider_id == 'backup'
    assert failed.calls == backup.calls == 1
    assert execution.provider_trace[0]['fallback_reason'] == 'try_next_provider'


def test_registry_skips_unauthorized_provider_without_calling_it():
    unauthorized = FakeProvider('unauthorized', priority=10, status=ProviderStatus.UNAUTHORIZED)
    execution = ProviderRegistry((unauthorized,)).execute(request())
    assert execution.result.status == 'provider_unavailable'
    assert unauthorized.calls == 0
    assert execution.provider_trace == [{
        'capability': 'search_hotels', 'provider_id': 'unauthorized',
        'provider_status': 'unauthorized', 'attempted': False,
        'http_attempts':0,'cache_hit':False,'transport_basis':'blocked_before_adapter',
        'fallback_reason': 'provider_not_eligible',
    }]
