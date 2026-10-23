"""Opt-in AMap adapter. Raw POIs/geometry live only in request memory.

v3 remains the compatibility default; M4 experiments explicitly select v5.
A point ETA is never promoted to a verified path or a horizon-wide upper bound.
"""
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from threading import Lock

import httpx
from ..schema import TZ


class _RedactMapQuery(logging.Filter):
    def filter(self, record):
        # httpx INFO logs the full query URL, including percent-encoded values.
        message = record.getMessage()
        message = re.sub(r'(?i)([?&](?:key|sig|jscode|securityJsCode)=)[^&\s"\']+', r'\1[REDACTED]', message)
        record.msg, record.args = message, ()
        return True


for _name in ('httpx', 'httpcore', 'httpcore.connection', 'httpcore.http11', 'httpcore.http2', 'httpcore.proxy'):
    _logger = logging.getLogger(_name)
    if not any(isinstance(f, _RedactMapQuery) for f in _logger.filters):
        _logger.addFilter(_RedactMapQuery())


def _number(value, *, maximum=172800, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('numeric field required')
    value = float(value)
    if not math.isfinite(value) or value < 0 or value > maximum or positive and value == 0:
        raise ValueError('numeric field out of range')
    return value


def _text(value):
    return value if isinstance(value, str) else None


def _coordinate(longitude, latitude):
    lon = _number(longitude, maximum=180)
    lat = _number(latitude, maximum=90)
    # M4 is deliberately Kaifeng-only; catches swapped axes and another city.
    if not (113.5 <= lon <= 115.5 and 34.0 <= lat <= 35.5):
        raise ValueError('outside Kaifeng experiment bounds')
    return f'{lon:.6f},{lat:.6f}'


@dataclass(frozen=True)
class Quote:
    status: str
    duration_seconds: int | None = None
    distance_m: float | None = None
    cost_fen: int | None = None
    queried_at: str | None = None
    departure_at: str | None = None
    source: str = 'AMap Web Service'
    uncertainty: str = 'provider estimate; not a guaranteed arrival or calibrated upper bound'
    walking_seconds: int | None = None
    walking_distance_m: float | None = None
    provider: str = 'amap'
    coordinate_system: str = 'GCJ02'
    source_reference: str | None = None  # endpoint path only, no query/key
    api_version: str = 'v3'
    alternatives_count: int = 0
    steps_count: int = 0
    geometry_available: bool = False
    transit_lines_count: int | None = None
    transfers: int | None = None
    total_includes_wait: bool | None = None
    origin_id: str | None = None
    destination_id: str | None = None
    # Restricted data, excluded from repr. Never use dataclasses.asdict for reports.
    paths: tuple = field(default=(), repr=False)


@dataclass(frozen=True)
class Poi:
    poi_id: str = field(repr=False)
    name: str = field(repr=False)
    longitude: float = field(repr=False)
    latitude: float = field(repr=False)
    address: str | None = field(default=None, repr=False)
    adcode: str | None = None
    citycode: str | None = None
    category: str | None = None
    coordinate_system: str = 'GCJ02'
    entrance_verified: bool = False
    coordinate_role: str = 'provider_poi_not_verified_entrance'
    children: tuple = field(default=(), repr=False)
    business: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class PoiSearch:
    status: str
    pois: tuple[Poi, ...] = field(default=(), repr=False)
    rejected_count: int = 0
    api_version: str = 'v3'


@dataclass(frozen=True)
class _Reply:
    status: str
    data: dict = field(default_factory=dict, repr=False)


# Official error table: numeric codes are safer than arbitrary upstream text.
_AUTH = {'10001', '10005', '10006', '10007', '10008', '10009', '10011', '10013', '10026'}
_PERMISSION = {'10002', '10012', '10041'}
_LIMIT = {'10003', '10004', '10010', '10014', '10015', '10019', '10020', '10021', '10029', '10044', '10045'}


class AmapProvider:
    def __init__(self, key='', *, enabled=False, terms_reviewed=False, timeout_seconds=2.0,
                 max_requests=30, qps=3, transport=None, clock=time.monotonic, sleeper=time.sleep,
                 api_version='v3', retries=1):
        if (not 0 < timeout_seconds <= 10 or not 1 <= max_requests <= 100 or not 1 <= qps <= 10
                or api_version not in ('v3', 'v5') or retries not in (0, 1)):
            raise ValueError('请求限制无效')
        self._key = key
        self.enabled = enabled and terms_reviewed and bool(key)
        self.timeout = timeout_seconds
        self.max_requests, self.interval = max_requests, 1 / qps
        self.clock, self.sleeper = clock, sleeper
        self.api_version, self.retries = api_version, retries
        self._last, self._count = None, 0
        self.lock = Lock()
        self.events = []  # allowlisted diagnostics, never provider payloads or URLs
        self.halted = None
        self.client = httpx.Client(timeout=httpx.Timeout(timeout_seconds), transport=transport,
                                   follow_redirects=False, trust_env=False)

    @classmethod
    def from_environment(cls, *, enabled=False, terms_reviewed=False, **kwargs):
        # Never fall back to VITE_AMAP_KEY or use a JS security code for Web Services.
        return cls(os.getenv('AMAP_WEB_SERVICE_KEY', ''), enabled=enabled,
                   terms_reviewed=terms_reviewed, **kwargs)

    @property
    def request_count(self):
        return self._count

    def close(self):
        self.client.close()

    def _request(self, path, params):
        if not self.enabled:
            return _Reply('disabled')
        if self.halted:
            return _Reply(self.halted)
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleeper(self.interval)
            with self.lock:
                now = self.clock()
                if self._count >= self.max_requests:
                    return _Reply('request_budget_exhausted')
                if self._last is not None and now - self._last < self.interval:
                    return _Reply('local_rate_limited')
                self._last, self._count = now, self._count + 1
                sequence = self._count
            started = time.monotonic()
            event = {'sequence': sequence, 'endpoint': path, 'http_status': None,
                     'business_status': None, 'infocode': None, 'info': None}
            try:
                response = self.client.get('https://restapi.amap.com' + path,
                                           params={**params, 'key': self._key, 'output': 'JSON'})
                event['http_status'] = response.status_code
                if response.status_code == 429:
                    self.halted = status = 'provider_rate_limited'
                elif response.status_code in (401, 403):
                    self.halted = status = 'authorization_or_provider_error'
                elif response.status_code >= 500:
                    status = 'provider_unavailable'
                elif response.status_code != 200:
                    status = 'http_error'
                elif len(response.content) > 2_000_000:
                    status = 'response_too_large'
                else:
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError('object required')
                    raw_status, code, info = data.get('status'), data.get('infocode'), data.get('info')
                    event['business_status'] = raw_status if raw_status in ('0', '1') else None
                    event['infocode'] = code if isinstance(code, str) and re.fullmatch(r'\d{5}', code) else None
                    # info is normally an enum, but never echo arbitrary provider strings/key.
                    event['info'] = info if isinstance(info, str) and re.fullmatch(r'[A-Z_]{2,60}', info) and self._key not in info else None
                    if raw_status == '1' and code in (None, '10000'):
                        event['status'] = 'success'
                        event['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
                        self.events.append(event)
                        return _Reply('success', data)
                    if code in _LIMIT:
                        self.halted = status = 'provider_rate_limited'
                    elif code in _AUTH:
                        self.halted = status = 'authorization_or_provider_error'
                    elif code in _PERMISSION:
                        # Endpoint-specific permission denial does not invalidate all APIs.
                        status = 'permission_denied'
                    elif code in ('40000', '40002'):
                        self.halted = status = 'quota_or_service_expired'
                    elif code in ('20801', '20802'):
                        status = 'no_route'
                    elif code in ('10016', '10017'):
                        status = 'provider_unavailable'
                    elif code in ('20000', '20001', '20002', '20003'):
                        status = 'invalid_parameters'
                    else:
                        status = 'authorization_or_provider_error'
            except (httpx.TimeoutException, httpx.NetworkError):
                status = 'timeout_or_network'
            except httpx.HTTPError:
                status = 'http_error'
            except (ValueError, TypeError, KeyError, AttributeError):
                status = 'invalid_response'
            event['status'] = status
            event['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
            self.events.append(event)
            if status not in ('provider_unavailable', 'timeout_or_network'):
                break
        return _Reply(status)

    def search_pois(self, keywords='', *, version='v3', city='410200', around=None, types=None):
        if city != '410200':
            return PoiSearch('wrong_city', api_version=version)
        if version not in ('v3', 'v5') or not isinstance(keywords, str) or len(keywords) > 80 or not (keywords or types):
            return PoiSearch('invalid_parameters', api_version=version)
        params = {'keywords': keywords}
        if version == 'v3':
            params.update(city=city, citylimit='true', offset='10', page='1', extensions='all')
        else:
            params.update(region=city, city_limit='true', page_size='10', page_num='1', show_fields='children,business')
        if types:
            params['types'] = types
        if around is not None:
            try:
                if around.coordinate_system != 'GCJ02':
                    raise ValueError('coordinate system')
                params.update(location=_coordinate(around.longitude, around.latitude), radius='1500')
            except (ValueError, TypeError, AttributeError):
                return PoiSearch('coordinate_mismatch', api_version=version)
        path = f'/{version}/place/' + ('around' if around is not None else 'text')
        reply = self._request(path, params)
        if reply.status != 'success':
            return PoiSearch(reply.status, api_version=version)
        rows = reply.data.get('pois')
        if not isinstance(rows, list):
            return PoiSearch('invalid_response', api_version=version)
        pois, rejected = [], 0
        for row in rows[:10]:
            try:
                if not isinstance(row, dict):
                    raise ValueError('POI object required')
                adcode = _text(row.get('adcode'))
                if not adcode or not adcode.startswith('4102'):
                    raise ValueError('not Kaifeng')
                loc = row['location'].split(',')
                if len(loc) != 2:
                    raise ValueError('coordinate pair required')
                _coordinate(*loc)
                pid, name = _text(row.get('id')), _text(row.get('name'))
                if not pid or not name:
                    raise ValueError('identity required')
                children = row.get('children', ())
                business = row.get('business', row.get('biz_ext', {}))
                pois.append(Poi(pid, name, float(loc[0]), float(loc[1]), _text(row.get('address')),
                                adcode, _text(row.get('citycode')), _text(row.get('type')),
                                children=tuple(children) if isinstance(children, list) else (),
                                business=business if isinstance(business, dict) else {}))
            except (ValueError, TypeError, KeyError, AttributeError):
                rejected += 1
        return PoiSearch('found' if pois else 'no_poi' if not rows else 'invalid_poi',
                         tuple(pois), rejected, version)

    def estimate(self, origin, destination, mode, departure, queried_at, *,
                 use_poi_ids=False, citycode=None, strategy=None):
        base = dict(queried_at=queried_at.isoformat(), departure_at=departure.isoformat(),
                    api_version=self.api_version, origin_id=getattr(origin, 'fact_id', None),
                    destination_id=getattr(destination, 'fact_id', None))
        def fail(status):
            return Quote(status, **base)
        if not self.enabled:
            return fail('disabled')
        if mode not in ('walking', 'driving', 'transit'):
            return fail('unsupported_mode')
        try:
            for p in (origin, destination):
                if p.coordinate_system != 'GCJ02':
                    raise ValueError('coordinate system')
            params = {'origin': _coordinate(origin.longitude, origin.latitude),
                      'destination': _coordinate(destination.longitude, destination.latitude)}
        except (ValueError, TypeError, AttributeError):
            return fail('coordinate_mismatch')
        if departure.tzinfo is None or queried_at.tzinfo is None:
            return fail('invalid_parameters')
        version = self.api_version
        if use_poi_ids:
            ids = (getattr(origin, 'poi_id', None), getattr(destination, 'poi_id', None))
            # Walking docs do not specify POI-id parameters. Never silently ignore them.
            if version != 'v5' or mode == 'walking' or not all(ids):
                return fail('poi_id_not_supported')
            names = ('origin_id', 'destination_id') if mode == 'driving' else ('originpoi', 'destinationpoi')
            params.update(zip(names, ids))
        if mode == 'transit':
            local = departure.astimezone(TZ)
            params.update(date=local.strftime('%Y-%m-%d'), time=local.strftime('%H-%M' if version == 'v5' else '%H:%M'))
            if version == 'v5':
                # citycode comes from the matched POI, not an adcode guessed as citycode.
                if not isinstance(citycode, str) or not re.fullmatch(r'\d{3,4}', citycode):
                    return fail('citycode_required')
                params.update(city1=citycode, city2=citycode, AlternativeRoute='3')
            else:
                params['city'] = '开封'
        if strategy is not None:
            if mode != 'driving' or strategy not in (0, 1, 2, 32, 33, 34, 35, 36, 37, 38):
                return fail('invalid_parameters')
            params['strategy'] = str(strategy)
        if version == 'v5':
            params['show_fields'] = 'cost,polyline'
        path = f'/{version}/direction/' + {'walking': 'walking', 'driving': 'driving', 'transit': 'transit/integrated'}[mode]
        reply = self._request(path, params)
        if reply.status != 'success':
            return fail(reply.status)
        try:
            route = reply.data['route']
            paths = route.get('transits' if mode == 'transit' else 'paths')
            if not isinstance(paths, list):
                return fail('invalid_response')
            if not paths:
                return fail('no_route')
            p = paths[0]
            cost = p.get('cost', {}) if version == 'v5' else {}
            duration = cost.get('duration') if version == 'v5' else p.get('duration')
            seconds = math.ceil(_number(duration, positive=True))
            metres = _number(p.get('distance'), maximum=1_000_000, positive=True)
            walking_seconds, walking_m, lines, transfers = None, None, None, None
            steps = p.get('steps', []) if mode != 'transit' else []
            if not isinstance(steps, list):
                raise ValueError('steps required')
            if mode == 'walking':
                walking_seconds, walking_m = seconds, metres
            if mode == 'transit':
                segments = p.get('segments', [])
                if not isinstance(segments, list):
                    raise ValueError('invalid transit segments')
                walks, walk_metres, lines = [], [], 0 if segments else None
                for segment in segments:
                    w = segment.get('walking')
                    if isinstance(w, dict):
                        walks.append(w.get('duration', w.get('cost', {}).get('duration') if isinstance(w.get('cost'), dict) else None))
                        walk_metres.append(w.get('distance'))
                        ws = w.get('steps', [])
                        if isinstance(ws, list):
                            steps.extend(ws)
                    else:
                        walks.append(None); walk_metres.append(None)
                    bus = segment.get('bus')
                    if isinstance(bus, dict) and isinstance(bus.get('buslines'), list) and bus['buslines']:
                        lines += 1  # multiple buslines can be alternatives for one segment
                if walks and all(v not in (None, [], '') for v in walks):
                    walking_seconds = math.ceil(sum(_number(v) for v in walks))
                if walk_metres and all(v not in (None, [], '') for v in walk_metres):
                    walking_m = sum(_number(v, maximum=1_000_000) for v in walk_metres)
                transfers = max(lines - 1, 0) if lines else None
                if walking_seconds is not None and walking_seconds > seconds:
                    raise ValueError('walking exceeds total')
            cost_fen = 0 if mode == 'walking' else None
            # Never equate tolls/taxi guesses with total door-to-door driving cost.
            if mode == 'transit' and version == 'v3' and p.get('cost') not in (None, [], ''):
                cost_fen = math.ceil(_number(p['cost'], maximum=1_000_000) * 100)
            geometry = any(isinstance(step, dict) and isinstance(step.get('polyline'), str) and bool(step['polyline']) for step in steps)
            return Quote('estimated', seconds, metres, cost_fen, **base,
                         walking_seconds=walking_seconds, walking_distance_m=walking_m,
                         source_reference=path, alternatives_count=len(paths), steps_count=len(steps),
                         geometry_available=geometry, transit_lines_count=lines, transfers=transfers,
                         total_includes_wait=True if mode == 'transit' and version == 'v5' else None,
                         paths=tuple(paths))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            return fail('invalid_response')


def quote_to_engine_estimate(quote, origin_id, destination_id, mode):
    """Admission gate, not fabricated bounds. Current schema requires surveyed paths.

    Explicitly preserve unknown until an approved path/validity/envelope policy exists.
    Never return a bound pair by copying the provider's single ETA into both fields.
    """
    from ..travel import unknown
    return unknown(origin_id, destination_id, mode,
                   'AMap quote lacks verified entrance/path and calibrated horizon envelope'
                   if quote.status == 'estimated' else 'AMap quote unavailable: ' + quote.status)
