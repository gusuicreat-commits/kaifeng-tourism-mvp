"""Single-day party totals through the real local preview and confirmation chain."""
import copy,json

import pytest

from app.city import facts
from app.city.budget_models import PricingOptions
from app.city.engine.models import OperationalBundle
from app.city.engine.storage import import_operations
from app.db import connect
from test_city_engine_api import engine
from test_exploration import base,collect,plan,rid


@pytest.fixture
def priced_day(engine,monkeypatch):
    from app.city.agent import provider
    monkeypatch.setattr(provider,'generate',lambda *a,**kw:pytest.fail('Must not call a model'))
    operations=copy.deepcopy(engine[2]['operations'])
    for cost in operations['costs']:
        amount=30000 if cost['place_id']=='museum' else 10000 if cost['place_id']=='garden' else 0
        cost.update(revision=2,unit='per_person',lower_fen=amount,upper_fen=amount)
    with connect() as db:
        import_operations(db,OperationalBundle.model_validate(operations),facts.records(db),apply=True)
    return engine


@pytest.mark.parametrize('party,budget,status,known',[
    (2,50000,'infeasible',None),(2,70000,'feasible',60000),(None,50000,'feasible',30000),
])
def test_ticket_budget_uses_explicit_party_only(priced_day,party,budget,status,known):
    c,t,_=priced_day
    assert collect(c,t,'place','museum','must').status_code==200
    response=plan(priced_day,budget_fen=budget,**({'party_size':party} if party else {}))
    assert response.status_code==200,response.text
    result=response.json();assert result['status']==status,result
    solved=result['planning']['result']
    if known is None:
        assert not solved['itineraries'] and not result['planning']['candidate_id']
        assert c.get(base(t)).json()['route_version']==0
    else:
        assert solved['itineraries']
        assert all(p['known_cost_upper_fen']==known and not p['cost_unknown'] for p in solved['itineraries'])


def test_preview_and_confirm_preserve_pricing_and_soft_per_person_budget(priced_day):
    c,t,_=priced_day
    collect(c,t,'place','museum','must')
    response=plan(priced_day,party_size=2,budget_fen=70000,per_person_budget_fen=10000,per_person_budget_daily=True)
    assert response.status_code==200,response.text
    result=response.json();assert result['status']=='feasible',result
    candidate=result['planning']['candidate_id']
    with connect() as db:
        req=json.loads(db.execute('SELECT requirements_json FROM city_engine_candidates WHERE id=?',(candidate,)).fetchone()[0])
    assert req['pricing']==PricingOptions(party_size=2,per_person_budget_fen=10000,per_person_daily=True).model_dump(mode='json')
    assert req['budget_fen']==70000
    committed=c.post(base(t)+'/engine/confirm',json={'request_id':rid(),'expected_version':t['version'],'candidate_id':candidate})
    assert committed.status_code==200,committed.text
    current=c.get(base(t)+'/engine/current').json()
    assert current['route_version']==1 and not current['stale'],current
    with connect() as db:
        saved=json.loads(db.execute('SELECT requirements_json FROM city_engine_routes WHERE trip_id=? AND route_version=1',(t['id'],)).fetchone()[0])
    assert saved['pricing']==req['pricing'] and saved['budget_fen']==70000
    assert current['itinerary']==committed.json()['itinerary']==result['planning']['result']['itineraries'][0]
    assert current['itinerary']['known_cost_upper_fen']==60000


def test_no_party_does_not_enable_pricing_for_per_person_budget(priced_day):
    c,t,_=priced_day
    collect(c,t,'place','museum','must')
    result=plan(priced_day,budget_fen=50000,per_person_budget_fen=10000).json()
    assert result['status']=='feasible',result
    with connect() as db:
        req=json.loads(db.execute('SELECT requirements_json FROM city_engine_candidates WHERE id=?',(result['planning']['candidate_id'],)).fetchone()[0])
    assert req['pricing'] is None
    assert result['planning']['result']['itineraries'][0]['known_cost_upper_fen']==30000


def test_lower_cost_with_party_prefers_cheaper_equal_reward_combination(priced_day):
    c,t,_=priced_day
    for place in ('museum','garden'):assert collect(c,t,'place',place).status_code==200
    journeys=copy.deepcopy(priced_day[2]['operations']['journeys'])
    for j in journeys:
        j['revision']=2
        if 'garden' in (j['travel']['origin_place_id'],j['travel']['destination_place_id']):
            j['travel']['lower_seconds']+=300;j['travel']['upper_seconds']+=300
    with connect() as db:
        import_operations(db,OperationalBundle.model_validate({'journeys':journeys}),facts.records(db),apply=True)
    # Either visit fits; both do not. Cost must win even if the cheaper trip takes longer.
    response=plan(priced_day,party_size=2,objective='lower_cost',ends_at='2030-05-01T10:00:00+08:00')
    assert response.status_code==200,response.text
    result=response.json();assert result['status']=='feasible',result
    cheapest=next(p for p in result['planning']['result']['itineraries'] if 'lower_cost' in p['objectives'])
    assert [a['place_id'] for a in cheapest['activities']]==['garden']
    assert cheapest['known_cost_upper_fen']==20000 and not cheapest['cost_unknown']


@pytest.mark.parametrize('unit,known,unknown',[
    ('per_person',62800,False),('per_party',31400,False),(None,0,True),
])
def test_place_and_travel_units_share_party_pricing(priced_day,unit,known,unknown):
    c,t,d=priced_day
    cost=copy.deepcopy(next(x for x in d['operations']['costs'] if x['place_id']=='museum'))
    cost.update(revision=3,unit=unit,lower_fen=30000,upper_fen=30000)
    journeys=copy.deepcopy(d['operations']['journeys'])
    for j in journeys:j.update(revision=2,cost_unit=unit,cost_lower_fen=700,cost_upper_fen=700)
    with connect() as db:
        import_operations(db,OperationalBundle.model_validate({'costs':[cost],'journeys':journeys}),facts.records(db),apply=True)
    collect(c,t,'place','museum','must')
    result=plan(priced_day,party_size=2).json();assert result['status']=='feasible',result
    assert all(p['known_cost_upper_fen']==known and p['cost_unknown']==unknown for p in result['planning']['result']['itineraries'])


def test_lower_cost_with_party_prefers_cheaper_slower_transport(priced_day):
    c,t,d=priced_day
    journeys=copy.deepcopy(d['operations']['journeys']);extras=[]
    for j in journeys:
        j.update(revision=2,cost_unit='per_person',cost_lower_fen=700,cost_upper_fen=700)
        cheap=copy.deepcopy(j);cheap.update(journey_id=j['journey_id']+':cheap',revision=1,cost_lower_fen=100,cost_upper_fen=100)
        cheap['travel']['lower_seconds']+=600;cheap['travel']['upper_seconds']+=600;extras.append(cheap)
    with connect() as db:
        import_operations(db,OperationalBundle.model_validate({'journeys':journeys+extras}),facts.records(db),apply=True)
    collect(c,t,'place','museum','must')
    result=plan(priced_day,party_size=2,objective='lower_cost').json();assert result['status']=='feasible',result
    cheapest=next(p for p in result['planning']['result']['itineraries'] if 'lower_cost' in p['objectives'])
    assert cheapest['known_cost_upper_fen']==60400
    assert all(l['journey']['journey_id'].endswith(':cheap') for l in cheapest['legs'])
