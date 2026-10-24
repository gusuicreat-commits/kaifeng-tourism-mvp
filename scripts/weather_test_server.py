"""Temporary DB for weather-reminder browser acceptance; synthetic forecast only."""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import Body
from fastapi.routing import APIRoute
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['TOURISM_LLM_API_KEY'] = ''
os.environ.setdefault('TOURISM_FRONTEND_DIST', str(ROOT / 'frontend/dist'))

with tempfile.TemporaryDirectory(prefix='weather-browser-') as folder:
    os.environ['DATABASE_PATH'] = str(Path(folder) / 'synthetic.sqlite3')
    os.environ['M7_DELETION_LEDGER_PATH'] = str(Path(folder) / 'deletion.sqlite3')
    os.environ['QWEATHER_ENABLED'] = '1'
    os.environ['QWEATHER_API_KEY'] = 'synthetic-weather-key'
    os.environ['QWEATHER_API_HOST'] = 'https://synthetic.re.qweatherapi.com'

    from app.main import app
    from app.seed import seed
    from app.memory.storage import init_memory
    from app.memory import storage, session
    from app.city import facts, state
    from app.city.assistant import context, service, tools
    from app.city.engine import service as engine
    from app.city.engine.models import OperationalBundle
    from app.city.engine.storage import import_operations
    from app.db import connect
    import uvicorn
    from planning_model_fixture import install
    install()

    clock = [datetime.fromisoformat('2030-05-01T08:00:00+08:00')]
    for module in (storage, session, state, context, service, tools, engine):
        module.now = lambda: clock[0]

    seed(); init_memory()
    data = json.loads((ROOT / 'backend/tests/fixtures/engine_synthetic.json').read_text())
    for record in data['facts']['records']:
        record['is_demo'] = False
    for journey in data['operations']['journeys']:
        journey['travel']['evidence']['kind'] = 'verified'
        journey['travel']['path_status'] = 'verified'
        journey['time_basis'] = 'survey_envelope'
    for cost in data['operations']['costs']:
        cost['evidence']['kind'] = 'verified'
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(data['facts']), db, apply=True)
        import_operations(db, OperationalBundle.model_validate(data['operations']), facts.records(db), apply=True)

    from app.city import weather
    weather.now = lambda: clock[0]
    META = {'tag': '0' * 64, 'attributions': ['https://developer.qweather.com/attribution.html']}

    def period(condition='晴', probability=.1, amount=0, temperature=23):
        return {'condition': {'text': condition, 'code': '100'},
                'temperature': {'value': temperature, 'unit': '°C'},
                'feelsLike': {'value': temperature, 'unit': '°C'},
                'wind': {'direction': {'degree': 0, 'compass': 'n'}, 'speed': {'value': 2, 'unit': 'm/s'}},
                'precipitation': {'amount': {'value': amount, 'unit': 'mm'}, 'probability': probability,
                                  'type': 'rain' if probability else 'none'}}

    def hourly_rows():
        rows = []
        for hour in range(24):
            rain = hour in (11, 12, 13, 14, 15, 16)
            rows.append({'forecastTime': f'2030-05-01T{hour:02d}:00:00+08:00',
                         **period('小雨' if rain else '晴', .8 if rain else .1, 1.2 if rain else 0)})
        return rows

    def daily_rows():
        rows = []
        for day in range(1, 8):
            rows.append({'forecastStartTime': f'2030-05-{day:02d}T00:00:00+08:00',
                         'forecastEndTime': f'2030-05-{day + 1:02d}T00:00:00+08:00',
                         'temperatureMin': {'value': 15, 'unit': '°C'},
                         'temperatureMax': {'value': 26, 'unit': '°C'},
                         'daytime': {'forecastStartTime': f'2030-05-{day:02d}T07:00:00+08:00',
                                     'forecastEndTime': f'2030-05-{day:02d}T19:00:00+08:00', **period()},
                         'nighttime': {'forecastStartTime': f'2030-05-{day:02d}T19:00:00+08:00',
                                       'forecastEndTime': f'2030-05-{day + 1:02d}T07:00:00+08:00', **period()}})
        return rows

    def handler(request):
        if '/current/' in request.url.path:
            return httpx.Response(200, json={'metadata': META, **period()})
        if '/hourly/' in request.url.path:
            return httpx.Response(200, json={'metadata': META, 'hours': hourly_rows()})
        return httpx.Response(200, json={'metadata': META, 'days': daily_rows()})

    weather.QWeatherProvider.from_environment = classmethod(
        lambda cls, **kwargs: weather.QWeatherProvider(
            'synthetic-weather-key', 'https://synthetic.re.qweatherapi.com',
            transport=httpx.MockTransport(handler)))

    class ClockBody(BaseModel):
        now: str

    def advance_clock(body: ClockBody):
        clock[0] = datetime.fromisoformat(body.now)
        return {'now': clock[0].isoformat()}

    def fixture_requirements():
        return data['requirements']

    # Insert before the single-origin frontend catch-all so /api/test/* resolves.
    app.router.routes.insert(0, APIRoute('/api/test/clock', advance_clock, methods=['POST']))
    app.router.routes.insert(0, APIRoute('/api/test/requirements', fixture_requirements, methods=['GET']))

    uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('WEATHER_TEST_PORT', '8021')), log_level='warning')
