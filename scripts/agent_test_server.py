"""Local-only V3 browser fixture. Synthetic evidence exists solely in a temporary DB."""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.environ['TOURISM_LLM_API_KEY']=''
os.environ.setdefault('TOURISM_FRONTEND_DIST',str(ROOT/'frontend/dist'))

with tempfile.TemporaryDirectory(prefix='agent-v3-browser-') as folder:
    os.environ['DATABASE_PATH']=str(Path(folder)/'synthetic.sqlite3')
    os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'deletion.sqlite3')
    from app.main import app
    from app.seed import seed
    from app.memory.storage import init_memory
    from app.memory import storage,session
    from app.city import facts,state
    from app.city.assistant import context,service,tools
    from app.city.engine import service as engine
    from app.city.engine.models import OperationalBundle
    from app.city.engine.storage import import_operations
    from app.db import connect
    import uvicorn
    from planning_model_fixture import install
    install()
    moment=datetime.fromisoformat('2030-05-01T09:40:00+08:00')
    for module in (storage,session,state,context,service,tools,engine):module.now=lambda:moment
    seed();init_memory()
    data=json.loads((ROOT/'backend/tests/fixtures/engine_synthetic.json').read_text())
    for record in data['facts']['records']:record['is_demo']=False
    for j in data['operations']['journeys']:
        j['travel']['evidence']['kind']='verified';j['travel']['path_status']='verified';j['time_basis']='survey_envelope'
    for cost in data['operations']['costs']:cost['evidence']['kind']='verified'
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(data['facts']),db,apply=True)
        import_operations(db,OperationalBundle.model_validate(data['operations']),facts.records(db),apply=True)
    uvicorn.run(app,host='127.0.0.1',port=8021,log_level='warning')
