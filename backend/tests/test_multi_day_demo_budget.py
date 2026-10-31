"""Demo budget admission and bounded-search diagnostics use synthetic dated facts only."""
from dataclasses import replace
import copy
import json

import pytest
from app.db import connect
from app.city.budget_models import PricingOptions
from app.city.engine import multi_day
from app.city.engine.models import Issue, SolveResult
from test_city_engine import sample,multi_sample,goal
from test_city_engine_api import confirm,engine,preview
from test_multi_day_api import multi_engine


@pytest.fixture
def budget_trip(sample):
    req,ctx=multi_sample(sample,goals=[dict(goal('must','museum',required=True).model_dump(mode='json'),
        allowed_dates=['2030-05-01','2030-05-02'])],alternatives=1)
    req=req.model_copy(update={'pricing':PricingOptions(total_budget_fen=100000,party_size=2,
        dining_per_person_day_fen=1000,hard_limit=True)})
    return req,ctx


def test_demo_hard_budget_keeps_unknown_summary_and_unpriced_items(budget_trip):
    req,ctx=budget_trip;ctx=replace(ctx,demo_reference=True)
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:0)
    assert result.status=='feasible' and result.itineraries,result
    for plan in result.itineraries:
        assert plan.budget.status=='unknown' and plan.budget.hard_limit and plan.cost_unknown
        assert plan.budget.unknown_items and any(i.amount_fen is None for i in plan.budget.items)
        assert plan.budget.remaining_fen is None
        assert plan.budget==multi_day.assemble(req,plan.days,plan.objectives[0],ctx).budget
        assert [a.goal_id for d in plan.days for a in d.itinerary.activities]==['must']


@pytest.mark.parametrize('search',['complete','bounded','expired'])
@pytest.mark.parametrize('demo,budget,code',[(True,1000,'BUDGET_OVER'),(False,100000,'BUDGET_UNKNOWN')])
def test_budget_failures_are_reported_even_when_search_is_not_exhausted(budget_trip,monkeypatch,search,demo,budget,code):
    req,ctx=budget_trip;ctx=replace(ctx,demo_reference=demo)
    req=req.model_copy(update={'pricing':req.pricing.model_copy(update={'total_budget_fen':budget})})
    ticks=[0.0]
    if search=='bounded':monkeypatch.setattr(multi_day,'MAX_ASSIGNMENTS',2)
    if search=='expired':
        original=multi_day.assemble
        def assemble(*args):
            plan=original(*args);ticks[0]=10;return plan
        monkeypatch.setattr(multi_day,'assemble',assemble)
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:ticks[0])
    assert result.status=='needs_data' and not result.itineraries,result
    assert {i.code for i in result.issues}=={code}
    assert result.metrics['search_truncated']==(search!='complete')


def test_demo_unknown_budget_still_checks_summary_consistency(budget_trip,monkeypatch):
    req,ctx=budget_trip;ctx=replace(ctx,demo_reference=True);original=multi_day.assemble
    def assemble(*args):
        plan=original(*args)
        return plan.model_copy(update={'budget':plan.budget.model_copy(update={'known_total_fen':0})})
    monkeypatch.setattr(multi_day,'assemble',assemble)
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:0)
    assert result.status=='invalid' and not result.itineraries,result
    assert 'COST_TOTAL' in {i.code for i in result.issues}


def test_mixed_failures_keep_search_timeout(budget_trip,monkeypatch):
    req,ctx=budget_trip;original=multi_day.solve;calls=[]
    monkeypatch.setattr(multi_day,'MAX_ASSIGNMENTS',2)
    def solve(*args,**kwargs):
        calls.append(1)
        if len(calls)==3:
            return SolveResult(status='needs_data',computed_at=ctx.assessed_at,
                issues=(Issue(code='NO_CANDIDATES',message='合成候选缺失'),))
        return original(*args,**kwargs)
    monkeypatch.setattr(multi_day,'solve',solve)
    result=multi_day.plan_multi_day(req,ctx,clock=lambda:0)
    assert len(calls)==3
    assert result.status=='timeout' and not result.itineraries,result
    assert {i.code for i in result.issues}=={'SEARCH_TIMEOUT'}


def _api_budget(req,total=100000,**pricing):
    body=copy.deepcopy(req)
    body['pricing']={'total_budget_fen':total,'party_size':2,'hard_limit':True,**pricing}
    return body


def _remove_api_costs():
    with connect() as db:
        db.execute("DELETE FROM city_engine_operations WHERE kind='cost'")


def test_demo_unknown_budget_preview_candidate_confirms_and_advances_route(multi_engine,monkeypatch):
    e,req=multi_engine;_remove_api_costs();monkeypatch.setenv('KAIFENG_DEMO_MODE','1')
    p=preview(e,_api_budget(req))
    assert p.status_code==200 and p.json()['status']=='preview',p.text
    candidate=p.json();plan=candidate['result']['itineraries'][0]
    assert plan['budget']['status']=='unknown' and plan['cost_unknown']
    r=confirm(e,candidate)
    assert r.status_code==200,r.text
    assert r.json()['route_version']==1


def test_non_demo_unknown_budget_candidate_is_rejected_at_confirmation(multi_engine,monkeypatch):
    e,req=multi_engine;_remove_api_costs();monkeypatch.setenv('KAIFENG_DEMO_MODE','1')
    p=preview(e,_api_budget(req));assert p.status_code==200 and p.json()['status']=='preview',p.text
    candidate=p.json()
    from app.city.engine import service
    from app.city.engine.context import PlanningContext
    with connect() as db:
        fingerprint=db.execute('SELECT fingerprint FROM city_engine_candidates WHERE id=?',(candidate['candidate_id'],)).fetchone()[0]
    original_context=service.context
    monkeypatch.setattr(service,'context',lambda db:replace(original_context(db),demo_reference=False))
    monkeypatch.setattr(PlanningContext,'fingerprint',lambda self:fingerprint)
    r=confirm(e,candidate)
    assert r.status_code==409,r.text
    detail=r.json()['detail'];assert detail['code']=='VALIDATION_FAILED'
    assert {i['code'] for i in detail['issues']}=={'BUDGET'}


def test_demo_known_budget_overrun_is_rejected_at_confirmation(multi_engine,monkeypatch):
    e,req=multi_engine;monkeypatch.setenv('KAIFENG_DEMO_MODE','1')
    body=_api_budget(req,total=100000,dining_per_person_day_fen=1000)
    body['goals'].append({'goal_id':'restaurant','target_kind':'place','target_id':'restaurant',
        'kind':'dining','required':True,'allowed_dates':['2030-05-03'],'fixed_date':'2030-05-03'})
    p=preview(e,body);assert p.status_code==200 and p.json()['status']=='preview',p.text
    candidate=p.json()
    assert candidate['result']['itineraries'][0]['budget']['known_total_fen']>=2000
    low=_api_budget(body,total=1000,dining_per_person_day_fen=1000)
    with connect() as db:
        db.execute('UPDATE city_engine_candidates SET requirements_json=? WHERE id=?',
                   (json.dumps(low),candidate['candidate_id']))
    r=confirm(e,candidate)
    assert r.status_code==409,r.text
    detail=r.json()['detail'];assert detail['code']=='VALIDATION_FAILED'
    assert 'BUDGET' in {i['code'] for i in detail['issues']}
