import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ('AMAP_PLANNING_ENABLED', 'AMAP_REFERENCE_ROUTES_ENABLED',
         'AMAP_LOCATION_SEARCH_ENABLED', 'FLYAI_ENABLED', 'QWEATHER_ENABLED')
KEYS = ('AMAP_WEB_SERVICE_KEY', 'AMAP_JS_KEY', 'FLYAI_API_KEY',
        'QWEATHER_API_KEY', 'DASHSCOPE_API_KEY', 'TOURISM_LLM_API_KEY')


def run_browser_server(tmp_path, *, overrides=None):
    dotenv = tmp_path / '.env'
    dotenv.write_text('\n'.join([
        'AMAP_PLANNING_ENABLED=1',
        'AMAP_REFERENCE_ROUTES_ENABLED=true',
        'AMAP_LOCATION_SEARCH_ENABLED=yes',
        'FLYAI_ENABLED=1',
        'QWEATHER_ENABLED=1',
        'AMAP_WEB_SERVICE_KEY=fake-amap-service-key',
        'AMAP_JS_KEY=fake-amap-js-key',
        'FLYAI_API_KEY=fake-flyai-key',
        'QWEATHER_API_KEY=fake-weather-key',
        'DASHSCOPE_API_KEY=fake-dashscope-key',
        'TOURISM_LLM_API_KEY=fake-llm-key',
        'KAIFENG_DEMO_MODE=0',
        'E20_DOTENV_LOADED=1',
    ]) + '\n')
    code = """
import importlib
import json
import os
import runpy
import sys
from pathlib import Path
import dotenv

_load_dotenv = dotenv.load_dotenv
dotenv.load_dotenv = lambda *args, **kwargs: _load_dotenv(Path(sys.argv[1]), override=False)
import uvicorn

def capture(*args, **kwargs):
    importlib.import_module('app.main')
    from app import language
    from app.city import weather
    from app.city.agent import reference_routes
    from app.city.engine import live_travel, service
    service.facts.records = lambda db: []
    service.load_operations = lambda db: []
    context = service.context(None)
    print(json.dumps({
        'flags': {name: os.getenv(name) for name in (
            'AMAP_PLANNING_ENABLED', 'AMAP_REFERENCE_ROUTES_ENABLED',
            'AMAP_LOCATION_SEARCH_ENABLED', 'FLYAI_ENABLED', 'QWEATHER_ENABLED')},
        'keys_empty': all(not os.getenv(name, '').strip() for name in (
            'AMAP_WEB_SERVICE_KEY', 'AMAP_JS_KEY', 'FLYAI_API_KEY',
            'QWEATHER_API_KEY', 'DASHSCOPE_API_KEY', 'TOURISM_LLM_API_KEY')),
        'demo_mode': os.getenv('KAIFENG_DEMO_MODE'),
        'dotenv_loaded': os.getenv('E20_DOTENV_LOADED'),
        'app_reads': {
            'model_available': language.available(),
            'amap_planning_enabled': live_travel.enabled(),
            'reference_routes_enabled': reference_routes.enabled(),
            'location_search_enabled': reference_routes.capabilities()['poi_search_enabled'],
            'flyai_enabled': reference_routes.capabilities()['travel_services']['flyai_enabled'],
            'qweather_enabled': weather.enabled(),
            'demo_reference': context.demo_reference,
        },
    }, ensure_ascii=False))

uvicorn.run = capture
runpy.run_path(sys.argv[2], run_name='__main__')
"""
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'backend'), str(ROOT / 'scripts')))
    for name in list(env):
        if name.startswith('E2E_') or name in FLAGS or name == 'E20_DOTENV_LOADED':
            env.pop(name)
    env.update({name: 'fake-inherited-key' for name in KEYS})
    env['KAIFENG_DEMO_MODE'] = '0'
    if overrides:
        env.update(overrides)
    result = subprocess.run(
        [sys.executable, '-c', code, str(dotenv), str(ROOT / 'scripts' / 'browser_server.py')],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def test_browser_server_isolates_dotenv_provider_configuration(tmp_path):
    result = run_browser_server(tmp_path)

    assert result['flags'] == {
        'AMAP_PLANNING_ENABLED': '0',
        'AMAP_REFERENCE_ROUTES_ENABLED': '0',
        'AMAP_LOCATION_SEARCH_ENABLED': '0',
        'FLYAI_ENABLED': '0',
        'QWEATHER_ENABLED': '0',
    }
    assert result['keys_empty'] is True
    assert result['dotenv_loaded'] == '1'
    assert result['demo_mode'] == '1'
    assert result['app_reads'] == {
        'model_available': False,
        'amap_planning_enabled': False,
        'reference_routes_enabled': False,
        'location_search_enabled': False,
        'flyai_enabled': False,
        'qweather_enabled': False,
        'demo_reference': True,
    }


def test_browser_server_allows_explicit_demo_override(tmp_path):
    result = run_browser_server(tmp_path, overrides={'E2E_DEMO_MODE': '0'})

    assert result['demo_mode'] == '0'
    assert result['app_reads']['demo_reference'] is False


@pytest.mark.parametrize('source', ['direct', 'e2e', 'priority'])
def test_browser_server_preserves_explicit_provider_overrides(tmp_path, source):
    overrides = {name: '1' for name in FLAGS} if source == 'direct' else {
        'E2E_' + name: '1' for name in FLAGS}
    if source == 'priority':
        overrides.update({name: '0' for name in FLAGS})
    result = run_browser_server(tmp_path, overrides=overrides)

    assert result['flags'] == dict.fromkeys(FLAGS, '1')
    assert result['keys_empty'] is True
