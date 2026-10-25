"""Disposable DB for end-to-end tests, never touches the user's development DB."""
import os
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import uvicorn
from planning_model_fixture import install
install()
with tempfile.TemporaryDirectory(prefix='bianyou-e2e-') as test_dir:
    os.environ['DATABASE_PATH']=str(Path(test_dir)/'test.sqlite3')
    os.environ['M7_DELETION_LEDGER_PATH']=str(Path(test_dir)/'deletions.sqlite3')
    os.environ['TOURISM_LLM_API_KEY']=''  # Tests never trigger a paid provider from the user's .env.
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
    uvicorn.run('app.main:app',host='127.0.0.1',port=int(os.getenv('E2E_PORT','8018')))
