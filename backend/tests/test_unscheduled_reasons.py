"""Omission explanations use synthetic facts and the existing pure solver only."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from app.city import exploration
from app.city.agent.orchestration import _ready_reply
from app.city.engine.models import Goal
from test_city_engine import sample
from test_city_engine_api import engine
from test_exploration import collect,plan,base


def _goal(goal_id,**patch):
    return Goal(goal_id=goal_id,target_kind='place',target_id='museum',kind='visit',**patch)


def _explain(req,ctx,*,blocked=(),plans=None,standby=()):
    result={'itineraries':plans if plans is not None else [{'activities':[]}],
            'metrics':{'blocked_candidates':list(blocked)}}
    keys={g.goal_id:'place:'+g.goal_id for g in req.goals}
    meta={g.goal_id:{'key':keys[g.goal_id],'name':'[合成测试]'+g.goal_id,
          'status':'standby' if g.goal_id in standby else 'pending','goal':g} for g in req.goals}
    return exploration._unscheduled(result,req,ctx,keys,meta)


@pytest.mark.parametrize('reasons,expected',[
    (['OPENING_UNKNOWN'],'opening_unknown'),
    (['OPENING_AMBIGUOUS_OR_STALE'],'opening_unknown'),
    (['TIME_WINDOW_EMPTY'],'closed_in_window'),
    (['SESSION_TIME_UNKNOWN'],'no_session'),
    (['SESSION_OUTSIDE_OPENING_OR_GOAL_WINDOW'],'no_session'),
    (['SESSION_NOT_READY'],'no_session'),
    (['RESERVATION_NOT_CONFIRMED'],'access_unconfirmed'),
    (['TICKET_NOT_CONFIRMED'],'access_unconfirmed'),
    (['COST_UNKNOWN'],'cost_unknown'),
    (['PLACE_NOT_READY'],'data_missing'),
    (['NO_USABLE_VARIANT'],'data_missing'),
    (['NO_USABLE_VARIANT','COST_UNKNOWN','TICKET_NOT_CONFIRMED','SESSION_NOT_READY','TIME_WINDOW_EMPTY','OPENING_UNKNOWN'],'opening_unknown'),
    (['NO_USABLE_VARIANT','COST_UNKNOWN','TICKET_NOT_CONFIRMED','SESSION_NOT_READY','TIME_WINDOW_EMPTY'],'closed_in_window'),
    (['COST_UNKNOWN','TICKET_NOT_CONFIRMED','SESSION_NOT_READY'],'no_session'),
    (['COST_UNKNOWN','TICKET_NOT_CONFIRMED'],'access_unconfirmed'),
    (['NO_USABLE_VARIANT','COST_UNKNOWN'],'cost_unknown'),
])
def test_blocked_candidates_are_explained_in_order(sample,monkeypatch,reasons,expected):
    req,ctx=sample;req=req.model_copy(update={'goals':(_goal('goal'),)})
    monkeypatch.setattr(exploration.engine,'plan_trip',lambda *a:pytest.fail('blocked targets need no standalone solve'))
    # The preparer records several entries for one goal, including NO_USABLE_VARIANT.
    blocked=[{'goal_id':'goal','reasons':[reason]} for reason in reasons]
    rows=_explain(req,ctx,blocked=blocked)
    assert rows['0'][0]['reason_code']==expected
    assert rows['0'][0]['goal_id']=='goal' and rows['0'][0]['key']=='place:goal'


@pytest.mark.parametrize('seconds,expected',[(2400,'too_far_alone'),(3600,'time_conflict')])
def test_standalone_actual_solver_keeps_time_endpoints_mode_and_pace(sample,monkeypatch,seconds,expected):
    from datetime import timedelta
    req,ctx=sample;req=req.model_copy(update={'goals':(_goal('goal',duration_seconds=1800),),
        'ends_at':req.starts_at+timedelta(seconds=seconds),'pace':'relaxed','modes':('walking',)})
    original=exploration.engine.plan_trip;calls=[]
    def solve(single,context):
        calls.append(single)
        assert single.model_dump(exclude={'goals','alternatives'})==req.model_dump(exclude={'goals','alternatives'})
        assert context is ctx and single.alternatives==1 and len(single.goals)==1
        return original(single,context)
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    rows=_explain(req,ctx)
    assert rows['0'][0]['reason_code']==expected and len(calls)==1


@pytest.mark.parametrize('status',['timeout','unknown','unavailable','needs_data'])
def test_standalone_uncertain_status_never_claims_too_far(sample,monkeypatch,status):
    req,ctx=sample;req=req.model_copy(update={'goals':(_goal('goal'),)})
    monkeypatch.setattr(exploration.engine,'plan_trip',lambda *a:SimpleNamespace(status=status))
    assert _explain(req,ctx)['0'][0]['reason_code']=='time_conflict'


def test_standalone_solver_is_capped_at_three_targets_and_reused_by_other_alternatives(sample,monkeypatch):
    req,ctx=sample;req=req.model_copy(update={'goals':tuple(_goal(f'goal-{i}') for i in range(5))})
    calls=[]
    def solve(single,context):
        calls.append(single.goals[0].goal_id)
        return SimpleNamespace(status='infeasible' if len(calls)==1 else 'feasible')
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    rows=_explain(req,ctx,plans=[{'activities':[]},{'activities':[]},{'activities':[]}])
    assert calls==['goal-0','goal-1','goal-2']
    assert [item['reason_code'] for item in rows['0']]==['too_far_alone']+['time_conflict']*4
    assert rows['0']==rows['1']==rows['2']


def test_other_alternatives_do_not_start_new_standalone_solves_or_report_kept_or_standby(sample,monkeypatch):
    req,ctx=sample;req=req.model_copy(update={'goals':(_goal('first'),_goal('second'),_goal('standby'))})
    calls=[]
    def solve(single,context):
        calls.append(single.goals[0].goal_id);return SimpleNamespace(status='infeasible')
    monkeypatch.setattr(exploration.engine,'plan_trip',solve)
    rows=_explain(req,ctx,plans=[{'activities':[{'goal_id':'first'}]},{'activities':[{'goal_id':'second'}]}],standby=('standby',))
    assert calls==['second']
    assert [(r['goal_id'],r['reason_code']) for r in rows['0']]==[('second','too_far_alone')]
    assert [(r['goal_id'],r['reason_code']) for r in rows['1']]==[('first','time_conflict')]


def test_missing_transport_is_data_missing_without_standalone_solve(sample,monkeypatch):
    req,ctx=sample;req=req.model_copy(update={'goals':(_goal('goal'),)})
    ctx=replace(ctx,operations=ctx.operations.model_copy(update={'journeys':()}))
    monkeypatch.setattr(exploration.engine,'plan_trip',lambda *a:pytest.fail('missing transport needs no standalone solve'))
    assert _explain(req,ctx)['0'][0]['reason_code']=='data_missing'


def test_preview_explanations_share_enriched_snapshot_and_persist_idempotently(engine,monkeypatch):
    from app.city.engine import service
    c,t,_=engine
    collect(c,t,'place','museum');collect(c,t,'place','garden')
    original=service.context
    def without_transport(db):
        ctx=original(db)
        return replace(ctx,operations=ctx.operations.model_copy(update={'journeys':()}))
    enriched=[]
    def enrich(ctx,req):
        with exploration.connect() as db:full=original(db)
        enriched.append(full);return full
    monkeypatch.setattr(service,'context',without_transport)
    monkeypatch.setattr(service.live_travel,'enrich',enrich)
    calls=[];original_solve=service.plan_trip
    def solve(req,ctx):
        calls.append((req,ctx));return original_solve(req,ctx)
    monkeypatch.setattr(service,'plan_trip',solve)
    response=plan(engine,ends_at='2030-05-01T10:00:00+08:00')
    assert response.status_code==200,response.text
    result=response.json();assert result['status']=='feasible',result
    assert result['unscheduled']['0'][0]['reason_code']=='time_conflict'
    assert len(enriched)==1 and len(calls)==2 and all(ctx is enriched[0] for _,ctx in calls)
    body=__import__('json').loads(response.request.content)
    assert c.post(base(t)+'/exploration/preview',json=body).json()==result
    assert len(calls)==2
    assert c.get(base(t)+'/exploration/constraints').json()['result']==result
    assert c.get(base(t)).json()['route_version']==0


def test_ready_reply_lists_unscheduled_reasons():
    result={'planning':{'result':{'itineraries':[{'activities':[]}] }},
            'goal_keys':{'a':'place:a','b':'place:b'},'activity_names':{},
            'unscheduled':{'0':[
                {'goal_id':'a','key':'place:a','name':'万岁山武侠城','reason_code':'too_far_alone','reason':'单独去也来不及：路程加停留超出你的时间段'},
                {'goal_id':'b','key':'place:b','name':'鼓楼夜市','reason_code':'closed_in_window','reason':'开放时间和你的出行时段对不上'},
            ]}}
    assert _ready_reply(result)=='方案已准备好。这版没排进：万岁山武侠城（单独去也来不及：路程加停留超出你的时间段）、鼓楼夜市（开放时间和你的出行时段对不上）。请点“确认方案”保存。'


def test_ready_reply_keeps_legacy_fallback_without_unscheduled():
    result={'planning':{'result':{'itineraries':[{'activities':[]}] }},
            'goal_keys':{'a':'place:a'},'activity_names':{'a':'[合成测试]目标'}}
    assert _ready_reply(result)=='方案已准备好。时间不够，这版没排进：[合成测试]目标。请点“确认方案”保存。'
