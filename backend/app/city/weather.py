"""Read-only QWeather forecasts for Kaifeng; never a formal itinerary write.

Official v1 endpoints use the account-specific HTTPS host and X-QW-Api-Key.
The city reference point is intentionally not presented as a verified park entrance.
"""
import copy
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends

from ..memory.session import Session, current, limit
from ..memory.storage import now
from .schema import TZ
from .assistant import context
from .schema import EntityId
from .agent.models import WeatherIntent

LATITUDE, LONGITUDE = 34.80, 114.31
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _object(value):
    if not isinstance(value, dict):
        raise ValueError('object required')
    return value


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('number required')
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('number outside expected range')
    return value


def _text(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 32 or not re.fullmatch(r'[\w\-/\s\u4e00-\u9fff]+', value):
        raise ValueError('invalid condition text')
    return value


def _measure(value, unit, low, high):
    value = _object(value)
    if value.get('unit') != unit:
        raise ValueError('unexpected weather unit')
    return round(_number(value.get('value'), low, high), 2)


def _time(value):
    if not isinstance(value, str):
        raise ValueError('forecast time required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('aware forecast time required')
    return result.astimezone(TZ)


def _attributions(data):
    metadata = _object(data.get('metadata'))
    tag = metadata.get('tag')
    rows = metadata.get('attributions')
    if not isinstance(tag, str) or not re.fullmatch(r'[0-9a-f]{16,128}', tag) or not isinstance(rows, list) or not 1 <= len(rows) <= 4:
        raise ValueError('weather metadata required')
    urls = []
    for value in rows:
        if not isinstance(value, str) or len(value) > 300:
            raise ValueError('invalid attribution')
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or not (parsed.hostname == 'qweather.com' or parsed.hostname.endswith('.qweather.com')):
            raise ValueError('unknown attribution host')
        urls.append(value)
    return urls


def _precipitation(value, probability_required=True):
    value = _object(value)
    kind = value.get('type')
    if kind not in ('rain', 'snow', 'ice', 'mixed', 'none', 'unknown'):
        raise ValueError('unknown precipitation type')
    probability = value.get('probability')
    if probability_required:
        probability = round(_number(probability, 0, 1) * 100)
    elif probability is not None:
        probability = round(_number(probability, 0, 1) * 100)
    return {'precipitation_mm': _measure(value.get('amount'), 'mm', 0, 1000),
            'precipitation_probability_percent': probability, 'precipitation_type': kind}


def _condition(value):
    value = _object(value)
    code = value.get('code')
    if not isinstance(code, str) or not re.fullmatch(r'\d{1,5}', code):
        raise ValueError('weather condition code required')
    return _text(value.get('text'))


def _common(row, probability_required=True, *, feels_like=True, temperature=True):
    row = _object(row)
    wind = _object(row.get('wind'))
    direction = _object(wind.get('direction'))
    compass = direction.get('compass')
    if not isinstance(compass, str) or compass not in ('n', 'nne', 'ne', 'ene', 'e', 'ese', 'se', 'sse', 's', 'ssw', 'sw', 'wsw', 'w', 'wnw', 'nw', 'nnw', 'none', 'vrb'):
        raise ValueError('invalid wind direction')
    return {'condition': _condition(row.get('condition')),
            'temperature_c': _measure(row.get('temperature'), '°C', -80, 70) if temperature else 0.0,
            'feels_like_c': _measure(row.get('feelsLike'), '°C', -100, 80) if feels_like else 0.0,
            'wind_direction': compass,
            'wind_speed_mps': _measure(wind.get('speed'), 'm/s', 0, 150),
            **_precipitation(row.get('precipitation'), probability_required)}


def _temperature(row, name):
    return _measure(row.get(name), '°C', -80, 70)


def _hour(row):
    result = _common(row)
    return {'starts_at': _time(row.get('forecastTime')).isoformat(), **result}


def _period(row, name):
    result = _common(row, feels_like=False, temperature=False)
    start, end = _time(row.get('forecastStartTime')), _time(row.get('forecastEndTime'))
    if end <= start or end - start > timedelta(hours=15):
        raise ValueError('invalid daily forecast period')
    return {'period': name, 'starts_at': start.isoformat(), 'ends_at': end.isoformat(), **result}


@dataclass
class _Reply:
    status: str
    data: dict = field(default_factory=dict, repr=False)


class QWeatherProvider:
    def __init__(self, key='', api_host='', *, timeout_seconds=4.0, transport=None,
                 clock=time.monotonic, moment=None):
        if not 0 < timeout_seconds <= 10:
            raise ValueError('invalid weather timeout')
        parsed = urlsplit(api_host)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError('invalid QWeather API host')
        if not parsed.hostname or not parsed.hostname.endswith('.re.qweatherapi.com'):
            raise ValueError('QWeather requires the account-specific API host')
        self.key = key
        self.api_host = api_host.rstrip('/')
        self.clock = clock
        self.moment = moment or now
        self.client = httpx.Client(base_url=self.api_host, timeout=httpx.Timeout(timeout_seconds),
                                   follow_redirects=False, trust_env=False, transport=transport)
        self.events = []
    @classmethod
    def from_environment(cls, **kwargs):
        return cls(os.getenv('QWEATHER_API_KEY', '').strip(),
                   os.getenv('QWEATHER_API_HOST', '').strip(), **kwargs)

    @property
    def enabled(self):
        return bool(self.key)

    def _request(self, kind):
        if not self.enabled:
            return _Reply('disabled')
        paths = {'current': '/weather/v1/current', 'hourly': '/weather/v1/hourly', 'daily': '/weather/v1/daily'}
        if kind not in paths:
            raise ValueError('unsupported weather query')
        ttl = 1800 if kind == 'current' else 3600
        cache_key = (self.api_host, kind)
        with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
            if cached and self.clock() - cached[0] < ttl:
                return _Reply('success', copy.deepcopy(cached[1]))
        started = time.monotonic()
        event = {'endpoint': paths[kind], 'http_status': None, 'status': None}
        try:
            params = {'localTime': 'true', 'lang': 'zh'}
            if kind == 'hourly':
                params['hours'] = '72'
            elif kind == 'daily':
                params['days'] = '7'
            response = self.client.get(f'{paths[kind]}/{LATITUDE:.2f}/{LONGITUDE:.2f}',
                                       params=params, headers={'X-QW-Api-Key': self.key})
            event['http_status'] = response.status_code
            if response.status_code in (401, 403):
                status = 'authorization_failed'
            elif response.status_code == 429:
                status = 'provider_rate_limited'
            elif response.status_code >= 500:
                status = 'provider_unavailable'
            elif response.status_code != 200:
                status = 'http_error'
            elif len(response.content) > 512_000:
                status = 'response_too_large'
            else:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError('weather object required')
                if data.get('code') is not None:
                    status = 'provider_error' if data.get('code') != '200' else 'invalid_response'
                else:
                    attributions = _attributions(data)
                    base = {'source': 'QWeather', 'evidence_status': 'provider_estimate',
                            'location_name': '开封市区参考点', 'latitude': LATITUDE, 'longitude': LONGITUDE,
                            'queried_at': self.moment().astimezone(TZ).isoformat(), 'attributions': attributions}
                    if kind == 'current':
                        data = {**base, **_common(data, probability_required=False)}
                    elif kind == 'hourly':
                        rows = data.get('hours')
                        if not isinstance(rows, list) or not 1 <= len(rows) <= 72:
                            raise ValueError('hourly forecast coverage required')
                        data = {**base, 'hours': [_hour(row) for row in rows]}
                    else:
                        rows = data.get('days')
                        if not isinstance(rows, list) or not 1 <= len(rows) <= 7:
                            raise ValueError('daily forecast coverage required')
                        days = []
                        for row in rows:
                            row = _object(row)
                            start, end = _time(row.get('forecastStartTime')), _time(row.get('forecastEndTime'))
                            if end <= start or end - start > timedelta(days=2):
                                raise ValueError('invalid daily forecast')
                            periods = [_period(row[name], name) for name in ('daytime', 'nighttime')]
                            # Periods carry no temperature; the day's range is reported at day level.
                            days.append({'date': start.date().isoformat(),
                                         'temperature_min_c': _temperature(row, 'temperatureMin'),
                                         'temperature_max_c': _temperature(row, 'temperatureMax'),
                                         'periods': periods})
                        data = {**base, 'days': days}
                    status = 'success'
            if status == 'success':
                with _CACHE_LOCK:
                    if len(_CACHE) >= 24 and cache_key not in _CACHE:
                        _CACHE.clear()
                    _CACHE[cache_key] = (self.clock(), data)
        except (httpx.TimeoutException, httpx.NetworkError):
            status = 'timeout_or_network'
        except httpx.HTTPError:
            status = 'http_error'
        except (ValueError, TypeError, KeyError, AttributeError):
            status = 'invalid_response'
            data = {}
        event.update(status=status, elapsed_ms=round((time.monotonic() - started) * 1000, 2))
        self.events.append(event)
        return _Reply(status, data if status == 'success' else {})

    def current(self):
        return self._request('current')

    def hourly(self):
        return self._request('hourly')

    def daily(self):
        return self._request('daily')


def enabled():
    return os.getenv('QWEATHER_ENABLED', '').lower() in ('1', 'true', 'yes')


def explicit(text):
    if not re.search(r'天气|下雨|降雨|降水|气温', text):
        return None
    if not re.search(r'查|看看|怎样|怎么样|如何|多少|会不会|有没有|吗|？|\?|提醒|影响|预报', text):
        return None
    if re.search(r'(?:按天气|因为天气|由于天气).{0,12}(?:调整|重新|改动|更改|换|安排)', text):
        return None
    return WeatherIntent(quote=text.strip()[:500])


def adjustment_requested(text):
    return text.strip() in ('可以，按天气调整行程', '按天气重新核算行程')


def _failure(status, code='WEATHER_UNAVAILABLE'):
    return {'status': 'unavailable', 'code': code, 'provider_status': status,
            'reply': '天气暂时查不到，没法判断对行程的影响。',
            'weather': None, 'formal_state_changed': False}


def _overlap(row, start, end):
    period_start = datetime.fromisoformat(row['starts_at'])
    period_end = datetime.fromisoformat(row.get('ends_at', row['starts_at'])) if row.get('ends_at') else period_start + timedelta(hours=1)
    return period_start < end and start < period_end


def _risk(rows):
    return any(row['precipitation_probability_percent'] is not None and row['precipitation_probability_percent'] >= 50
               or row['precipitation_mm'] >= 1 or re.search(r'雨|雪|雷|冰雹', row['condition']) for row in rows)


def assess(snapshot, data):
    impacts, unknown = [], []
    for activity in snapshot.envelope.official_plan['activities']:
        planned = activity.get('planned')
        if activity.get('progress') != 'pending' or not planned:
            continue
        start, end = datetime.fromisoformat(planned['starts_at']), datetime.fromisoformat(planned['departs_at'])
        if end <= now():
            continue
        hourly = [row for row in data['hourly']['hours'] if _overlap(row, start, end)]
        coverage = timedelta()
        for row in hourly:
            row_start, row_end = datetime.fromisoformat(row['starts_at']), datetime.fromisoformat(row['starts_at']) + timedelta(hours=1)
            coverage += min(end, row_end) - max(start, row_start)
        if coverage >= end - start:
            matched, basis = hourly, 'hourly_forecast'
        else:
            matched = [period for day in data['daily']['days'] for period in day['periods'] if _overlap(period, start, end)]
            coverage = sum((min(end, datetime.fromisoformat(row['ends_at'])) - max(start, datetime.fromisoformat(row['starts_at'])) for row in matched), timedelta())
            basis = 'daily_period_forecast'
        if coverage < end - start:
            unknown.append({'activity_id': activity['stop_id'], 'name': activity['name'],
                            'starts_at': start.isoformat(), 'reason': 'forecast_coverage_incomplete'})
            continue
        if not _risk(matched):
            continue
        impacts.append({'activity_id': activity['stop_id'], 'name': activity['name'],
                        'starts_at': start.isoformat(), 'departs_at': end.isoformat(), 'basis': basis,
                        'conditions': list(dict.fromkeys(row['condition'] for row in matched))[:4],
                        'max_precipitation_probability_percent': max((row['precipitation_probability_percent'] or 0) for row in matched),
                        'precipitation_mm': round(sum(row['precipitation_mm'] for row in matched), 2),
                        'venue_weather_policy': 'unknown', 'indoor_suitability': 'unknown'})
    return impacts, unknown


def _report(snapshot, data, impacts, unknown):
    multi=bool(getattr(snapshot,'multi_draft',None))
    report = {'location_name': data['hourly']['location_name'], 'source': 'QWeather',
              'evidence_status': 'provider_estimate', 'queried_at': data['hourly']['queried_at'],
              'attributions': data['hourly']['attributions'], 'current': data.get('current'),
              'hourly': data['hourly'], 'daily': data['daily'], 'impacts': impacts,
              'coverage_unknown': unknown, 'adjustment_offered': bool(impacts and snapshot.route.get('itinerary') and not multi),
              'trip_version': snapshot.row['version'], 'route_version': snapshot.row['route_version'],
              'formal_state_changed': False, 'automatic_route_change': False,
              'advice_basis': 'forecast_only_not_cancellation_or_indoor_venue_fact'}
    if impacts:
        first = impacts[0]
        start, end = datetime.fromisoformat(first['starts_at']), datetime.fromisoformat(first['departs_at'])
        period=f'{start:%m月%d日 %H:%M}—{end:%H:%M}' if multi else f'{start:%H:%M}—{end:%H:%M}'
        reply = (f'开封参考点预报显示，{first["name"]}在{period}时段'
                 f'降水概率最高{first["max_precipitation_probability_percent"]}%，累计预报降水量约{first["precipitation_mm"]:g}毫米。'
                 '这是天气风险，不代表景区或演出取消。'+('' if multi else '需要我重新核算现有行程吗？'))
        if first['basis'] == 'daily_period_forecast':
            reply += '这段只有白天/晚间时段预报，不能推断具体小时。'
    elif unknown:
        reply = '有些时段还没有天气预报，暂时判断不了。'
    else:
        reply = '已查询开封天气，当前有完整预报覆盖的行程时段未触发降水提醒；预报会变化，出行前可再次查询。'
    if multi:report.update(scope='current_day',day_id=snapshot.day_id)
    return {'status': 'answered', 'reply': reply, 'weather': report,
            'history_reply': reply, 'formal_state_changed': False}


def answer(snapshot, *, include_current=True, provider=None):
    if not enabled():
        return _failure('disabled', 'WEATHER_DISABLED')
    try:
        provider = provider or QWeatherProvider.from_environment()
    except ValueError:
        return _failure('configuration_invalid')
    data, events = {}, []
    try:
        before = len(provider.events)
        replies = []
        if include_current:
            replies.append(('current', provider.current()))
        replies.extend((('hourly', provider.hourly()), ('daily', provider.daily())))
        events.extend(provider.events[before:])
        failed = next((reply.status for _, reply in replies if reply.status != 'success'), None)
        if failed:
            return _failure(failed) | {'provider_events': events}
        data = {name: reply.data for name, reply in replies}
        impacts, unknown = assess(snapshot, data)
        return _report(snapshot, data, impacts, unknown) | {'provider_events': events}
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        return _failure('invalid_response') | {'provider_events': events}


def adjustment_preview(session, trip_id, snapshot):
    from .assistant import tools
    tools.require_single_day(snapshot)
    result = answer(snapshot, include_current=False)
    result.pop('provider_events', None)
    if result['status'] != 'answered' or not result['weather']['impacts']:
        return result
    if not snapshot.route.get('itinerary'):
        return {'status': 'needs_data', 'reply': '还没有正式行程，不能按天气重算；请先形成并确认一份行程。', 'weather': result['weather']}
    from .assistant.models import Intent
    from .agent.service import _preview_result
    preview = tools.preview(session, trip_id, snapshot,
                            Intent(operation='preview', purpose='modify', execute=True, preserve_shows=True), uuid4())
    if preview['status'] != 'preview':
        return {'status': preview['status'], 'reply': '天气查过了，现在的行程不需要调整。',
                'weather': result['weather'], 'planning': preview}
    output = _preview_result(preview, snapshot)
    output.update(reply='已按最新天气重新排了一版，看看是否合适，确认后保存。',
                  weather=result['weather'], weather_recheck='queried_before_preview')
    return output


def capabilities():
    configured = bool(os.getenv('QWEATHER_API_KEY', '').strip() and os.getenv('QWEATHER_API_HOST', '').strip())
    return {'enabled': enabled(), 'configured': configured,
            'location': 'Kaifeng_city_reference_point', 'scope': 'read_only_forecast_and_adjustment_question',
            'current_cache_seconds': 1800, 'forecast_cache_seconds': 3600,
            'formal_state_changed': False, 'background_notifications': False,
            'weather_drives_solver_directly': False, 'venue_cancellation_evidence': 'not_inferred'}


router = APIRouter(prefix='/api/city/v1/trips/{trip_id}/weather', tags=['和风天气只读提醒'])


@router.get('')
def trip_weather(trip_id: EntityId, session: Session = Depends(current)):
    limit('city-weather:' + session.id, 30)
    snapshot = context.load(session, trip_id, 'trip')
    result = answer(snapshot)
    result.pop('provider_events', None)
    return result
