"""Validate B2.5's isolated browser inputs against the same production V2 models."""
from datetime import datetime
import pytest
from app.db import connect
from app.city import facts
from app.city.engine import service
from app.city.engine.models import MultiDayRequirements,MultiDayConstraints
from test_city_engine_api import engine,preview,confirm,current,rid
from multi_day_browser_fixture import browser_requirements,dated_records


def test_browser_fixture_has_explicit_daily_scope_and_overnight():
    data=browser_requirements();req=MultiDayRequirements.model_validate(data)
    constraints=MultiDayConstraints.model_validate({k:v for k,v in data.items() if k!='schema_version'}|{'schema_version':'city-constraints/2'})
    assert req.days[0].destination_place_id==req.days[1].origin_place_id==req.overnights[0].place_id
    assert len(req.days)==2 and constraints.days[1].business_date==req.end_date
    assert {g.fixed_date for g in req.goals}=={d.business_date for d in req.days}


@pytest.fixture
def browser_engine(engine,monkeypatch):
    client,trip,data=engine
    with connect() as db:facts.import_bundle(facts.FactBundle(records=dated_records(data)),db,apply=True)
    monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2030-05-01T08:00:00+08:00'))
    return engine


def test_browser_whole_candidate_confirms_and_reads_both_days(browser_engine):
    candidate=preview(browser_engine,browser_requirements())
    assert candidate.status_code==200 and candidate.json()['status']=='preview',candidate.text
    saved=confirm(browser_engine,candidate.json())
    assert saved.status_code==200,saved.text
    read=current(browser_engine).json()
    assert read['route_version']==1 and len(read['itinerary']['days'])==2
    assert [d['itinerary']['activities'][0]['goal_id'] for d in read['itinerary']['days']]==['museum-today','restaurant-tomorrow']


def test_exploration_v2_candidate_should_confirm_at_its_saved_workspace_version(browser_engine):
    client,trip,_=browser_engine
    req=browser_requirements()
    constraints={k:v for k,v in req.items() if k!='schema_version'}|{'schema_version':'city-constraints/2'}
    result=client.post('/api/city/v1/trips/'+trip['id']+'/exploration/preview',json={
        'request_id':rid(),'expected_version':trip['version'],'basket_version':0,'planning_version':0,'constraints':constraints})
    assert result.status_code==200,result.text
    saved=confirm(browser_engine,result.json())
    assert saved.status_code==200,saved.text
