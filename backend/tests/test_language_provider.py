"""Provider compatibility and fail-closed parsing; never calls a paid endpoint."""
import json

import httpx
import pytest
from fastapi import HTTPException

from app import language


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv('TOURISM_LLM_BASE_URL', 'https://provider.invalid/v1')
    monkeypatch.setenv('TOURISM_LLM_API_KEY', 'test-only-not-real')
    monkeypatch.setenv('TOURISM_LLM_MODEL', 'kimi-k2.6')
    real_client = httpx.Client

    def install(handler):
        monkeypatch.setattr(language.httpx, 'Client', lambda **kwargs: real_client(
            transport=httpx.MockTransport(handler), **kwargs))
    return install


@pytest.mark.parametrize('model', ['kimi-k2.6', 'kimi-k3', 'kimi-k2.7-code', 'qwen3.7-plus', 'qwen3.7-plus-2026-05-26', 'other-model'])
def test_provider_parameters_and_validated_json(provider, monkeypatch, model):
    monkeypatch.setenv('TOURISM_LLM_MODEL', model)
    def handle(request):
        assert str(request.url) == 'https://provider.invalid/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-only-not-real'
        body = json.loads(request.content)
        assert body['response_format'] == {'type': 'json_object'}
        assert body['max_tokens'] == 700
        if model.startswith('qwen3.7-plus'):
            assert body['enable_thinking'] is False and body['temperature'] == 0
            assert 'thinking' not in body and 'reasoning_effort' not in body
        elif model == 'kimi-k2.6':
            assert body['thinking'] == {'type': 'disabled'}
            assert 'temperature' not in body
        elif model.startswith('kimi-'):
            assert 'temperature' not in body and 'thinking' not in body
            if model == 'kimi-k3': assert body['reasoning_effort'] == 'low'
        else:
            assert body['temperature'] == 0
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': '{"duration_minutes":120}'}}]})
    provider(handle)
    assert language._call('Return JSON only.', {'text': '两小时'}) == {'duration_minutes': 120}


@pytest.mark.parametrize('failure', ['timeout', 'auth', 'quota', 'truncated', 'tool_call',
                                      'invalid_json', 'array', 'null', 'oversized'])
def test_provider_failure_returns_safe_error(provider, failure):
    def handle(request):
        if failure == 'timeout': raise httpx.ReadTimeout('private upstream detail', request=request)
        if failure in ('auth', 'quota'):
            return httpx.Response(401 if failure == 'auth' else 429,
                                  json={'error': {'message': 'private upstream detail'}})
        content = {'invalid_json': '{', 'array': '[]', 'null': None,
                   'oversized': 'x' * 12001}.get(failure, '{"duration_minutes":120}')
        reason = {'truncated': 'length', 'tool_call': 'tool_calls'}.get(failure, 'stop')
        return httpx.Response(200, json={'choices': [{'finish_reason': reason,
                                                     'message': {'content': content}}]})
    provider(handle)
    with pytest.raises(HTTPException) as caught:
        language._call('Return JSON only.', {'text': '两小时'})
    assert caught.value.status_code == 503
    assert 'private upstream detail' not in caught.value.detail
    assert 'test-only-not-real' not in caught.value.detail


def test_missing_configuration_never_calls_network(monkeypatch):
    monkeypatch.setenv('TOURISM_LLM_API_KEY', '')
    monkeypatch.setattr(language.httpx, 'Client', lambda **_: pytest.fail('network attempted'))
    with pytest.raises(HTTPException) as caught:
        language._call('Return JSON only.', {})
    assert caught.value.status_code == 503
