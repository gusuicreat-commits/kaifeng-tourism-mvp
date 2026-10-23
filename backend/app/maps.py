"""Narrow same-origin JS API security proxy. No map/route persistence."""
import os
import logging
import re
import time
from collections import OrderedDict
from threading import Lock
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

class _MapAccessLog(logging.Filter):
    def filter(self, record):
        # Uvicorn's access logger receives raw SDK queries (browser key + location).
        # Preserve the path/status while dropping this module's entire query string.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if isinstance(args[2], str) and args[2].startswith('/api/maps/'):
                args[2] = args[2].split('?', 1)[0]
                record.args = tuple(args)
        return True


_access_logger = logging.getLogger('uvicorn.access')
if not any(isinstance(f, _MapAccessLog) for f in _access_logger.filters):
    _access_logger.addFilter(_MapAccessLog())


router = APIRouter(prefix='/api/maps', tags=['maps'])
PATHS = frozenset({
    'v4/map/styles', 'v4/map/styles/light', 'v4/geolocation/sdk',
    'v3/log/init', 'v3/ip', 'v3/assistant/coordinate/convert',
    'v3/place/text', 'v3/place/around', 'v3/place/detail',
    'v5/place/text', 'v5/place/around', 'v5/place/detail',
    'v3/direction/walking', 'v3/direction/driving', 'v3/direction/transit/integrated',
    'v5/direction/walking', 'v5/direction/driving', 'v5/direction/transit/integrated',
})
_rates = OrderedDict()
_lock = Lock()
NO_STORE = {'Cache-Control': 'no-store, private', 'Referrer-Policy': 'same-origin', 'X-Content-Type-Options': 'nosniff'}


def credentials():
    key = os.getenv('AMAP_JS_KEY', '').strip()
    code = os.getenv('AMAP_JS_SECURITY_CODE', '').strip()
    if not all(re.fullmatch(r'[a-fA-F0-9]{32}', x) for x in (key, code)):
        return None
    if key == os.getenv('AMAP_WEB_SERVICE_KEY', '').strip():
        return None
    return key, code


def boundary(request):
    origin = request.headers.get('origin')
    referer = request.headers.get('referer')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, '地图服务仅供本站使用')
    for supplied in (origin, referer):
        if supplied:
            parsed = urlsplit(supplied)
            if parsed.netloc != request.url.netloc or parsed.scheme != request.url.scheme:
                raise HTTPException(403, '地图服务仅供本站使用')
    # Public map endpoint, bounded per-process/IP. Deployment still needs edge quotas.
    who = request.client.host if request.client else 'unknown'
    tick = time.monotonic()
    with _lock:
        count, since = _rates.pop(who, (0, tick))
        if tick - since >= 60: count, since = 0, tick
        _rates[who] = (count + 1, since)
        while len(_rates) > 1024: _rates.popitem(last=False)
        if count >= 120: raise HTTPException(429, '地图请求过于频繁，请稍后再试')


@router.get('/config')
def config(request: Request):
    boundary(request)
    configured = credentials()
    return JSONResponse({'configured': bool(configured), 'version': '2.0',
                         'key': configured[0] if configured else None,
                         'service_host': '/api/maps/_AMapService',
                         'reason': None if configured else '尚未配置 Web端 JS API Key 和安全代理密钥'}, headers=NO_STORE)


@router.get('/_AMapService/{path:path}')
async def proxy(path: str, request: Request):
    boundary(request)
    if path not in PATHS: raise HTTPException(404, '不支持的地图接口')
    configured = credentials()
    if not configured: raise HTTPException(503, '地图安全代理尚未配置')
    key, code = configured
    pairs = list(request.query_params.multi_items())
    if len(request.scope.get('query_string', b'')) > 4096 or len(pairs) > 40:
        raise HTTPException(400, '地图参数过长')
    names = [k.lower() for k, _ in pairs]
    if any(names.count(k)>1 and (k != 's' or len({v for n,v in pairs if n.lower()==k})!=1) for k in set(names)) or any(k in names for k in ('jscode', 'url', 'host', 'target', 'redirect')):
        raise HTTPException(400, '不允许的地图参数')
    callback = request.query_params.get('callback')
    if callback and not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$.]{0,79}', callback):
        raise HTTPException(400, '不允许的回调参数')
    if any(v != key for k, v in pairs if k.lower() == 'key'):
        raise HTTPException(403, '地图 Key 不匹配')
    host = 'webapi.amap.com' if path.startswith('v4/map/styles') else 'restapi.amap.com'
    params = list(dict((k, v) for k, v in pairs if k.lower() != 'key').items()) + [('key', key), ('jscode', code)]
    # Import the existing redaction filter before issuing any credential-bearing request.
    from .city.engine import amap as _redaction
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            async with client.stream('GET', f'https://{host}/{path}', params=params,
                                     headers={'Accept': 'application/json, text/plain, */*'}) as upstream:
                if upstream.status_code != 200:
                    raise HTTPException(502, '地图服务暂不可用')
                chunks = bytearray()
                async for chunk in upstream.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 2 * 1024 * 1024: raise HTTPException(502, '地图响应过大')
                content = bytes(chunks)
                # No upstream headers, cookies, URLs or credential echoes are forwarded.
                for secret in (code, os.getenv('AMAP_WEB_SERVICE_KEY', '')):
                    if secret: content = content.replace(secret.encode(), b'[redacted]')
                media = upstream.headers.get('content-type', 'application/json').split(';')[0]
                if media not in ('application/json', 'text/plain', 'application/octet-stream', 'application/javascript', 'text/javascript'):
                    raise HTTPException(502, '地图响应格式异常')
                # Official plugins use JSONP; upstream sometimes labels it application/json.
                # A valid callback needs executable MIME under our nosniff policy.
                return Response(content, media_type='application/javascript' if callback else media, headers=NO_STORE)
    except httpx.HTTPError:
        raise HTTPException(502, '地图连接失败，请稍后重试') from None
