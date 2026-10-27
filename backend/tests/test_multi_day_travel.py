"""Dated provider snapshots use only an in-memory transport substitute."""
import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest
from app.city.engine import live_travel
from app.city.engine.amap import Quote
from app.city.engine.models import MultiDayRequirements, MultiDayItinerary, ScheduledLeg
from app.city.engine.multi_day import applicable, day_requirements
from multi_day_helpers import requirements, itinerary
from test_city_engine_travel import sample


class Provider:
    interval=0
    def __init__(self,limit=60,ticks=None):
        self.max_requests=limit;self.request_count=0;self.closed=0;self.days=[];self.ticks=ticks
    def estimate(self,origin,destination,mode,departure,checked):
        self.request_count+=1;self.days.append(departure.date())
        if self.ticks is not None:self.ticks[0]+=1
        return Quote('estimated',duration_seconds=60,distance_m=80,geometry_available=True)
    def close(self):self.closed+=1


@pytest.fixture
def dated(monkeypatch):
    _,ctx=sample();records={k:r.model_copy(update={'is_demo':False}) for k,r in ctx.facts.items()}
    records['source',live_travel.SOURCE_ID]=records['source','fixture:source'].model_copy(update={'fact_id':live_travel.SOURCE_ID})
    ctx=replace(ctx,facts=records,synthetic=False,operations=ctx.operations.model_copy(update={'journeys':()}))
    data=requirements(3)
    for d in data['days']:d.update(origin_place_id='origin',destination_place_id='origin')
    for o in data['overnights']:o['place_id']='origin'
    data['goals']=[{'goal_id':'museum','target_kind':'place','target_id':'museum',
                    'allowed_dates':[d['business_date'] for d in data['days']]}]
    monkeypatch.setenv('AMAP_PLANNING_ENABLED','1')
    return MultiDayRequirements.model_validate(data),ctx


def daily(req,day):
    return day_requirements(req,day,[g for g in req.goals if applicable(g,day)],req.objective,req.solve_seconds)


def test_daily_quotes_keep_same_pair_separate_and_cannot_apply_to_other_days(dated):
    req,ctx=dated;provider=Provider();enriched=live_travel.enrich(ctx,req,provider=provider)
    pair=[j for j in enriched.operations.journeys if (j.travel.origin_place_id,j.travel.destination_place_id)==('origin','museum')]
    assert len(pair)==3 and len({j.journey_id for j in pair})==3
    assert provider.days==[d.business_date for d in req.days for _ in range(2)]
    for index,day in enumerate(req.days):
        assert [live_travel.applies_to_day(j,daily(req,day)) for j in pair]==[index==i for i in range(3)]
    assert provider.closed==0


def test_same_day_quote_revision_and_window_have_distinct_snapshot_identity(dated):
    req,ctx=dated
    first=live_travel.enrich(ctx,req,provider=Provider())
    later=live_travel.enrich(replace(ctx,assessed_at=ctx.assessed_at+timedelta(seconds=1)),req,provider=Provider())
    changed=req.model_copy(update={'days':tuple(d.model_copy(update={'ends_at':d.ends_at+timedelta(minutes=1)}) for d in req.days)})
    wider=live_travel.enrich(ctx,changed,provider=Provider())
    identities=lambda c:{j.journey_id for j in c.operations.journeys}
    assert not identities(first)&identities(later) and not identities(first)&identities(wider)


def test_multi_day_restore_keeps_every_day_and_existing_operation(dated):
    req,ctx=dated;enriched=live_travel.enrich(ctx,req,provider=Provider())
    data=itinerary(3)
    for day,source in zip(data['days'],req.days):
        journey=next(j for j in enriched.operations.journeys if j.travel.origin_place_id=='origin'
                     and j.travel.destination_place_id=='museum' and live_travel.applies_to_day(j,daily(req,source)))
        leg=ScheduledLeg(journey=journey,departs_at=source.starts_at,arrives_at=source.starts_at+timedelta(minutes=1))
        day['itinerary']['legs']=[leg.model_dump(mode='json')]
    plan=MultiDayItinerary.model_validate(data)
    existing=next(j for j in enriched.operations.journeys if j.travel.origin_place_id==j.travel.destination_place_id)
    base=replace(ctx,operations=ctx.operations.model_copy(update={'journeys':(existing,)}))
    restored=live_travel.restore_snapshot(base,plan)
    assert len(restored.operations.journeys)==4
    assert {j.journey_id for j in restored.operations.journeys}=={existing.journey_id}|{d.itinerary.legs[0].journey.journey_id for d in plan.days}
    assert live_travel.restore_snapshot(restored,plan).operations==restored.operations


def test_multi_day_owns_one_provider_and_one_request_budget(dated,monkeypatch):
    req,ctx=dated;provider=Provider(limit=3);created=[]
    monkeypatch.setattr(live_travel,'provider_from_environment',lambda:created.append(provider) or provider)
    live_travel.enrich(ctx,req)
    assert len(created)==1 and provider.closed==1 and provider.request_count==3
    assert provider.days==[req.days[0].business_date]*2+[req.days[1].business_date]


def test_multi_day_deadline_is_shared_across_all_days(dated):
    req,ctx=dated;ticks=[0];provider=Provider(ticks=ticks)
    enriched=live_travel.enrich(ctx,req,provider=provider,clock=lambda:ticks[0],deadline=2)
    assert provider.request_count==2 and provider.days==[req.days[0].business_date]*2
    assert all(live_travel.applies_to_day(j,daily(req,req.days[0])) for j in enriched.operations.journeys)


def test_single_day_identity_and_snapshot_restore_remain_compatible(dated):
    req,ctx=dated;day=daily(req,req.days[0]);enriched=live_travel.enrich(ctx,day,provider=Provider())
    for j in enriched.operations.journeys:
        t=j.travel;expected=hashlib.sha256(f'{t.origin_place_id}|{t.destination_place_id}|{t.mode}'.encode()).hexdigest()[:20]
        assert j.journey_id=='amap:'+expected and live_travel.applies_to_day(j,day)
    data=itinerary()['days'][0]['itinerary'];journey=enriched.operations.journeys[0]
    data['legs']=[ScheduledLeg(journey=journey,departs_at=day.starts_at,arrives_at=day.starts_at).model_dump(mode='json')]
    from app.city.engine.models import Itinerary
    restored=live_travel.restore_snapshot(ctx,Itinerary.model_validate(data))
    assert restored.operations.journeys==(journey,)


def test_travel_matrix_rejects_a_quote_from_another_day(dated):
    from app.city.engine.travel import TravelTimeService
    req,ctx=dated;enriched=live_travel.enrich(ctx,req,provider=Provider());travel=TravelTimeService(enriched)
    pair=[j for j in enriched.operations.journeys if (j.travel.origin_place_id,j.travel.destination_place_id)==('origin','museum')]
    for index,day in enumerate(req.days):
        assert [travel.usable(j,daily(req,day)) for j in pair]==[index==i for i in range(3)]
