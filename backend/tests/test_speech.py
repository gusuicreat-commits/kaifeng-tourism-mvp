import base64
import io
import json
import wave

import httpx
import pytest
from app import speech


def wav(seconds=1, rate=16000, channels=1):
    data = io.BytesIO()
    with wave.open(data, 'wb') as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\0\0' * int(seconds * rate) * channels)
    return data.getvalue()


@pytest.fixture(autouse=True)
def isolated_speech_config(monkeypatch):
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    monkeypatch.delenv('DASHSCOPE_BASE_URL', raising=False)


def provider(monkeypatch, handler):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-private-key')
    original = httpx.AsyncClient
    monkeypatch.setattr(speech.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def send(client, content=None):
    return client.post('/api/speech/transcriptions', files={'file': ('recording.wav', wav() if content is None else content, 'audio/wav')})


def test_missing_key_is_explicit_and_status_never_exposes_secrets(client, monkeypatch):
    assert client.get('/api/speech/status').json() == {'available': False, 'max_seconds': 60}
    assert send(client).status_code == 503
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'private-test-key')
    response = client.get('/api/speech/status')
    assert response.json()['available'] is True
    assert 'private-test-key' not in response.text


@pytest.mark.parametrize('body', [
    {'output': {'text': '  我想去清明上河园。  '}},
    {'output': {'output': {'sentence': {'text': '我想去清明上河园。'}}}},
])
def test_real_request_contract_and_documented_response_shapes(client, monkeypatch, body):
    audio = wav()
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
        assert request.headers['Authorization'] == 'Bearer test-private-key'
        assert request.headers['X-DashScope-SSE'] == 'disable'
        payload = json.loads(request.content)
        assert payload['model'] == 'qwen-audio-3.0-asr-flash'
        data = payload['input']['messages'][-1]['content'][0]['input_audio']['data']
        assert data.startswith('data:audio/wav;base64,')
        assert base64.b64decode(data.split(',')[1]) == audio
        assert payload['parameters']['vocabulary']['清明上河园'] == 3
        assert payload['parameters']['sample_rate'] == '16000'
        return httpx.Response(200, json=body)
    provider(monkeypatch, handler)
    response = send(client, audio)
    assert response.status_code == 200
    assert response.json() == {'text': '我想去清明上河园。'}
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1


@pytest.mark.parametrize(('audio', 'expected'), [
    (b'', 415), (b'not-a-wav', 415), (wav(.1), 422),
    (wav(60.1), 422), (wav(1, 48000), 415), (wav(1, channels=2), 415),
    (wav()[:-10], 422), (b'x' * (speech.MAX_BYTES + 1), 413),
])
def test_invalid_recordings_never_reach_paid_provider(client, monkeypatch, audio, expected):
    def handler(_):
        pytest.fail('Invalid input must not incur a provider call')
    provider(monkeypatch, handler)
    assert send(client, audio).status_code == expected


@pytest.mark.parametrize(('status', 'expected'), [(401, 503), (403, 503), (429, 429), (500, 502)])
def test_upstream_errors_are_sanitized_and_not_retried(client, monkeypatch, status, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={'message': 'internal test-private-key details'})
    provider(monkeypatch, handler)
    response = send(client)
    assert response.status_code == expected
    assert 'test-private-key' not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize('body', [{}, {'output': []}, {'output': {'text': 123}}, {'code': 'InvalidApiKey'}, {'output': {'text': ''}}])
def test_unusable_provider_results_are_not_success(client, monkeypatch, body):
    provider(monkeypatch, lambda _: httpx.Response(200, json=body))
    assert send(client).status_code == (422 if body == {'output': {'text': ''}} else 502)


def test_timeout_is_actionable(client, monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout('private provider details', request=request)
    provider(monkeypatch, handler)
    response = send(client)
    assert response.status_code == 504
    assert '超时' in response.json()['detail']
    assert 'private' not in response.text


def test_untrusted_endpoint_is_rejected_before_sending_key(client, monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'private-key')
    monkeypatch.setenv('DASHSCOPE_BASE_URL', 'https://example.com/api/v1')
    assert send(client).status_code == 503
