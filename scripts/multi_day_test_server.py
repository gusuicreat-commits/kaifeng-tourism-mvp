"""B2.5 disposable browser fixture. No live model, map, weather or travel providers."""
import copy,json,os,sys,tempfile
from datetime import datetime,timedelta
from pathlib import Path
from uuid import UUID,uuid4
from fastapi import Depends,Body

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
for key in ('TOURISM_LLM_API_KEY','QWEATHER_API_KEY','AMAP_API_KEY','FLYAI_API_KEY'):os.environ[key]=''
for key in ('AMAP_PLANNING_ENABLED','QWEATHER_ENABLED'):os.environ[key]='0'
os.environ['M7_SESSION_RATE_LIMIT']='10000'
os.environ.setdefault('TOURISM_FRONTEND_DIST',str(ROOT/'frontend/dist'))

with tempfile.TemporaryDirectory(prefix='multi-day-browser-') as folder:
    os.environ['DATABASE_PATH']=str(Path(folder)/'synthetic.sqlite3')
    os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'deletion.sqlite3')
    from app.main import app
    from app.seed import seed
    from app.memory.storage import init_memory
    from app.memory import storage,session
    from app.city import facts,state,exploration_state,weather,hotels,hotel_binding,travel_services
    from app.city.assistant import context,service,tools
    from app.city.engine import service as engine
    from app.city.engine.models import OperationalBundle,PreviewRequestV2,ReplanRequestV2,ConfirmRequest,MultiDayConstraints
    from app.city.engine.storage import import_operations
    from app.city.state_schema import CreateCityTripV2
    from app.city.agent import provider
    from app.db import connect
    from multi_day_browser_fixture import browser_requirements,dated_records
    import uvicorn

    clock=[datetime.fromisoformat('2030-05-01T08:00:00+08:00')]
    for module in (storage,session,state,context,service,tools,engine,weather):module.now=lambda:clock[0]
    def no_provider(*args,**kwargs):raise AssertionError('Live provider forbidden in multi-day browser tests')
    provider.generate=no_provider
    hotels.search=lambda query:hotels.normalize({'status':0,'data':{'itemList':[
        {'shId':'browser-hotel','name':'[合成测试]hotel','address':'隔离测试地址','price':'¥188'}]}},checked_at=clock[0])
    seed();init_memory()
    data=json.loads((ROOT/'backend/tests/fixtures/engine_synthetic.json').read_text())
    for record in data['facts']['records']:record['is_demo']=False
    for journey in data['operations']['journeys']:
        journey['travel']['evidence']['kind']='verified';journey['travel']['path_status']='verified';journey['time_basis']='survey_envelope'
    for cost in data['operations']['costs']:
        cost['evidence']['kind']='verified';cost['unit']='per_person'
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(data['facts']),db,apply=True)
        facts.import_bundle(facts.FactBundle(records=dated_records(data)),db,apply=True)
        import_operations(db,OperationalBundle.model_validate(data['operations']),facts.records(db),apply=True)

    @app.post('/api/e2e/multi-day',include_in_schema=False)
    def setup(body:dict=Body(default={}),s:session.Session=Depends(session.current)):
        clock[0]=datetime.fromisoformat('2030-05-01T08:00:00+08:00')
        req=browser_requirements()
        if body.get('kind') in ('hotel','budget'):
            req['days'][0]['destination_place_id']='origin'
            req['days'][1]['origin_place_id']='origin'
            req['days'][1]['destination_place_id']='origin'
            req['overnights'][0]['place_id']='origin'
        if body.get('kind')=='budget':req['pricing']={'party_size':2,'total_budget_fen':100000,'dining_per_person_day_fen':3000}
        if body.get('kind')=='taxi':
            req['modes']=['driving'];req['objective']='lower_cost';req['solve_seconds']=3
            req['pricing']={'party_size':5,'vehicle_count':2,'total_budget_fen':100000,'dining_per_person_day_fen':1000}
            journeys=[]
            for original in data['operations']['journeys']:
                for suffix,price,extra in (('fast',1000,0),('cheap',200,600)):
                    item=copy.deepcopy(original);item['journey_id']='taxi:'+original['journey_id']+':'+suffix
                    item['travel']['mode']='driving';item['vehicle_cost_fen']=price;item['cost_unit']='per_person'
                    item['vehicle_cost_source']='amap_taxi_estimate'
                    item['travel']['lower_seconds']+=extra;item['travel']['upper_seconds']+=extra
                    journeys.append(item)
            with connect() as db:import_operations(db,OperationalBundle(journeys=journeys),facts.records(db),apply=True)
        draft={'schema_version':'city-draft/2','start_date':req['start_date'],'end_date':req['end_date'],
            'days':[{**d,'stops':[]} for d in req['days']],'overnights':req['overnights']}
        trip=state.create(s,CreateCityTripV2.model_validate({'request_id':str(uuid4()),'draft':draft}))
        identity=UUID(trip['id'])
        constraints=MultiDayConstraints.model_validate({k:v for k,v in req.items() if k not in ('schema_version','solve_seconds','alternatives')}|{'schema_version':'city-constraints/2'}).model_dump(mode='json')
        with connect() as db:exploration_state.save(db,identity,constraints,None,0)
        preview=engine.preview(s,identity,PreviewRequestV2.model_validate({'request_id':str(uuid4()),
            'expected_version':trip['version'],'requirements':req}),planning_version=1)
        # Seed a readable workspace snapshot without exercising the separate exploration writer.
        # Its V2 version synchronization is a backend gap reported by B2.5, not bypassed in production.
        with connect() as db:
            db.execute('UPDATE city_exploration_states SET result_json=? WHERE trip_id=?',
                (json.dumps(preview|{'planning_version':1,'rounds':0,'basket_version':0,'trip_version':trip['version']}),trip['id']))
        if body.get('kind') in ('hotel','budget'):
            with connect() as db:
                hotel_binding.import_binding(db,{'candidate_id':'flyai:browser-hotel','place_id':'hotel',
                    'revision':1,'source':'isolated synthetic browser fixture',
                    'checked_at':'2020-01-01T00:00:00+08:00','valid_from':'2020-01-01T00:00:00+08:00',
                    'valid_until':'2030-06-01T00:00:00+08:00'})
            travel_services.query(s,identity,'找酒店 2030-05-01 入住，住1晚',0)
        if body.get('kind')=='ongoing':
            confirmed=engine.confirm_itinerary(s,identity,ConfirmRequest.model_validate({'request_id':str(uuid4()),
                'expected_version':trip['version'],'candidate_id':preview['candidate_id'],'alternative_index':0}))
            activity=confirmed['itinerary']['days'][0]['itinerary']['activities'][0]
            clock[0]=datetime.fromisoformat(activity['starts_at'])+timedelta(minutes=1)
            with connect() as db:
                db.execute("UPDATE city_trip_states SET status='ACTIVE',anchor_json=? WHERE id=?",(json.dumps({
                    'place_id':activity['place_id'],'confirmed_at':clock[0].isoformat(),'source':'manual_known_place'}),trip['id']))
            preview=engine.preview(s,identity,ReplanRequestV2.model_validate({'request_id':str(uuid4()),
                'expected_version':confirmed['version'],'requirements':req,'ongoing':{'stop_id':activity['goal_id'],'remaining_seconds':1200}}),replanning=True)
            engine.confirm_itinerary(s,identity,ConfirmRequest.model_validate({'request_id':str(uuid4()),
                'expected_version':confirmed['version'],'candidate_id':preview['candidate_id'],'alternative_index':0}))
        return {'trip_id':trip['id'],'moment':clock[0].isoformat(),'requirements':req,'preview':preview}

    @app.post('/api/e2e/budget-start/{trip_id}',include_in_schema=False)
    def start_budget(trip_id:UUID,s:session.Session=Depends(session.current)):
        with connect() as db:
            row=state.owned(db,s,trip_id);route=engine.route_row(db,trip_id)
            plan=json.loads(route['itinerary_json']);activity=plan['days'][0]['itinerary']['activities'][0]
            clock[0]=datetime.fromisoformat(activity['starts_at'])+timedelta(minutes=1)
            db.execute("UPDATE city_trip_states SET status='ACTIVE',anchor_json=? WHERE id=?",(json.dumps({
                'place_id':activity['place_id'],'confirmed_at':clock[0].isoformat(),'source':'manual_known_place'}),str(trip_id)))
        return {'moment':clock[0].isoformat()}

    uvicorn.run(app,host='127.0.0.1',port=int(os.getenv('E2E_MULTI_DAY_PORT','8055')),log_level='warning')
