import csv
import io
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2]/'.env')

from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from .runtime import STARTUP
from .db import connect, scenic_list, scenic_get, network_get
from .seed import seed
from .crowds import crowd_summary
from .importer import import_csv
from .models import PlanRequest, RouteRequest, Dataset
from .planner import plan, point_route
from .speech import router as speech_router
from .language import router as language_router
from .schedules import facts_for, TZ
from datetime import date
from .memory.api import router as memory_router, retention_worker
from .memory.storage import init_memory, maintenance
from .memory.session import authorize_plan, ensure, recheck
from .city.api import router as city_router
from .city.engine.api import router as city_engine_router
from .city.assistant.api import router as city_assistant_router
from .city.agent.api import router as city_agent_router
from .city.weather import router as city_weather_router
from .city.exploration import router as exploration_router
from .maps import router as maps_router
import asyncio
from contextlib import suppress


@asynccontextmanager
async def lifespan(app):
    seed()
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, result TEXT NOT NULL)')
    init_memory()
    try:maintenance()
    except HTTPException:
        pass  # Scenic facts remain available; M7 authorization fails closed until ledger repair.
    worker=asyncio.create_task(retention_worker())
    try:yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):await worker


app=FastAPI(title='汴梁游记 · 开封城市旅游助手',version='0.1.0',lifespan=lifespan)
app.include_router(speech_router)
app.include_router(language_router)
app.include_router(memory_router)
app.include_router(city_router)
app.include_router(city_engine_router)
app.include_router(city_assistant_router)
app.include_router(city_agent_router)
app.include_router(city_weather_router)
from .city.travel_services import router as travel_services_router
app.include_router(travel_services_router)
app.include_router(exploration_router)
app.include_router(maps_router)


@app.middleware('http')
async def memory_boundary(request,call_next):
    if request.url.path.startswith(('/api/v1/','/api/city/','/api/plans','/api/ai/','/api/maps/')):
        origin=request.headers.get('origin')
        if request.method not in ('GET','HEAD','OPTIONS') and origin:
            from urllib.parse import urlsplit
            supplied=urlsplit(origin)
            if supplied.netloc!=request.url.netloc or supplied.scheme!=request.url.scheme:
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail':'不允许跨站修改个人行程。'},status_code=403)
    try:response=await call_next(request)
    except __import__('sqlite3').Error:
        from fastapi.responses import JSONResponse
        response=JSONResponse({'detail':'数据保存暂不可用，未确认的修改没有生效，请重试。'},status_code=503)
    if request.url.path.startswith(('/api/v1/','/api/city/','/api/plans','/api/ai/','/api/maps/')):
        response.headers['Cache-Control']='no-store, private'
        response.headers['Pragma']='no-cache'
    return response


def require_scenic(sid):
    s=scenic_get(sid)
    if not s:
        raise HTTPException(404,detail='景区不存在')
    return s


@app.get('/api/health')
def health():
    with connect() as db:
        version=db.execute('SELECT version FROM schema_version').fetchone()[0]
    return {'status':'ok','schema_version':version,'realtime_monitoring':False,
            'runtime':STARTUP}


@app.get('/api/scenics')
def scenics(q:str=Query(default='',max_length=80)):
    return {'items':scenic_list(q.strip())}


@app.get('/api/scenics/{sid}')
def scenic(sid:str):
    return require_scenic(sid)


@app.get('/api/scenics/{sid}/network')
def network(sid:str):
    require_scenic(sid)
    net=network_get(sid)
    if not net:
        raise HTTPException(404,detail='该景区内部地图尚待核实，暂不支持内部路线')
    return net


@app.get('/api/scenics/{sid}/crowds')
def crowds(sid:str,dataset:Dataset='demo',hour:int=Query(13,ge=0,le=23),target_time:datetime|None=None):
    require_scenic(sid)
    if target_time:
        if target_time.tzinfo is None or target_time.utcoffset() is None:
            raise HTTPException(422, detail='目标时间必须包含时区')
        target_time=target_time.astimezone(TZ)
        hour=target_time.hour
    return crowd_summary(sid,dataset,hour,target_time=target_time)


@app.get('/api/scenics/{sid}/schedules')
def schedules(sid:str,date:date,dataset:Dataset='reported'):
    require_scenic(sid)
    return {'items':[f.model_dump(mode='json') for f in facts_for(sid,date,dataset)]}


@app.post('/api/plans',status_code=201)
def create_plan(body:PlanRequest,request:Request,response:Response):
    require_scenic(body.scenic_id)
    session=ensure(request,response)
    result=plan(body)
    result['id']=uuid.uuid4().hex
    result['created_at']=datetime.now(timezone.utc).isoformat()
    with connect() as db:
        recheck(db,session)
        db.execute('INSERT INTO plans VALUES (?,?,?)',(result['id'],result['created_at'],json.dumps(result,ensure_ascii=False)))
        db.execute('INSERT INTO m7_plan_owners VALUES (?,?,NULL)',(result['id'],session.id))
    return result


@app.get('/api/plans/{plan_id}')
def get_plan(plan_id:str,request:Request,response:Response):
    with connect() as db:
        authorize_plan(db,plan_id,request,response)
        row=db.execute('SELECT result FROM plans WHERE id=?',(plan_id,)).fetchone()
    if not row:
        raise HTTPException(404,detail='路线不存在，请重新规划')
    return json.loads(row['result'])


@app.post('/api/routes')
def route(body:RouteRequest):
    require_scenic(body.scenic_id)
    return point_route(body)


@app.post('/api/crowds/import')
async def upload(file:UploadFile=File(...)):
    content=await file.read(1024*1024+1)
    await file.close()
    if len(content)>1024*1024:
        raise HTTPException(413,detail='CSV不能超过1 MiB')
    return import_csv(content)


@app.get('/api/crowds/template')
def template():
    content='scenic_id,node_id,observed_at,period_end,count,crowd_level,capacity,source_type,source_name,source_url,measurement_kind,confidence,is_simulated\nwansui,theatre,2026-10-01T13:00:00+08:00,,120,0.8,,field,团队导入演示样本,,observation,0.7,true\n'
    return Response(content,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="crowd-template.csv"'})


# Built frontend is served by the same origin. Dev mode uses Vite's /api proxy.
DIST=Path(os.environ.get('TOURISM_FRONTEND_DIST',str(Path(__file__).resolve().parents[2]/'frontend'/'dist')))
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')
    if (DIST/'images').exists():
        app.mount('/images',StaticFiles(directory=DIST/'images'),name='images')

    @app.get('/audio-recorder.js',include_in_schema=False)
    def audio_recorder():
        return FileResponse(DIST/'audio-recorder.js',media_type='application/javascript')

    @app.get('/{path:path}',include_in_schema=False)
    def frontend(path:str):
        if path.startswith('api/'):
            raise HTTPException(404,detail='API不存在')
        return FileResponse(DIST/'index.html')
