"""Reference-data production flow with explicit mocked transport in temporary DBs."""
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.db import connect
from app.city import facts
from app.city.engine import live_travel, service
from app.city.engine.amap import Quote
from app.city.engine.context import PlanningContext
from app.city.engine.models import OperationalBundle
from app.city.engine.prepare import prepare
from app.city.engine.models import Requirements

PACKAGE = Path(__file__).resolve().parents[2] / 'data/mvp/city-facts-reference-2026-10-25.json'


class FakeProvider:
    interval=0
    request_count=0
    fail=False
    def estimate(self, origin, destination, mode, departure, checked):
        self.request_count+=1
        return Quote('timeout_or_network') if self.fail else Quote('estimated',duration_seconds=600,
            distance_m=700,geometry_available=True,queried_at=checked.isoformat())
    def close(self):pass


@pytest.fixture
def reference(client,monkeypatch):
    bundle=json.loads(PACKAGE.read_text())
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(bundle),db,apply=True)
    monkeypatch.setenv('AMAP_PLANNING_ENABLED','1')
    provider=FakeProvider()
    monkeypatch.setattr(live_travel,'provider_from_environment',lambda:provider)
    client.post('/api/v1/sessions')
    trip=client.post('/api/city/v1/trips',json={'request_id':str(uuid4()),'draft':{}}).json()
    # Fixed replay date only in this isolated test; no claim about live service time.
    moment=datetime.fromisoformat('2026-10-24T19:00:00+08:00')
    monkeypatch.setattr(service,'now',lambda:moment)
    req=dict(business_date='2026-10-25',starts_at='2026-10-25T17:30:00+08:00',ends_at='2026-10-25T22:00:00+08:00',
        origin_place_id='pl_mvp_kaifengfu_meeting',destination_place_id='pl_mvp_kaifengfu_meeting',
        goals=[dict(goal_id='park',target_kind='place',target_id='pl_wb_000003',required=True,duration_seconds=5400),
               dict(goal_id='dinner',target_kind='food',target_id='food_wb_000001',kind='dining',required=True,duration_seconds=3600),
               dict(goal_id='market',target_kind='place',target_id='pl_wb_000013',required=True,duration_seconds=2700)])
    return client,trip,req,provider


def preview(e):
    c,t,r,_=e
    response=c.post(f"/api/city/v1/trips/{t['id']}/engine/preview",json={'request_id':str(uuid4()),'expected_version':t['version'],'requirements':r})
    assert response.status_code==200,response.text
    return response.json()


def commit(e,p):
    c,t,_,_=e
    return c.post(f"/api/city/v1/trips/{t['id']}/engine/confirm",json={'request_id':str(uuid4()),'expected_version':p['base_version'],'candidate_id':p['candidate_id']})


def test_reference_is_not_verified_and_raw_candidate_still_blocked(reference):
    with connect() as db:catalog=facts.records(db)
    p=catalog['place','pl_wb_000003'];moment=datetime.fromisoformat('2026-10-25T17:30:00+08:00')
    assert p.validation_status=='unverified' and p.reviewer is None and p.verified_at is None
    assert facts.status(p,catalog,moment)['ready']
    assert not facts.status(p.model_copy(update={'planning_status':'candidate'}),catalog,moment)['ready']
    assert not facts.status(p.model_copy(update={'rights_status':'prohibited'}),catalog,moment)['ready']
    assert not facts.status(p.model_copy(update={'longitude':None,'latitude':None}),catalog,moment)['ready']
    ctx=PlanningContext(catalog,OperationalBundle(),moment-timedelta(days=1))
    assert ctx.ready(p,moment,moment+timedelta(hours=4))


def test_provider_estimate_preview_confirm_reload_and_sources(reference,monkeypatch):
    e=reference;c,t,_,provider=e;p=preview(e)
    assert p['status']=='preview',p
    assert provider.request_count>0
    itinerary=p['result']['itineraries'][0]
    assert itinerary['evidence_mode']=='reference_with_estimates'
    assert all(a['data_status']=='reference' for a in itinerary['activities'])
    assert all(l['journey']['time_basis']=='provider_estimate' for l in itinerary['legs'])
    assert any(l['journey']['estimated_seconds']==600 and l['journey']['planning_buffer_seconds']==120 for l in itinerary['legs'])
    assert all(l['journey']['travel']['upper_seconds'] is None for l in itinerary['legs'])
    with connect() as db:
        assert db.execute('SELECT COUNT(*) FROM city_engine_routes').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM city_engine_operations').fetchone()[0]==0
    saved=commit(e,p)
    assert saved.status_code==200,saved.text
    route=c.get(f"/api/city/v1/trips/{t['id']}/engine/current").json()
    assert not route['stale'],route
    # Old quotes are marked for refresh, rather than erasing the saved itinerary.
    monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2026-10-24T19:20:00+08:00'))
    route=c.get(f"/api/city/v1/trips/{t['id']}/engine/current").json()
    assert not route['stale'] and route['traffic_refresh_recommended']
    assert 'reference' in json.dumps(c.get('/api/city/v1/facts').json())


def test_three_objectives_reuse_provider_quotes_and_keep_unknown_costs(reference):
    c,t,req,provider=reference
    single=preview((c,t,req|{'alternatives':1},provider))
    calls=provider.request_count
    multi=preview((c,t,req|{'alternatives':3},provider))
    assert single['status']==multi['status']=='preview'
    assert calls>0 and provider.request_count==2*calls
    plans=multi['result']['itineraries']
    assert {o for p in plans for o in p['objectives']}=={'balanced','less_walking','lower_cost'}
    assert all(p['cost_unknown'] for p in plans)
    assert commit(reference,multi).status_code==200
    assert provider.request_count==2*calls


def test_expired_quote_cannot_be_confirmed(reference,monkeypatch):
    p=preview(reference)
    monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2026-10-24T19:20:00+08:00'))
    assert commit(reference,p).status_code==409


def test_provider_failure_does_not_use_demo_or_old_quote(reference):
    reference[3].fail=True
    p=preview(reference)
    assert p['status']!='preview' and not p['result']['itineraries']
    with connect() as db:assert db.execute('SELECT COUNT(*) FROM city_engine_routes').fetchone()[0]==0


def test_reference_free_ticket_and_unknown_closing_are_not_silently_filled(reference):
    with connect() as db:catalog=facts.records(db)
    market=catalog['open_rule','open_mvp_gulou_20261025']
    assert market.closes_at is None and market.ticket_required is False
    assert not facts.access_required(market)
    assert preview(reference)['status']=='preview'


def test_demo_mode_selects_curated_opening_and_uses_horizon_for_unknown_close(reference):
    with connect() as db:
        catalog=facts.records(db)
    market=catalog['open_rule','open_mvp_gulou_20261025']
    conflicting=market.model_copy(update={
        'fact_id':'open_reference_conflict_20261025',
        'closes_at':datetime.fromisoformat('2026-10-25T22:00:00+08:00'),
    })
    catalog[('open_rule',conflicting.fact_id)]=conflicting
    starts=datetime.fromisoformat('2026-10-25T17:30:00+08:00')
    ends=datetime.fromisoformat('2026-10-25T22:00:00+08:00')
    production=PlanningContext(catalog,OperationalBundle(),starts-timedelta(days=1))
    demo=PlanningContext(catalog,OperationalBundle(),starts-timedelta(days=1),demo_reference=True)
    assert len(production.planning_rules('pl_wb_000013',starts.date(),starts,ends))==2
    selected=demo.planning_rules('pl_wb_000013',starts.date(),starts,ends)
    assert [r.fact_id for r in selected]==['open_mvp_gulou_20261025']
    req=Requirements.model_validate({
        'business_date':'2026-10-25','starts_at':starts,'ends_at':ends,
        'origin_place_id':'pl_mvp_kaifengfu_meeting','destination_place_id':'pl_mvp_kaifengfu_meeting',
        'goals':[{'goal_id':'market','target_kind':'place','target_id':'pl_wb_000013',
                  'required':True,'duration_seconds':2700}],
    })
    problem=prepare(req,demo)
    activity=next(a for a in problem.activities if a.goal_id=='market')
    assert activity.latest==int((ends-starts).total_seconds())-2700


def test_demo_mode_picks_split_window_overlapping_trip(reference):
    with connect() as db:
        catalog=facts.records(db)
    for key in [k for k,v in catalog.items() if k[0]=='open_rule' and v.place_id=='pl_wb_000013']:
        del catalog[key]
    base=catalog['source','src_mvp_team_return']
    def window(suffix,opens,closes):
        rule=facts.OpenRule(kind='open_rule',fact_id='open_split_'+suffix,place_id='pl_wb_000013',business_date='2026-10-25',
            opens_at=f'2026-10-25T{opens}:00+08:00',closes_at=f'2026-10-25T{closes}:00+08:00',source_ids=(base.fact_id,),
            rights_status='internal_only',planning_status='reference',planning_note='分段营业测试')
        catalog[('open_rule',rule.fact_id)]=rule
    window('lunch','10:30','14:00');window('dinner','17:30','21:00')
    starts=datetime.fromisoformat('2026-10-25T17:30:00+08:00');ends=datetime.fromisoformat('2026-10-25T22:00:00+08:00')
    production=PlanningContext(catalog,OperationalBundle(),starts-timedelta(days=1))
    demo=PlanningContext(catalog,OperationalBundle(),starts-timedelta(days=1),demo_reference=True)
    assert len(production.planning_rules('pl_wb_000013',starts.date(),starts,ends))==2
    assert [r.fact_id for r in demo.planning_rules('pl_wb_000013',starts.date(),starts,ends)]==['open_split_dinner']


def test_budget_with_unknown_costs_blocks_in_production_but_not_in_demo(reference,monkeypatch):
    c,t,r,provider=reference
    budgeted=(c,t,r|{'budget_fen':80000},provider)
    monkeypatch.delenv('KAIFENG_DEMO_MODE',raising=False)
    blocked=preview(budgeted)
    assert blocked['status']!='preview' and not blocked['result']['itineraries']
    monkeypatch.setenv('KAIFENG_DEMO_MODE','1')
    p=preview(budgeted)
    assert p['status']=='preview',p
    assert all(i['cost_unknown'] for i in p['result']['itineraries'])
    assert any('费用未知时不按预算拦截' in w for w in p['result']['warnings'])
    assert commit(budgeted,p).status_code==200


def test_scenic_area_named_as_trip_endpoint_means_the_open_point_at_its_gate(reference):
    with connect() as db:items=facts.records(db)
    assert facts.trip_endpoint(items,'pl_wb_000003')=='pl_mvp_kaifengfu_meeting'
    # Places that are not scenic areas, and areas without a recorded gate point, stay as named.
    assert facts.trip_endpoint(items,'pl_wb_000013')=='pl_wb_000013'
    assert facts.trip_endpoint(items,'pl_mvp_kaifengfu_meeting')=='pl_mvp_kaifengfu_meeting'


def test_default_stay_is_longer_for_a_whole_scenic_area_or_night_market(reference):
    from app.city.engine.prepare import default_stay
    with connect() as db:items=facts.records(db)
    assert default_stay('visit',items['place','pl_wb_000003'])==5400
    assert default_stay('visit',items['place','pl_wb_000013'])==3600
    assert default_stay('dining',items['place','pl_wb_vendor_000001'])==3600
