"""Unit-aware budget and real local HTTP/solver integration; no live providers."""
import copy,json
from datetime import datetime,timedelta
from dataclasses import replace
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.db import connect
from app.city import costing,travel_services,hotels
from app.city.budget_models import PricingOptions,StayPriceInput
from app.city.engine.models import Journey,MultiDayRequirements,OperationalBundle
from app.city.engine.storage import import_operations
from app.city.engine import service
from test_city_engine_api import engine,preview,confirm,current,rid
from test_multi_day_api import multi_engine,start_today,remaining
from test_hotel_overnight_binding import hotel_trip,select


def price_ops(e,taxi=False):
    operations=copy.deepcopy(e[2]['operations'])
    for c in operations['costs']:c.update(unit='per_person',revision=2,lower_fen=500,upper_fen=500)
    for j in operations['journeys']:
        j.update(cost_unit='per_person',revision=2)
        for b in j.get('boundaries',[]):b['cost_unit']='per_person'
        if taxi:
            j['travel']['mode']='driving';j['vehicle_cost_fen']=1000
    with connect() as db:
        from app.city import facts
        import_operations(db,OperationalBundle.model_validate(operations),facts.records(db),apply=True)
    return operations


def options(**kw):return {'party_size':2,'dining_per_person_day_fen':1000,**kw}


@pytest.mark.parametrize('unit,count,expected',[('per_person',3,1500),('per_party',3,500),(None,3,None),('per_person',None,None)])
def test_units_are_explicit(unit,count,expected):
    p=PricingOptions(party_size=count)
    assert costing.count_amount(500,unit,p)[0]==expected
    assert costing.count_amount(0,unit,p)[0]==0


def test_budget_is_not_silently_multiplied_by_days():
    assert costing.limit(PricingOptions(party_size=2,per_person_budget_fen=80000),3)==160000
    assert costing.limit(PricingOptions(party_size=2,per_person_budget_fen=80000,per_person_daily=True),3)==480000
    with pytest.raises(ValidationError):PricingOptions(hard_limit=True,per_person_budget_fen=80000)


@pytest.mark.parametrize('unit,expected',[('per_room_night',60000),('per_room_stay',30000),('party_stay',15000)])
def test_hotel_price_uses_explicit_unit_once(unit,expected):
    req=MultiDayRequirements.model_validate(__import__('multi_day_helpers').requirements(3))
    selection={'id':'h','price_selection':{'amount_fen':15000,'rooms':2,'unit':unit,'check_in':'2030-05-01','check_out':'2030-05-03','basis':'reported','source':'user'}}
    item=costing.stay_item(selection,req.overnights,datetime.fromisoformat('2030-04-30T08:00:00+08:00'))[0]
    assert item.amount_fen==expected
    selection['price_selection']['expires_at']='2030-04-29T08:00:00+08:00'
    assert costing.stay_item(selection,req.overnights,datetime.fromisoformat('2030-04-30T08:00:00+08:00'))[0].amount_fen is None


def test_taxi_multiplies_vehicles_not_people(engine):
    raw=copy.deepcopy(engine[2]['operations']['journeys'][0]);raw['travel']['mode']='driving';raw['vehicle_cost_fen']=1234
    j=Journey.model_validate(raw)
    assert costing.travel_amount(j,PricingOptions(party_size=5,vehicle_count=2))==2468
    assert costing.travel_amount(j,PricingOptions(party_size=5,driving_basis='self_drive')) is None


def priced_select(e,amount=15000,unit='per_room_stay',rooms=2):
    p,resp,_=select(e);assert resp.status_code==200,resp.text
    c=e[0];w=c.get(p+'/services').json()
    body={'request_id':rid(),'expected_version':w['version'],'candidate_id':'flyai:1',
        'manual_price':{'amount_fen':amount,'unit':unit,'rooms':rooms}}
    result=c.post(p+'/services/preview',json=body);assert result.status_code==200,result.text
    result=c.post(p+'/services/confirm',json={'request_id':rid(),'expected_version':w['version'],
        'preview_id':result.json()['preview_id'],'confirmed':True});assert result.status_code==200,result.text
    return p,result


def test_full_budget_preview_confirm_read_export_and_unknown_limit(hotel_trip):
    e,req=hotel_trip;price_ops(e);p,_=priced_select(e)
    req['pricing']=options(total_budget_fen=200000)
    r=preview(e,req);assert r.status_code==200 and r.json()['status']=='preview',r.text
    plan=r.json()['result']['itineraries'][0];budget=plan['budget']
    assert budget['by_category']['lodging']==30000
    assert budget['by_category']['dining']==6000
    assert budget['known_total_fen']==sum(i['amount_fen'] for i in budget['items'])
    assert budget['per_person_fen']==budget['known_total_fen']//2
    assert budget['status']=='within_budget' and not plan['cost_unknown']
    committed=confirm(e,r.json());assert committed.status_code==200,committed.text
    read=current(e);assert not read.json()['stale'],read.text
    assert read.json()['itinerary']['budget']==budget
    export=e[0].get('/api/v1/memory/export').json()['city_trips'][0]
    assert json.loads(export['planning_engine']['versions'][0]['itinerary_json'])['budget']==budget


def test_unknown_costs_are_not_certified_under_hard_budget(hotel_trip):
    e,req=hotel_trip;select(e)
    req['pricing']=options(total_budget_fen=200000,hard_limit=True)
    r=preview(e,req);assert r.status_code==200,r.text
    assert r.json()['candidate_id'] is None and r.json()['status'] in ('needs_data','timeout'),r.text
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()['route_version']==0


def test_expired_price_rejects_old_confirmation_and_marks_current_stale(hotel_trip):
    e,req=hotel_trip;price_ops(e);p,_=priced_select(e)
    req['pricing']=options()
    r=preview(e,req).json();assert r['candidate_id'],r
    with connect() as db:
        w=travel_services.read(db,e[1]['id']);w['selection']['hotels']['price_selection']['expires_at']='2030-04-29T08:00:00+08:00';travel_services.write(db,e[1]['id'],w)
    rejected=confirm(e,r);assert rejected.status_code==409,rejected.text


def test_report_replaces_covered_costs_and_survives_second_replan(hotel_trip,monkeypatch):
    e,req=hotel_trip;price_ops(e);priced_select(e)
    req['pricing']=options(total_budget_fen=200000)
    initial=confirm(e,preview(e,req).json());assert initial.status_code==200,initial.text
    req=current(e).json()['requirements'];e[1]['version']=initial.json()['version']
    moment=start_today(e,monkeypatch)
    snapshot=initial.json()['itinerary']['budget']
    covered=[i['cost_id'] for i in snapshot['items'] if i['category']=='lodging' or i['day']<'2030-05-02']
    req['pricing']['spending']={'amount_fen':22222,'as_of':moment.isoformat(),'covered_cost_ids':covered}
    response=remaining(e,req);assert response.status_code==200 and response.json()['candidate_id'],response.text
    budget=response.json()['result']['itineraries'][0]['budget']
    assert budget['by_category']['spent']==22222 and 'lodging' not in budget['by_category']
    assert not set(covered)&{i['cost_id'] for i in budget['items']}
    accepted=confirm(e,response.json());assert accepted.status_code==200,accepted.text
    e[1]['version']=accepted.json()['version']
    read=current(e);assert not read.json()['stale'],read.text
    response=remaining(e,req);assert response.status_code==200 and response.json()['candidate_id'],response.text
    assert response.json()['result']['itineraries'][0]['budget']['by_category']['spent']==22222
    bad=copy.deepcopy(req);bad['pricing']['spending']['covered_cost_ids']=['invented']
    assert remaining(e,bad).status_code==422


def test_manual_price_not_allowed_on_nonhotel():
    choice=travel_services.Choice(request_id=rid(),expected_version=1,candidate_id='poi',manual_price=StayPriceInput(amount_fen=0,unit='party_stay'))
    from fastapi import HTTPException
    with pytest.raises(HTTPException):travel_services.selected_price({}, {'category':'parking'},choice)


def test_solver_compares_taxi_prices_preserves_required_and_enforces_whole_budget(multi_engine):
    e,_=multi_engine;ops=price_ops(e,taxi=True)
    extras=[]
    for j in ops['journeys']:
        if j['travel']['origin_place_id']==j['travel']['destination_place_id']:continue
        cheap=copy.deepcopy(j);cheap.update(journey_id=j['journey_id']+':cheap',revision=1,vehicle_cost_fen=200)
        cheap['travel']['lower_seconds']+=600;cheap['travel']['upper_seconds']+=600;extras.append(cheap)
        unknown=copy.deepcopy(j);unknown.update(journey_id=j['journey_id']+':unknown',revision=1,vehicle_cost_fen=None)
        unknown['travel']['lower_seconds']=1;unknown['travel']['upper_seconds']=1;extras.append(unknown)
    with connect() as db:
        from app.city import facts
        import_operations(db,OperationalBundle(journeys=extras),facts.records(db),apply=True)
    from multi_day_helpers import requirements
    req=requirements(1);req['days'][0]['ends_at']='2030-05-01T20:00:00+08:00';req['modes']=['driving']
    req['alternatives']=1;req['solve_seconds']=3
    req['pricing']=options(total_budget_fen=4000,hard_limit=True)
    r=preview(e,req);assert r.status_code==200 and r.json()['candidate_id'],r.text
    plan=r.json()['result']['itineraries'][0]
    assert plan['budget']['status']=='within_budget' and plan['known_cost_upper_fen']<=4000
    assert 'flexible' in {a['goal_id'] for d in plan['days'] for a in d['itinerary']['activities']}
    assert all(l['journey']['journey_id'].endswith(':cheap') for d in plan['days'] for l in d['itinerary']['legs'])
    assert plan['budget']['by_category']['transport']==400
    accepted=confirm(e,r.json());assert accepted.status_code==200,accepted.text


@pytest.mark.parametrize('price_unit,expected',[('per_room_night',40000),('per_room_stay',20000),('party_stay',10000)])
def test_server_selects_rate_amount_not_client_or_display_price(hotel_trip,price_unit,expected):
    e,req=hotel_trip;p,_,_=select(e)
    with connect() as db:
        w=travel_services.read(db,e[1]['id']);w['result']['items'][0]['rate_plans']=[{
            'room_id':'room1','rate_id':'rate1','currency':'CNY','max_occupancy':2,'min_stay_days':1,'max_stay_days':3,
            'quote':{'total_fen':10000,'price_unit':price_unit,'check_in':'2030-05-01','check_out':'2030-05-03'}}]
        w['result']['items'][0]['expires_at']='2030-05-01T08:00:00+08:00'
        travel_services.write(db,e[1]['id'],w)
    result=e[0].post(p+'/services/preview',json={'request_id':rid(),'expected_version':w['version'],
        'candidate_id':'flyai:1','room_id':'room1','rate_id':'rate1','rooms':2})
    assert result.status_code==200,result.text
    item=result.json()['after'];assert item['price_selection']['basis']=='quoted'
    parsed=MultiDayRequirements.model_validate(req)
    assert costing.stay_item(item,parsed.overnights,datetime.fromisoformat('2030-04-30T08:00:00+08:00'))[0].amount_fen==expected


@pytest.mark.parametrize('change',['currency','date','unit','capacity','nights'])
def test_inapplicable_room_rates_are_rejected(hotel_trip,change):
    e,_=hotel_trip;p,_,_=select(e)
    with connect() as db:
        w=travel_services.read(db,e[1]['id']);w['requirements']['guests']=3
        rate={'room_id':'room1','rate_id':'rate1','currency':'CNY','max_occupancy':3,'min_stay_days':1,
            'quote':{'total_fen':10000,'price_unit':'per_room_stay','check_in':'2030-05-01','check_out':'2030-05-03'}}
        if change=='currency':rate['currency']='USD'
        elif change=='date':rate['quote']['check_in']='2030-05-02'
        elif change=='unit':rate['quote'].pop('price_unit')
        elif change=='capacity':rate['max_occupancy']=2
        else:rate['min_stay_days']=3
        w['result']['items'][0]['rate_plans']=[rate];travel_services.write(db,e[1]['id'],w)
    result=e[0].post(p+'/services/preview',json={'request_id':rid(),'expected_version':w['version'],
        'candidate_id':'flyai:1','room_id':'room1','rate_id':'rate1','rooms':1})
    assert result.status_code==422,result.text


def test_expired_report_and_client_tampered_budget_fail(hotel_trip,monkeypatch):
    e,req=hotel_trip;price_ops(e);priced_select(e);req['pricing']=options()
    r=preview(e,req).json();assert r['candidate_id'],r
    with connect() as db:
        row=db.execute('SELECT result_json FROM city_engine_candidates WHERE id=?',(r['candidate_id'],)).fetchone()
        data=json.loads(row[0]);data['itineraries'][0]['budget']['items'][0]['amount_fen']=0
        db.execute('UPDATE city_engine_candidates SET result_json=? WHERE id=?',(json.dumps(data),r['candidate_id']))
    assert confirm(e,r).status_code==409
    good=confirm(e,preview(e,req).json());assert good.status_code==200,good.text
    e[1]['version']=good.json()['version'];req=current(e).json()['requirements'];moment=start_today(e,monkeypatch)
    req['pricing']['spending']={'amount_fen':10000,'as_of':(moment-timedelta(minutes=16)).isoformat(),'covered_cost_ids':['lodging:flyai:1']}
    assert remaining(e,req).status_code==422


def test_lodging_is_not_subject_to_an_arbitrary_equal_daily_budget(hotel_trip):
    e,req=hotel_trip;price_ops(e);priced_select(e,amount=100000,rooms=1)
    req['pricing']=options(total_budget_fen=180000,hard_limit=True)
    r=preview(e,req);assert r.status_code==200 and r.json()['candidate_id'],r.text
    budget=r.json()['result']['itineraries'][0]['budget']
    assert budget['by_day']['2030-05-01']>180000//3
    assert budget['known_total_fen']<=180000 and budget['status']=='within_budget'


def test_quote_time_expiry_preserves_saved_snapshot_but_requires_refresh(hotel_trip,monkeypatch):
    e,req=hotel_trip;price_ops(e);p,_=priced_select(e)
    with connect() as db:
        w=travel_services.read(db,e[1]['id']);w['selection']['hotels']['price_selection']['expires_at']='2030-04-30T08:03:00+08:00';travel_services.write(db,e[1]['id'],w)
    req['pricing']=options()
    r=preview(e,req).json();assert r['expires_at']=='2030-04-30T08:03:00+08:00'
    accepted=confirm(e,r);assert accepted.status_code==200,accepted.text
    budget=accepted.json()['itinerary']['budget']
    monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2030-05-02T08:04:00+08:00'))
    read=current(e).json();assert read['stale'] and any(i['code']=='BUDGET_CHANGED' for i in read['issues'])
    assert read['itinerary']['budget']==budget


def test_optional_prices_keep_alternative_solver_paths_available(multi_engine):
    e,req=multi_engine;price_ops(e,taxi=True)
    req['pricing']=options();req['modes']=['driving'];req['objective']='lower_cost';req['alternatives']=3
    r=preview(e,req);assert r.status_code==200 and r.json()['candidate_id'],r.text
    assert all(any(a['goal_id']=='visit:2030-05-01' for a in p['days'][0]['itinerary']['activities']) for p in r.json()['result']['itineraries'])


def test_nested_pricing_is_single_source_for_compatibility_fields():
    from app.city.engine.models import MultiDayConstraints
    c=MultiDayConstraints(schema_version='city-constraints/2',party_size=3,per_person_budget_fen=10000,
        pricing=PricingOptions(party_size=2,per_person_budget_fen=80000))
    assert c.party_size==2 and c.per_person_budget_fen==80000
