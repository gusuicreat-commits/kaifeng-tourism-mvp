"""Isolated map acceptance server: no production DB, no model or routing provider."""
import os
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.environ['TOURISM_LLM_API_KEY']=''
os.environ.setdefault('TOURISM_FRONTEND_DIST',str(ROOT/'frontend/dist'))
with tempfile.TemporaryDirectory(prefix='amap-m42-') as folder:
    os.environ['DATABASE_PATH']=str(Path(folder)/'test.sqlite3')
    os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'deletions.sqlite3')
    from app.main import app
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8023,log_level='warning',access_log=False)
