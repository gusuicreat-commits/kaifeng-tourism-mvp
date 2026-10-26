"""Synthetic QWeather responses only; no live provider or real-weather assertion."""
from datetime import datetime

import httpx
import pytest

from app.city import weather
from app.city.agent import service
from app.db import connect
from test_city_agent import run, snapshot
from test_city_assistant import city, get, path, rid, walking

HOST = 'https://synthetic.re.qweatherapi.com'
META = {'tag': '0' * 64, 'attributions': ['https://developer.qweather.com/attribution.html']}


def period(condition='晴', probability=.1, amount=0, temperature=23):
    row = {'condition': {'text': condition, 'code': '100'},
            'temperature': {'value': temperature, 'unit': '°C'},
            'feelsLike': {'value': temperature, 'unit': '°C'},
            'wind': {'direction': {'degree': 0, 'compass': 'n'}, 'speed': {'value': 2, 'unit': 'm/s'}},
            'precipitation': {'amount': {'value': amount, 'unit': 'mm'}, 'probability': probability, 'type': 'rain' if probability else 'none'}}
    if temperature is None:
        row.pop('temperature'); row.pop('feelsLike')
    return row


def hourly_rows():
    result = []
    for hour in range(24):
        rain = hour in (11, 12, 13, 14, 15, 16)
        result.append({'forecastTime': f'2030-05-01T{hour:02d}:00:00+08:00',
                       **period('小雨' if rain else '晴', .8 if rain else .1, 1.2 if rain else 0)})
    return result


def daily_rows():
    result = []
    for day in range(1, 8):
        daytime = {'forecastStartTime': f'2030-05-{day:02d}T07:00:00+08:00',
                   'forecastEndTime': f'2030-05-{day:02d}T19:00:00+08:00', **period(temperature=None)}
        nighttime = {'forecastStartTime': f'2030-05-{day:02d}T19:00:00+08:00',
                     'forecastEndTime': f'2030-05-{day + 1:02d}T07:00:00+08:00', **period(temperature=None)}
        result.append({'forecastStartTime': f'2030-05-{day:02d}T00:00:00+08:00',
                       'forecastEndTime': f'2030-05-{day + 1:02d}T00:00:00+08:00',
                       'temperatureMin': {'value': 15, 'unit': '°C'},
                       'temperatureMax': {'value': 26, 'unit': '°C'},
                       'daytime': daytime, 'nighttime': nighttime})
    return result


@pytest.fixture
def qweather(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['X-QW-Api-Key'] == 'synthetic-secret'
        assert 'key=' not in str(request.url)
        if '/current/' in request.url.path:
            return httpx.Response(200, json={'metadata': META, **period()})
        if '/hourly/' in request.url.path:
            assert request.url.params['hours'] == '72'
            return httpx.Response(200, json={'metadata': META, 'hours': hourly_rows()})
        assert request.url.params['days'] == '7'
        return httpx.Response(200, json={'metadata': META, 'days': daily_rows()})
    ticks = [0]
    provider = weather.QWeatherProvider('synthetic-secret', HOST,
        transport=httpx.MockTransport(handler), clock=lambda: ticks[0],
        moment=lambda: datetime.fromisoformat('2030-05-01T09:40:00+08:00'))
    monkeypatch.setattr(weather.QWeatherProvider, 'from_environment', classmethod(lambda cls, **kwargs: provider))
    monkeypatch.setattr(weather, 'now', lambda: datetime.fromisoformat('2030-05-01T09:40:00+08:00'))
    monkeypatch.setenv('QWEATHER_ENABLED', '1')
    weather._CACHE.clear()
    yield {'provider': provider, 'calls': calls, 'ticks': ticks}
    weather._CACHE.clear()
    provider.client.close()


def test_provider_parses_official_v1_shape_and_caches(qweather):
    p = qweather['provider']
    current = p.current()
    assert current.status == 'success'
    assert current.data['source'] == 'QWeather'
    assert current.data['evidence_status'] == 'provider_estimate'
    assert current.data['precipitation_probability_percent'] == 10
    assert p.current().data == current.data
    assert len(qweather['calls']) == 1
    qweather['ticks'][0] = 1801
    p.current()
    assert len(qweather['calls']) == 2
    hourly = p.hourly()
    assert len(hourly.data['hours']) == 24
    assert hourly.data['hours'][11]['precipitation_mm'] == 1.2
    assert p.daily().data['days'][0]['periods'][0]['period'] == 'daytime'
    assert (p.daily().data['days'][0]['temperature_min_c'], p.daily().data['days'][0]['temperature_max_c']) == (15, 26)


@pytest.mark.parametrize('failure', ['timeout', 'invalid', 'unauthorized'])
def test_provider_failure_is_bounded_redacted_and_not_retried(qweather, failure, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('synthetic timeout')
        if failure == 'unauthorized':
            return httpx.Response(401, text='synthetic-secret')
        return httpx.Response(200, json={'metadata': META, **period(), 'temperature': {'value': 'synthetic-secret', 'unit': '°C'}})
    p = weather.QWeatherProvider('synthetic-secret', HOST, transport=httpx.MockTransport(handler))
    result = p.current()
    assert result.status == ('timeout_or_network' if failure == 'timeout' else
                             'authorization_failed' if failure == 'unauthorized' else 'invalid_response')
    assert len(calls) == 1
    assert 'synthetic-secret' not in repr(p.events)
    assert 'synthetic-secret' not in repr(result)
    p.client.close()


def test_provider_rejects_non_account_host_and_bad_units():
    with pytest.raises(ValueError):
        weather.QWeatherProvider('key', 'https://dev.qweather.com')
    def handler(request):
        return httpx.Response(200, json={'metadata': META, **period()} | {'temperature': {'value': 23, 'unit': 'F'}})
    p = weather.QWeatherProvider('synthetic-secret', HOST, transport=httpx.MockTransport(handler))
    assert p.current().status == 'invalid_response'
    p.client.close()


def test_weather_query_matches_remaining_itinerary_without_state_changes(city, qweather, monkeypatch):
    walking(city)
    before = get(city)
    from app.city.agent import provider
    monkeypatch.setattr(provider, 'generate', lambda *args, **kwargs: pytest.fail('explicit weather query needs no model'))
    result = run(city, '我的行程会受下雨天气影响吗？')
    assert result['status'] == 'answered', result
    assert result['mode'] == 'weather'
    assert result['result']['weather']['impacts']
    assert result['result']['weather']['adjustment_offered'] is True
    assert '不代表景区或演出取消' in result['result']['reply']
    assert result['result']['weather']['formal_state_changed'] is False
    assert 'confirmation' not in result['result']
    assert get(city) == before
    assert [event['tool'] for event in result['events']] == ['understand', 'query_weather']


def test_weather_endpoint_reuses_forecast_cache(city, qweather):
    walking(city)
    response = city['client'].get(path(city, '/weather'))
    assert response.status_code == 200
    assert response.json()['weather']['impacts']
    response = city['client'].get(path(city, '/weather'))
    assert response.status_code == 200
    assert len(qweather['calls']) == 3
    assert all(event['status'] == 'success' for event in qweather['provider'].events)


def test_consent_rechecks_weather_then_creates_only_existing_engine_preview(city, qweather):
    walking(city)
    before = get(city)
    result = run(city, '可以，按天气调整行程')
    assert result['status'] == 'needs_confirmation', result
    assert result['mode'] == 'planning'
    assert result['result']['weather_recheck'] == 'queried_before_preview'
    assert result['result']['weather']['impacts']
    assert result['result']['confirmation']['target_kind'] == 'route'
    assert get(city) == before
    assert [event['tool'] for event in result['events']] == ['understand', 'planning_workflow']


def test_bare_yes_does_not_authorize_weather_or_formal_changes(city, qweather):
    walking(city)
    before = get(city)
    result = run(city, '好的')
    assert result['status'] != 'needs_confirmation'
    assert get(city) == before
    assert not qweather['calls']


def test_no_forecast_coverage_is_unknown_not_clear_weather(city, qweather):
    walking(city)
    snap, _ = snapshot(city)
    original = next(a for a in snap.envelope.official_plan['activities'] if a['progress'] == 'pending')
    snap.envelope.official_plan['activities'] = [original | {
        'planned': {'starts_at': '2030-05-10T14:00:00+08:00', 'departs_at': '2030-05-10T16:00:00+08:00'}}]
    result = weather.answer(snap)
    assert result['status'] == 'answered'
    assert result['weather']['impacts'] == []
    assert result['weather']['coverage_unknown'][0]['reason'] == 'forecast_coverage_incomplete'
    assert '判断不了' in result['reply']
    assert result['weather']['adjustment_offered'] is False


def test_disabled_or_invalid_configuration_fails_without_credentials(city, monkeypatch):
    walking(city)
    monkeypatch.setenv('QWEATHER_ENABLED', '0')
    result = run(city, '开封现在天气怎么样？')
    assert result['status'] == 'unavailable'
    assert result['result']['code'] == 'WEATHER_DISABLED'
    assert result['result']['weather'] is None
    assert 'route_version' not in result['result']


def test_r1_model_weather_intent_reaches_same_read_only_provider(city, qweather, monkeypatch):
    from app import language
    from app.city.agent import provider
    walking(city)
    before = get(city)
    # Deliberately absent from the fallback keyword list: semantic routing owns it.
    text = '小汴梁，今天出门要带伞吗？'
    calls = []
    monkeypatch.setattr(language, 'available', lambda: True)
    def semantic(system, payload, **kwargs):
        calls.append(payload['text'])
        return {'actions': [{'kind': 'query', 'quote': text}], 'weather_query': {'quote': text}}, {'source': 'mock_model'}
    monkeypatch.setattr(provider, 'generate', semantic)
    result = run(city, text)
    assert calls == [text] and result['metrics']['model_calls'] == 1
    assert result['mode'] == 'weather' and result['status'] == 'answered'
    assert result['result']['weather']['impacts'] and len(qweather['calls']) == 3
    assert get(city) == before


def test_r1_invented_weather_quote_never_queries_provider(city, qweather, monkeypatch):
    from app import language
    from app.city.agent import provider
    monkeypatch.setattr(language, 'available', lambda: True)
    monkeypatch.setattr(provider, 'generate', lambda *args, **kwargs: (
        {'weather_query': {'quote': '本轮不存在的天气原话'}}, {'source': 'mock_model'}))
    result = run(city, '你好')
    assert result['status'] == 'needs_clarification' and result['metrics']['tools'] == 0
    assert not qweather['calls']
