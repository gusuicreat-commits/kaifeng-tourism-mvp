"""Network and AMap protocol tests: transport substitutes only, no real HTTP requests."""
import json,math
from dataclasses import replace
from datetime import datetime,timedelta
from pathlib import Path
import httpx
import networkx as nx
import pytest
from app.city import facts
from app.city.engine.amap import AmapProvider
from app.city.engine.context import PlanningContext
from app.city.engine.models import OperationalBundle,Requirements
from app.city.engine.network import VerifiedWalkingNetwork
from app.city.engine.tools import plan_trip


def sample():
    d=json.loads((Path(__file__).parent/'fixtures/engine_synthetic.json').read_text())
    records=facts.FactBundle.model_validate(d['facts']).records
    ctx=PlanningContext({(r.kind,r.fact_id):r for r in records},OperationalBundle.model_validate(d['operations']),datetime.fromisoformat(d['assessed_at']),True)
    return Requirements.model_validate(d['requirements']),ctx


def test_networkx_uses_only_directed_supported_edges_and_never_fabricates_reverse():
    req,ctx=sample();g=nx.DiGraph()
    for pair in [('origin','museum'),('museum','hotel')]:
        j=next(j for j in ctx.operations.journeys if (j.travel.origin_place_id,j.travel.destination_place_id,j.travel.mode)==(*pair,'walking'))
        g.add_edge(*pair,journey=j)
    net=VerifiedWalkingNetwork(g,ctx);route=net.journey('origin','hotel',req)
    assert route and route.travel.upper_seconds==sum(g[a][b]['journey'].travel.upper_seconds for a,b in g.edges)
    assert net.journey('hotel','origin',req) is None
    g['museum']['hotel']['journey']=g['museum']['hotel']['journey'].model_copy(update={'travel':g['museum']['hotel']['journey'].travel.model_copy(update={'path_status':'unknown'})})
    assert net.journey('origin','hotel',req) is None


@pytest.mark.parametrize('mode',['walking','driving','transit'])
def test_amap_quotes_parse_units_preserve_cost_unknown_and_do_not_claim_bounds(mode):
    req,ctx=sample();seen=[]
    def handler(request):
        seen.append(request)
        assert request.url.params['key']=='test-only-key'
        assert request.extensions['timeout']['read']==2
        key='transits' if mode=='transit' else 'paths'
        return httpx.Response(200,json={'status':'1','route':{key:[{'duration':'600','distance':'800','cost':'2.5'}]}})
    p=AmapProvider('test-only-key',enabled=True,terms_reviewed=True,transport=httpx.MockTransport(handler))
    quote=p.estimate(ctx.facts['place','origin'],ctx.facts['place','hotel'],mode,req.starts_at,ctx.assessed_at);p.close()
    assert quote.status=='estimated' and quote.duration_seconds==600 and quote.distance_m==800
    assert quote.cost_fen==({'walking':0,'driving':None,'transit':250}[mode])
    assert 'not a guaranteed' in quote.uncertainty
    if mode=='transit':assert seen[0].url.params['city']=='开封' and seen[0].url.params['date']=='2030-05-01'


@pytest.mark.parametrize('failure,expected',[('no_key','disabled'),('terms','disabled'),('no_route','no_route'),
    ('429','provider_rate_limited'),('auth','authorization_or_provider_error'),('malformed','invalid_response'),
    ('zero','invalid_response'),('nan','invalid_response'),('timeout','timeout_or_network'),('server','provider_unavailable')])
def test_amap_failures_never_return_zero_minutes(failure,expected):
    req,ctx=sample();counter=[0];clock=[0]
    def handler(request):
        counter[0]+=1
        if failure=='timeout':raise httpx.ReadTimeout('secret must not escape')
        if failure=='server':return httpx.Response(503)
        if failure=='429':return httpx.Response(429)
        if failure=='auth':return httpx.Response(200,json={'status':'0','info':'private provider message'})
        if failure=='malformed':return httpx.Response(200,text='not json')
        if failure=='no_route':return httpx.Response(200,json={'status':'1','route':{'paths':[]}})
        return httpx.Response(200,json={'status':'1','route':{'paths':[{'duration':'nan' if failure=='nan' else '0','distance':'0'}]}})
    def sleep(_):clock[0]+=2
    p=AmapProvider('' if failure=='no_key' else 'test-key',enabled=True,terms_reviewed=failure!='terms',
                   transport=httpx.MockTransport(handler),clock=lambda:clock[0],sleeper=sleep)
    quote=p.estimate(ctx.facts['place','origin'],ctx.facts['place','hotel'],'walking',req.starts_at,ctx.assessed_at);p.close()
    assert quote.status==expected and quote.duration_seconds is None
    assert 'secret' not in repr(quote) and 'private provider' not in repr(quote)
    assert counter[0]<=2


def test_amap_rate_request_limits_and_coordinate_mismatch():
    req,ctx=sample();count=[0]
    def handler(r):count[0]+=1;return httpx.Response(200,json={'status':'1','route':{'paths':[{'duration':'10','distance':'20'}]}})
    p=AmapProvider('test-key',enabled=True,terms_reviewed=True,transport=httpx.MockTransport(handler),clock=lambda:0,max_requests=2)
    args=(ctx.facts['place','origin'],ctx.facts['place','hotel'],'walking',req.starts_at,ctx.assessed_at)
    assert p.estimate(*args).status=='estimated'
    assert p.estimate(*args).status=='local_rate_limited' and count[0]==1
    bad=args[0].model_copy(update={'coordinate_system':'WGS84'})
    assert p.estimate(bad,*args[1:]).status=='coordinate_mismatch';p.close()


def test_operational_envelope_must_cover_entire_horizon_and_mode():
    req,ctx=sample()
    ops=ctx.operations.model_copy(update={'journeys':tuple(j.model_copy(update={'travel':j.travel.model_copy(update={'evidence':j.travel.evidence.model_copy(update={'valid_until':req.starts_at+timedelta(minutes=10)})})}) for j in ctx.operations.journeys)})
    assert plan_trip(req,replace(ctx,operations=ops)).status=='needs_data'


def test_multi_day_dated_matrix_is_reused_across_objectives_without_provider(monkeypatch):
    from test_city_engine import multi_sample
    from app.city.engine import multi_day
    from app.city.engine.travel import TravelTimeService
    req,ctx=multi_sample(sample(),goals=[]);seen=[];original=TravelTimeService.matrix
    def matrix(service,day,activities):
        seen.append((id(service),day.business_date,day.starts_at,day.ends_at))
        return original(service,day,activities)
    monkeypatch.setattr(TravelTimeService,'matrix',matrix)
    # The independent validator prepares its own candidates; only record assignment preparation here.
    real_validate=__import__('app.city.engine.multi_day_validation',fromlist=['validate_multi_day']).validate_multi_day
    def validate(*args):
        size=len(seen);result=real_validate(*args);del seen[size:];return result
    monkeypatch.setattr('app.city.engine.multi_day_validation.validate_multi_day',validate)
    result=multi_day.plan_multi_day(req,ctx)
    assert result.status=='feasible' and len(seen)==2
    assert len({row[0] for row in seen})==1
    assert [row[1] for row in seen]==[d.business_date for d in req.days]
    assert result.metrics['provider_requests']==0 and result.metrics['travel_elapsed_ms']>=0
    journeys=tuple(j.model_copy(update={'travel':j.travel.model_copy(update={'evidence':
        j.travel.evidence.model_copy(update={'valid_until':req.days[0].ends_at+timedelta(seconds=1)})})}) for j in ctx.operations.journeys)
    result=multi_day.plan_multi_day(req,replace(ctx,operations=ctx.operations.model_copy(update={'journeys':journeys})))
    assert result.status=='needs_data' and not result.itineraries
    assert any('day:2030-05-02' in issue.affected_ids for issue in result.issues)



def test_live_driving_journeys_keep_vehicle_costs_out_of_per_person_costs(monkeypatch):
    from app.city.engine import live_travel
    req,ctx=sample()
    records={k:r.model_copy(update={'is_demo':False}) for k,r in ctx.facts.items()}
    records['source',live_travel.SOURCE_ID]=records['source','fixture:source'].model_copy(update={'fact_id':live_travel.SOURCE_ID})
    ctx=replace(ctx,facts=records,synthetic=False,operations=ctx.operations.model_copy(update={'journeys':()}))
    req=req.model_copy(update={'modes':('driving','walking'),'goals':()})
    monkeypatch.setenv('AMAP_PLANNING_ENABLED','1')
    def handler(request):
        return httpx.Response(200,json={'status':'1','route':{'taxi_cost':'15','paths':[
            {'distance':'800','cost':{'duration':'600','tolls':'2'},'steps':[{'polyline':'114.3,34.8;114.31,34.81'}]}]}})
    ticks=iter(range(100,500,2))
    p=AmapProvider('test-only-key',enabled=True,terms_reviewed=True,api_version='v5',retries=0,
                   transport=httpx.MockTransport(handler),clock=lambda:next(ticks))
    p.interval=0
    try:enriched=live_travel.enrich(ctx,req,provider=p)
    finally:p.close()
    driving=[j for j in enriched.operations.journeys if j.travel.mode=='driving' and j.estimated_seconds]
    assert driving
    for j in driving:
        assert j.vehicle_cost_fen==1500 and j.tolls_fen==200 and j.vehicle_cost_source=='amap_taxi_estimate'
        assert j.cost_lower_fen is None and j.cost_upper_fen is None
    same_point=False
    for j in enriched.operations.journeys:
        if j.travel.mode=='driving' and not j.estimated_seconds:
            # Same point: no ride, so the fare is known to be zero rather than unknown.
            assert j.vehicle_cost_fen==0 and j.tolls_fen==0 and j.vehicle_cost_source is None
            same_point=True
        elif j.travel.mode!='driving':
            assert j.vehicle_cost_fen is None and j.tolls_fen is None and j.vehicle_cost_source is None
    assert same_point


@pytest.mark.parametrize('budget',[None,0,100000])
def test_vehicle_costs_do_not_change_solver_known_cost_or_hard_budget(budget):
    req,ctx=sample();req=req.model_copy(update={'modes':('driving',),'budget_fen':budget,'alternatives':1})
    journeys=tuple(j.model_copy(update={'travel':j.travel.model_copy(update={'mode':'driving'}),
                                      'cost_lower_fen':None,'cost_upper_fen':None})
                   for j in ctx.operations.journeys if j.travel.mode=='walking')
    plain=replace(ctx,operations=ctx.operations.model_copy(update={'journeys':journeys}))
    with_cost=replace(ctx,operations=ctx.operations.model_copy(update={'journeys':tuple(
        j.model_copy(update={'vehicle_cost_fen':999999,'tolls_fen':99999,'vehicle_cost_source':'amap_taxi_estimate'}) for j in journeys)}))
    before=plan_trip(req,plain);after=plan_trip(req,with_cost)
    assert before.status==after.status
    assert [i.known_cost_upper_fen for i in before.itineraries]==[i.known_cost_upper_fen for i in after.itineraries]
    assert [i.cost_unknown for i in before.itineraries]==[i.cost_unknown for i in after.itineraries]
    assert [i.code for i in before.issues]==[i.code for i in after.issues]
    if budget is None:assert before.status=='feasible' and before.itineraries
    else:assert not before.itineraries
