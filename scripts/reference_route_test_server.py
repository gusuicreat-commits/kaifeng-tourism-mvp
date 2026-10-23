"""Synthetic HTTP protocol only, on an isolated temporary DB. Never loads credentials."""
import os
from pathlib import Path
import sys
import tempfile
import httpx
import uvicorn
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.environ['TOURISM_LLM_API_KEY']=''
os.environ['AMAP_LOCATION_SEARCH_ENABLED']='1'
os.environ['AMAP_TERMS_REVIEWED']='1'
os.environ['AMAP_REFERENCE_ROUTES_ENABLED']='1'
from app.city import location_resolution as locations

def handle(request):
    if '/place/' in request.url.path:
        query=request.url.params['keywords']
        if query=='API故障站':return httpx.Response(503,json={})
        rows=[{'id':query+':1','name':query,'location':'114.3,34.8','adcode':'410202','citycode':'0371','address':'[合成测试]南侧地址'}]
        if query=='模糊站':rows.append(rows[0]|{'id':query+':2','location':'114.31,34.81','address':'[合成测试]北侧地址'})
        return httpx.Response(200,json={'status':'1','infocode':'10000','pois':rows})
    return httpx.Response(200,json={'status':'1','infocode':'10000','route':{'paths':[{'distance':'1234','cost':{'duration':'720'},'steps':[{'instruction':'[合成测试]沿测试路前行'}]}]}})

locations.provider_from_environment=lambda:locations.amap.AmapProvider('SYNTHETIC_PROTOCOL_KEY',enabled=True,terms_reviewed=True,
    api_version='v5',retries=0,transport=httpx.MockTransport(handle))
with tempfile.TemporaryDirectory(prefix='reference-route-browser-') as folder:
    os.environ['DATABASE_PATH']=str(Path(folder)/'review.sqlite3')
    os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'deletions.sqlite3')
    uvicorn.run('app.main:app',host='127.0.0.1',port=int(os.getenv('E2E_REFERENCE_PORT','8050')),access_log=False,log_level='warning')
