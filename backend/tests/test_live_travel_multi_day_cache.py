"""Request-local quote reuse with synthetic facts and counting providers only."""
from dataclasses import replace
from datetime import timedelta, timezone

import pytest
from app.city import facts
from app.city.engine import live_travel, service
from app.city.engine.amap import Quote
from app.city.engine.models import MultiDayRequirements
from app.city.engine.storage import import_operations
from app.db import connect
from scripts.benchmark_city_engine import case
from test_city_engine import multi_sample
from test_city_engine_api import rid
from test_multi_day_travel import daily


class Provider:
    interval=.25
    def __init__(self,*,max_requests=60,failed=False):
        self.max_requests=max_requests;self.request_count=0;self.failed=failed;self.calls=[];self.closed=0
    def estimate(self,origin,destination,mode,departure,checked):
        assert self.request_count<self.max_requests
        self.request_count+=1
        self.calls.append((origin.longitude,origin.latitude,destination.longitude,destination.latitude,mode,departure))
        return Quote('timeout_or_network') if self.failed else Quote('estimated',duration_seconds=60,
            distance_m=80,geometry_available=True,taxi_cost_fen=1200,tolls_fen=100)
    def close(self):self.closed+=1


@pytest.fixture
def trip(monkeypatch):
    req,ctx=multi_sample(case(5),3,alternatives=1)
    data=req.model_dump()
    for d in data['days']:d.update(origin_place_id='origin',destination_place_id='origin')
    for o in data['overnights']:o['place_id']='origin'
    for g in data['goals']:g['required']=True
    records={k:r.model_copy(update={'is_demo':False}) for k,r in ctx.facts.items()}
    for i in range(5):
        key=('place',f'candidate:{i}')
        records[key]=records[key].model_copy(update={'longitude':114.31+i*.001})
    records['source',live_travel.SOURCE_ID]=records['source','fixture:source'].model_copy(update={'fact_id':live_travel.SOURCE_ID})
    costs=tuple(c.model_copy(update={'evidence':c.evidence.model_copy(update={'kind':'verified'})}) for c in ctx.operations.costs)
    ctx=replace(ctx,facts=records,synthetic=False,operations=ctx.operations.model_copy(update={'journeys':(),'costs':costs}))
    monkeypatch.setenv('AMAP_PLANNING_ENABLED','1')
    sleeps=[];monkeypatch.setattr(live_travel.time,'sleep',sleeps.append)
    return MultiDayRequirements.model_validate(data),ctx,sleeps


def day_journeys(ctx,req,day):
    return [j for j in ctx.operations.journeys if live_travel.applies_to_day(j,daily(req,day))]


@pytest.mark.parametrize('modes',[('walking',),('walking','driving')])
def test_same_time_reuses_quotes_at_budget_limit_and_keeps_daily_snapshots(trip,caplog,modes):
    req,ctx,sleeps=trip;req=req.model_copy(update={'modes':modes})
    single=Provider();live_travel.enrich(ctx,daily(req,req.days[0]),provider=single)
    sleeps.clear();provider=Provider(max_requests=single.request_count)
    with caplog.at_level('INFO',logger=live_travel.__name__):
        enriched=live_travel.enrich(ctx,req,provider=provider)
    assert provider.request_count==single.request_count==30*len(modes)
    assert sleeps==[provider.interval]*(provider.request_count-1)
    assert f'provider_requests={provider.request_count} cache_hits={2*provider.request_count}' in caplog.text
    identities=set()
    for day in req.days:
        journeys=day_journeys(enriched,req,day)
        assert len(journeys)==36*len(modes)
        assert all(j.journey_id.startswith(f'amap:day:{day.business_date}:') for j in journeys)
        assert not identities.intersection(j.journey_id for j in journeys)
        identities.update(j.journey_id for j in journeys)
        assert all(j.travel.evidence.valid_until>day.ends_at for j in journeys)
        assert all(j.vehicle_cost_fen==1200 and j.tolls_fen==100 for j in journeys
                   if j.travel.mode=='driving' and j.estimated_seconds)
    assert provider.closed==0


def test_different_departure_time_requests_another_set(trip,caplog):
    req,ctx,_=trip;days=list(req.days)
    days[1]=days[1].model_copy(update={'starts_at':days[1].starts_at+timedelta(minutes=30)})
    req=req.model_copy(update={'days':tuple(days)});provider=Provider()
    with caplog.at_level('INFO',logger=live_travel.__name__):
        enriched=live_travel.enrich(ctx,req,provider=provider)
    assert provider.request_count==60
    assert [call[-1].date() for call in provider.calls]==[req.days[0].business_date]*30+[req.days[1].business_date]*30
    assert all(len(day_journeys(enriched,req,day))==36 for day in req.days)
    assert 'provider_requests=60 cache_hits=30' in caplog.text


def test_cache_key_uses_beijing_hour_and_minute(trip):
    req,ctx,_=trip;days=list(req.days)
    days[1]=days[1].model_copy(update={'starts_at':days[1].starts_at.astimezone(timezone.utc)+timedelta(seconds=25)})
    req=req.model_copy(update={'days':tuple(days)});provider=Provider()
    enriched=live_travel.enrich(ctx,req,provider=provider)
    assert provider.request_count==30
    assert all(len(day_journeys(enriched,req,day))==36 for day in req.days)


def test_failed_quotes_are_cached_without_creating_usable_legs(trip,caplog):
    req,ctx,sleeps=trip;provider=Provider(failed=True)
    with caplog.at_level('INFO',logger=live_travel.__name__):
        enriched=live_travel.enrich(ctx,req,provider=provider)
    assert provider.request_count==30 and len(sleeps)==29
    assert 'provider_requests=30 cache_hits=60' in caplog.text
    for day in req.days:
        journeys=day_journeys(enriched,req,day)
        assert len(journeys)==6
        assert all(j.travel.origin_place_id==j.travel.destination_place_id for j in journeys)


def test_single_day_and_uncached_dated_calls_keep_request_counts(trip):
    req,ctx,sleeps=trip;provider=Provider();day=daily(req,req.days[0])
    enriched=live_travel.enrich(ctx,day,provider=provider)
    assert provider.request_count==30 and len(sleeps)==29
    assert len(enriched.operations.journeys)==36
    assert all(not j.journey_id.startswith('amap:day:') for j in enriched.operations.journeys)
    provider=Provider()
    for d in req.days[:2]:live_travel._enrich_day(ctx,daily(req,d),provider=provider,dated=True)
    assert provider.request_count==60


def test_quotes_do_not_survive_an_enrich_call_and_request_log_is_per_call(trip,caplog):
    req,ctx,_=trip;provider=Provider()
    with caplog.at_level('INFO',logger=live_travel.__name__):
        live_travel.enrich(ctx,req,provider=provider)
        live_travel.enrich(ctx,req,provider=provider)
    assert provider.request_count==60
    assert caplog.text.count('provider_requests=30 cache_hits=60')==2


def test_three_day_preview_with_five_places_fits_sixty_request_budget(trip,client,monkeypatch):
    req,ctx,_=trip
    with connect() as db:
        facts.import_bundle(facts.FactBundle(records=tuple(ctx.facts.values())),db,apply=True)
        import_operations(db,ctx.operations,facts.records(db),apply=True)
    monkeypatch.setattr(service,'now',lambda:ctx.assessed_at)
    client.post('/api/v1/sessions')
    t=client.post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    def preview():
        response=client.post(f"/api/city/v1/trips/{t['id']}/engine/preview",json={
            'request_id':rid(),'expected_version':t['version'],'requirements':req.model_dump(mode='json')})
        assert response.status_code==200,response.text
        return response.json()
    # Before E12 each day needs 30 requests; the third day's missing arcs exhaust
    # the shared 60-request budget and prevent a complete three-day preview.
    original=live_travel._enrich_day;uncached=Provider()
    def without_cache(*args,**kwargs):
        kwargs.pop('quotes',None);kwargs.pop('metrics',None)
        return original(*args,**kwargs)
    with monkeypatch.context() as old:
        old.setattr(live_travel,'_enrich_day',without_cache)
        old.setattr(live_travel,'provider_from_environment',lambda:uncached)
        before=preview()
    assert uncached.request_count==60 and uncached.closed==1
    assert before['result']['status']!='feasible' and not before['result']['itineraries']
    provider=Provider();monkeypatch.setattr(live_travel,'provider_from_environment',lambda:provider)
    after=preview()
    assert after['status']=='preview' and after['result']['status']=='feasible',after
    assert provider.request_count==30 and provider.closed==1
    for plan in after['result']['itineraries']:
        assert len(plan['days'])==3 and all(d['itinerary']['activities'] for d in plan['days'])
        assert sorted(a['goal_id'] for d in plan['days'] for a in d['itinerary']['activities'])==sorted(g.goal_id for g in req.goals)
