"""Disposable DB for end-to-end tests, never touches the user's development DB."""
import os
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import uvicorn


def configure_environment():
    # Set these before app.main imports load_dotenv; existing Playwright values remain explicit.
    for name in ('AMAP_PLANNING_ENABLED','AMAP_REFERENCE_ROUTES_ENABLED',
                 'AMAP_LOCATION_SEARCH_ENABLED','FLYAI_ENABLED','QWEATHER_ENABLED'):
        override='E2E_'+name
        if override in os.environ:
            os.environ[name]=os.environ[override]
        else:
            os.environ.setdefault(name,'0')
    for name in ('AMAP_WEB_SERVICE_KEY','AMAP_JS_KEY','FLYAI_API_KEY',
                 'QWEATHER_API_KEY','DASHSCOPE_API_KEY','TOURISM_LLM_API_KEY'):
        os.environ[name]=''
    os.environ['KAIFENG_DEMO_MODE']='0' if os.getenv('E2E_DEMO_MODE')=='0' else '1'


configure_environment()
from planning_model_fixture import install
install()
with tempfile.TemporaryDirectory(prefix='bianyou-e2e-') as test_dir:
    os.environ['DATABASE_PATH']=str(Path(test_dir)/'test.sqlite3')
    os.environ['M7_DELETION_LEDGER_PATH']=str(Path(test_dir)/'deletions.sqlite3')
    os.environ['SCENIC_ADMIN_API_KEY']='e2e-only-admin-key-01234567890123456789'
    # Many isolated browser identities share one loopback IP during the suite.
    os.environ['M7_SESSION_RATE_LIMIT']='10000'
    # Explicit test-only catalog. All data live in this disposable directory.
    if os.getenv('E2E_CITY_FACTS'):
        from app.seed import seed
        from app.memory.storage import init_memory
        from app.db import connect
        from app.city.facts import FactBundle, import_bundle
        seed();init_memory()
        with connect() as db:
            import_bundle(FactBundle.model_validate_json(Path(os.environ['E2E_CITY_FACTS']).read_text()),db,apply=True)
            if os.getenv('E2E_OBJECT_BINDINGS'):
                import json
                for binding in json.loads(Path(os.environ['E2E_OBJECT_BINDINGS']).read_text()):
                    db.execute('INSERT INTO city_object_bindings VALUES (?,?,?,?)',tuple(binding))
            if os.getenv('E2E_JOURNEYS'):
                from app.city.facts import records
                from app.city.engine.models import OperationalBundle
                from app.city.engine.storage import import_operations
                import_operations(db,OperationalBundle.model_validate_json(Path(os.environ['E2E_JOURNEYS']).read_text()),records(db),apply=True)
    uvicorn.run('app.main:app',host='127.0.0.1',port=int(os.getenv('E2E_PORT','8018')))
