"""V3 tests share an explicitly synthetic city fixture, never the running DB."""
import copy
import json
import threading
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect
from app import language
from app.city import facts
from app.city.assistant import context
from app.city.agent import service, understand, provider, toolbelt
from app.city.agent.models import Understanding, Constraint, Action
from test_city_assistant import city, walking, planned, get, modify, rid, path, confirm
from test_city_engine import sample


@pytest.fixture(autouse=True)
def empty_tasks():
    service.TASKS.clear()
    yield
    service.TASKS.clear()


def start(e,text):
    t=get(e)
    return e['client'].post(path(e,'/agent/tasks'),json={'request_id':rid(),'text':text,'expected_version':t['version'],'expected_route_version':t['route_version']})


def step(e,t,**kw):
    return e['client'].post(path(e,'/agent/tasks/'+t['task_id']+'/step'),json={'request_id':rid(),'expected_step':t['step'],**kw})


def run(e,text):
    r=start(e,text);assert r.status_code==200,r.text;t=r.json()
    for _ in range(12):
        if t['status']!='running':break
        r=step(e,t);assert r.status_code==200,r.text;t=r.json()
    assert t['status']!='running',t
    return t


def snapshot(e):
    from app.memory.session import Session
    with connect() as db:
        row=db.execute('SELECT * FROM conversation_sessions ORDER BY created_at DESC LIMIT 1').fetchone()
        s=Session(row['id'],row['guest_token_hash'],None)
    return context.load(s,e['trip']['id'],'modify'),s


def test_multigoal_preserves_hard_soft_optional_unknown(city):
    walking(city);snap,_=snapshot(city)
    u=understand.rules('找个吃饭的地方，有灌汤包最好，没有也行，下午演出必须看，步行不要超过十分钟，晚上别太晚回酒店。',snap)
    assert any(a.kind=='search_dining' for a in u.actions)
    assert any(c.field=='food' and c.strength=='optional' for c in u.constraints)
    assert any(c.field=='walking_seconds' and c.value==600 and c.strength=='hard' for c in u.constraints)
    assert any(c.field=='preserve_shows' and c.strength=='hard' for c in u.constraints)
    assert understand.route(u,snap)[0]=='clarify'
    assert next(c for c in u.constraints if c.field=='return_by').value is None


def test_clock_correction_uses_last_with_afternoon(city):
    walking(city);snap,_=snapshot(city)
    u=understand.rules('午饭下午四点吃，算了，还是五点吧。',snap)
    assert [c.value for c in u.constraints if c.field=='meal_at']==['17:00']
    assert len(u.corrections)==1 and understand.route(u,snap)[0]=='workflow'


def test_ambiguous_show_must_clarify(city):
    city['data']['requirements']['goals'].append({'goal_id':'extra','target_kind':'session','target_id':'s1','kind':'show','required':True})
    planned(city);snap,_=snapshot(city)
    u=understand.rules('帮我找个吃饭的地方，那场演出不能错过。',snap)
    assert understand.route(u,snap)[0]=='clarify'


@pytest.mark.parametrize("online",[False,True])
def test_next_semantic_online_and_deterministic_offline(city,monkeypatch,online):
    walking(city);before=get(city)
    calls=[]
    monkeypatch.setattr(language,'available',lambda:online)
    def semantic(*a,**k):
        calls.append(1)
        return {'actions':[{'kind':'query','quote':'下一站去哪？'}]},{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',semantic)
    t=run(city,'下一站去哪？')
    assert len(calls)==int(online) and t['metrics']['model_calls']==int(online)
    assert t['mode']=='direct' and t['result']['next_stop']['stop_id']=='lunch'
    assert t['metrics']['tools']==0 and get(city)==before


def test_offline_react_fixture_preview_and_confirmation(city):
    walking(city);before=get(city)
    t=run(city,'附近找个吃饭的地方，下午演出必须保留，最好少走一点。')
    assert t['status']=='needs_confirmation',t
    assert t['mode']=='react' and t['metrics']['tools']==4
    assert get(city)==before
    assert confirm(city,t['result']).status_code==200
    assert get(city)['route_version']==before['route_version']+1


def test_hard_walking_limit_rejects_without_silent_relaxation(city):
    walking(city);before=get(city)
    t=run(city,'附近找个吃饭的地方，演出必须看，步行不能超过1分钟。')
    assert t['status']=='needs_clarification',t
    assert t['result']['issues'][0]['code']=='WALKING_LIMIT' and get(city)==before


def test_unknown_food_never_creates_restaurant(city):
    walking(city)
    t=run(city,'附近找一家必须能吃灌汤包的店，下午演出不能错过。')
    assert t['status']=='needs_data' and 'confirmation' not in t['result']


def test_missing_location_stops_before_search(city):
    planned(city)
    t=run(city,'附近找个吃饭的地方，演出必须看。')
    assert t['status']=='needs_data' and t['metrics']['tools']==0


def test_task_cancel_stops_future_model_and_tools(city,monkeypatch):
    walking(city);t=start(city,'附近找个吃饭的地方，演出必须看。').json()
    t=step(city,t).json()
    c=city['client'].post(path(city,'/agent/tasks/'+t['task_id']+'/cancel')).json()
    monkeypatch.setattr(toolbelt,'search_dining',lambda *a:pytest.fail('cancelled task must not query'))
    assert step(city,c).json()['status']=='cancelled'


def test_cancelled_route_candidate_cannot_confirm(city):
    walking(city);t=run(city,'附近找个吃饭的地方，演出必须看。')
    assert t['status']=='needs_confirmation'
    city['client'].post(path(city,'/agent/tasks/'+t['task_id']+'/cancel'))
    assert confirm(city,t['result']).status_code==409


def test_page_change_stops_stale_task(city):
    walking(city);t=start(city,'附近找个吃饭的地方，演出必须看。').json();t=step(city,t).json()
    modify(city,{'operation':'set_anchor','anchor':{'place_id':'museum','confirmed_at':city['clock'][0].isoformat()}})
    t=step(city,t).json()
    assert t['status']=='stale' and t['metrics']['tools']==0


def test_fact_change_stops_stale_task(city):
    walking(city);t=start(city,'附近找个吃饭的地方，演出必须看。').json();t=step(city,t).json()
    with connect() as db:
        item=facts.records(db)['show_session','s2'].model_dump(mode='json')|{'version':2,'business_status':'cancelled'}
        facts.import_bundle(facts.FactBundle(records=[item]),db,apply=True)
    assert step(city,t).json()['status']=='stale'


def test_cross_session_and_csrf_denied(city):
    walking(city);t=start(city,'下一站去哪？').json()
    with TestClient(app) as other:
        other.post('/api/v1/sessions')
        assert other.get(path(city,'/agent/tasks/'+t['task_id'])).status_code==404
    assert city['client'].post(path(city,'/agent/tasks'),headers={'Origin':'https://other.invalid'},json={}).status_code==403


def test_history_off_purges_tasks_and_no_hidden_intent(city):
    walking(city);t=start(city,'附近找个吃饭的地方，演出必须看。').json()
    assert city['client'].put('/api/v1/memory/history',json={'enabled':False}).status_code==200
    assert t['task_id'] not in service.TASKS
    with connect() as db:
        rows=db.execute("SELECT result_json FROM city_receipts WHERE scope LIKE 'agent_chat:%'").fetchall()
        assert all('附近' not in r[0] and 'history_cleared' in r[0] for r in rows)
    t=run(city,'晚上别太晚回酒店。')
    assert t['status']=='needs_clarification' and t['understanding'] is None
    with connect() as db:assert db.execute('SELECT COUNT(*) FROM city_proposals').fetchone()[0]==0


def test_delete_purges_transient_tasks(city):
    walking(city);t=start(city,'下一站去哪？').json()
    assert city['client'].post('/api/v1/privacy/delete-request',json={'request_id':rid(),'confirmed':True}).status_code==200
    assert t['task_id'] not in service.TASKS
    assert city['client'].get(path(city,'/agent/tasks/'+t['task_id'])).status_code==401


def test_idempotency_and_step_cas(city):
    walking(city);v=get(city);body={'request_id':rid(),'text':'下一站去哪？','expected_version':v['version'],'expected_route_version':v['route_version']}
    a=city['client'].post(path(city,'/agent/tasks'),json=body).json()
    b=city['client'].post(path(city,'/agent/tasks'),json=body).json();assert a['task_id']==b['task_id']
    assert city['client'].post(path(city,'/agent/tasks'),json=body|{'text':'别的要求'}).status_code==409
    t=step(city,a).json();assert t['step']==1 and not t['busy']


def test_clarification_resume_preserves_previous_constraints(city):
    walking(city)
    t=run(city,'附近找个吃饭的地方，演出必须保留，晚上别太晚回酒店。')
    assert t['status']=='needs_clarification'
    t=run(city,'18点前回酒店。')
    assert t['mode']=='react'
    assert any(c['field']=='preserve_shows' for c in t['understanding']['constraints'])
    assert next(c for c in t['understanding']['constraints'] if c['field']=='return_by')['value']=='18:00'


def test_malicious_model_fields_rejected_and_falls_back(city,monkeypatch):
    walking(city);before=get(city)
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'owner':'other','actions':[{'kind':'confirm','quote':'吃饭'}]},{}))
    t=run(city,'附近找个吃饭的地方，演出必须看。')
    assert t['interpretation']['basis']=='rule_fallback' and get(city)==before


def test_model_cannot_drop_explicit_hard_show_constraint(city,monkeypatch):
    walking(city);snap,_=snapshot(city)
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_dining','quote':'找个吃饭的地方'}]},{}))
    u,meta=understand.parse('找个吃饭的地方，演出必须看。',snap)
    assert any(c.field=='preserve_shows' and c.strength=='hard' for c in u.constraints)


def test_repeat_model_action_stops(city,monkeypatch):
    walking(city)
    monkeypatch.setattr(language,'available',lambda:True)
    def fake(system,payload,**kw):
        if '下一步选择器' in system:return {'tool':'search_dining'},{}
        return {'actions':[{'kind':'search_dining','quote':'附近找个吃饭的地方'}]},{}
    monkeypatch.setattr(provider,'generate',fake)
    t=run(city,'附近找个吃饭的地方，演出必须看。')
    assert t['status']=='no_progress' and t['metrics']['tools']==1


def test_model_cannot_call_unregistered_commit(city,monkeypatch):
    walking(city)
    monkeypatch.setattr(language,'available',lambda:True)
    def fake(system,payload,**kw):
        if '下一步选择器' in system:return {'tool':'confirm_itinerary','place_id':'other'},{}
        return {'actions':[{'kind':'search_dining','quote':'找个吃饭的地方'}]},{}
    monkeypatch.setattr(provider,'generate',fake)
    t=run(city,'找个吃饭的地方。')
    assert t['status']=='invalid_action' and t['metrics']['tools']==0


def test_provider_failure_stops_no_retries(city,monkeypatch):
    walking(city)
    monkeypatch.setattr(language,'available',lambda:True)
    calls=[]
    def fake(system,payload,**kw):
        calls.append(system)
        if '下一步选择器' in system:raise HTTPException(503,detail='unavailable')
        return {'actions':[{'kind':'search_dining','quote':'找个吃饭的地方'}]},{}
    monkeypatch.setattr(provider,'generate',fake)
    t=run(city,'找个吃饭的地方。')
    assert t['status']=='unavailable' and len(calls)==2


def test_terminal_travel_cannot_preview(city):
    walking(city);modify(city,{'operation':'transition','status':'CANCELLED'},confirm_change=True)
    t=run(city,'附近找个吃饭的地方，演出必须看。')
    assert t['status']!='needs_confirmation'


def test_partial_correction_does_not_remove_earlier_hard_requirement(city):
    walking(city);snap,_=snapshot(city)
    u=understand.rules('演出必须看，我想去清明上河园，算了先找个吃饭的地方。',snap)
    assert any(c.field=='preserve_shows' and c.strength=='hard' for c in u.constraints)
    assert any(a.kind=='search_dining' for a in u.actions)
    u=understand.rules('演出必须看，必须吃灌汤包，算了先找普通餐厅。',snap)
    assert any(c.field=='preserve_shows' for c in u.constraints)
    assert not any(c.field=='food' for c in u.constraints)


def test_cancel_inflight_preview_consumes_late_candidate(city,monkeypatch):
    walking(city);before=get(city)
    t=start(city,'附近找个吃饭的地方，演出必须看。').json()
    for _ in range(4):t=step(city,t).json()
    assert [e['tool'] for e in t['events']][-1]=='verify_travel'
    ready=threading.Event();release=threading.Event();original=toolbelt.preview;results=[]
    def delayed(*a,**kw):
        result=original(*a,**kw);ready.set();assert release.wait(5);return result
    monkeypatch.setattr(toolbelt,'preview',delayed)
    thread=threading.Thread(target=lambda:results.append(step(city,t)));thread.start()
    assert ready.wait(5)
    cancelled=city['client'].post(path(city,'/agent/tasks/'+t['task_id']+'/cancel'))
    assert cancelled.status_code==200
    release.set();thread.join(5);assert not thread.is_alive()
    assert results[0].status_code==200 and results[0].json()['status']=='cancelled'
    with connect() as db:
        rows=db.execute('SELECT consumed FROM city_engine_candidates WHERE trip_id=?',(city['trip']['id'],)).fetchall()
        assert all(r['consumed'] for r in rows)
    assert get(city)==before


def test_total_task_budget_stops_before_tool(city):
    walking(city);t=start(city,'附近找个吃饭的地方。').json()
    service.TASKS[t['task_id']].started-=46
    response=step(city,t).json()
    assert response['status']=='budget_exhausted' and response['metrics']['tools']==0


def test_model_cannot_preview_before_evidence_queries(city,monkeypatch):
    walking(city);before=get(city)
    monkeypatch.setattr(language,'available',lambda:True)
    def fake(system,payload,**kw):
        if '下一步选择器' in system:
            assert all(x['tool']!='preview_plan' for x in payload['eligible_actions'])
            return {'tool':'preview_plan','place_id':'restaurant'},{}
        return {'actions':[{'kind':'search_dining','quote':'找个吃饭的地方'}]},{}
    monkeypatch.setattr(provider,'generate',fake)
    t=run(city,'找个吃饭的地方。')
    assert t['status']=='invalid_action' and t['metrics']['tools']==0 and get(city)==before


def test_tool_budget_and_no_progress_are_distinct(city,monkeypatch):
    from app.city.agent.models import Budget
    walking(city);t=start(city,'附近找个吃饭的地方。').json()
    service.TASKS[t['task_id']].budget=Budget(max_tools=1)
    t=step(city,t).json();t=step(city,t).json();t=step(city,t).json()
    assert t['status']=='budget_exhausted' and t['metrics']['tools']==1


def test_v3_intent_cannot_be_confirmed_through_v2(city):
    walking(city);t=run(city,'晚上别太晚回酒店。')
    with connect() as db:pid=db.execute('SELECT id FROM city_proposals WHERE consumed=0').fetchone()[0]
    r=city['client'].post(path(city,'/assistant/confirm'),json={'request_id':rid(),'expected_version':get(city)['version'],
        'target_kind':'state','candidate_id':pid,'confirmed':True})
    assert r.status_code==409


def test_export_covers_task_audit_without_internal_reasoning(city):
    walking(city);t=run(city,'附近找个吃饭的地方，演出必须看。')
    export=city['client'].get('/api/v1/memory/export').json()
    receipts=export['city_trips'][0]['receipts']
    record=next(r['result'] for r in receipts if r['result'].get('task_id')==t['task_id'])
    assert record['trace'] and record['stop_reason']=='needs_confirmation'
    assert 'reasoning' not in json.dumps(record) and 'longitude' not in json.dumps(record)


def test_model_can_choose_different_valid_query_order(city,monkeypatch):
    walking(city)
    monkeypatch.setattr(language,'available',lambda:True)
    decisions=iter([{'tool':'query_shows'},{'tool':'search_dining'},
                    {'tool':'verify_travel','place_id':'restaurant'},{'tool':'preview_plan','place_id':'restaurant'}])
    def fake(system,payload,**kw):
        if '下一步选择器' in system:return next(decisions),{}
        return {'actions':[{'kind':'search_dining','quote':'附近找个吃饭的地方'}]},{}
    monkeypatch.setattr(provider,'generate',fake)
    t=run(city,'附近找个吃饭的地方，演出必须看。')
    assert t['status']=='needs_confirmation' and t['events'][1]['tool']=='query_shows'


def test_walking_limit_is_solver_constraint_and_independent_confirmation_check(sample):
    from app.city.engine.tools import plan_trip
    from app.city.engine.prepare import prepare
    from app.city.engine.validation import validate_itinerary
    req,ctx=sample
    result=plan_trip(req,ctx);assert result.status=='feasible'
    hard=req.model_copy(update={'max_walking_seconds':1})
    assert plan_trip(hard,ctx).status=='infeasible'
    errors=validate_itinerary(prepare(hard,ctx),result.itineraries[0])
    assert any(e.code=='WALKING_LIMIT' for e in errors)


def test_understanding_endpoint_is_independent_and_read_only(city):
    walking(city);before=get(city)
    payload={'request_id':rid(),'text':'午饭下午四点吃，算了，还是五点吧。','expected_version':before['version'],'expected_route_version':before['route_version']}
    r=city['client'].post(path(city,'/agent/understand'),json=payload)
    assert r.status_code==200 and r.json()['understanding']['constraints'][0]['value']=='17:00'
    assert r.json()['memory_written'] is False and get(city)==before
    assert not service.TASKS


def test_query_and_modification_are_both_handled_in_one_turn(city):
    walking(city)
    t=run(city,'演出几点开始？午饭改到下午一点。')
    assert t['mode']=='compound' and t['status']=='needs_confirmation',t
    assert '15:00' in t['result']['answers'][0]['reply']
    assert '15:00' in t['result']['reply']


def test_legacy_pending_intent_survives_compound_schema_upgrade(city):
    walking(city);snap,_=snapshot(city)
    previous=Understanding(actions=(Action(kind='modify',quote='步行不能超过十分钟'),),
        constraints=(Constraint(field='walking_seconds',strength='hard',value=600,quote='步行不能超过十分钟'),))
    saved=previous.model_dump(mode='json');saved['schema_version']='travel-intent/3.1'
    snap.pending.append({'status':'valid','target_kind':'intent',
        'action':{'operation':'assistant_v3_intent','understanding':saved}})
    followup=Understanding(actions=(Action(kind='preference',quote='最好少走一点'),),
        constraints=(Constraint(field='objective',strength='soft',value='less_walking',quote='最好少走一点'),))
    merged=service._merge(followup,snap)
    assert merged.schema_version=='travel-intent/3.2'
    assert any(c.field=='walking_seconds' and c.strength=='hard' and c.value==600 for c in merged.constraints)


def test_optional_relaxation_never_applies_to_hard_food(city):
    walking(city);snap,_=snapshot(city)
    hard=understand.rules('找个吃饭的地方，必须吃灌汤包。',snap)
    with pytest.raises(HTTPException):toolbelt.search_dining(snap,hard,True)
    soft=understand.rules('找个吃饭的地方，有灌汤包最好，没有也行。',snap)
    assert toolbelt.search_dining(snap,soft,True)['items']


def test_search_does_not_drop_existing_meal_time_window(city):
    walking(city);snap,_=snapshot(city)
    from app.city.engine.models import Goal
    original=snap.requirements
    goals=tuple(g.model_copy(update={'target_kind':'place','target_id':'restaurant'}) if g.kind=='dining' else g for g in original.goals)
    snap.requirements=original.model_copy(update={'goals':goals})
    intent=understand.rules('附近找个吃饭的地方，演出必须看。',snap)
    req,_=toolbelt.apply_constraints(snap,intent,'restaurant')
    meal=next(g for g in req.goals if g.kind=='dining')
    assert meal.window_start==next(g for g in goals if g.kind=='dining').window_start
    assert meal.window_end==next(g for g in goals if g.kind=='dining').window_end


def test_trace_distinguishes_mock_choices_from_fallback_and_records_legal_actions(city,monkeypatch):
    walking(city);before=get(city)
    fallback=run(city,'附近找个吃饭的地方，下午演出必须保留。')
    assert all(d['source']=='deterministic_fallback' and d['fallback'] for d in fallback['decisions'])
    monkeypatch.setattr(language,'available',lambda:True)
    def fake(system,payload,**kw):
        if 'eligible_actions' not in payload:
            return {'actions':[{'kind':'search_dining','quote':'附近找个吃饭的地方'}]}, {'source':'mock_model'}
        choices=payload['eligible_actions']
        order=('search_dining','query_shows','verify_travel','preview_plan')
        return next(d for tool in order for d in choices if d['tool']==tool),{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',fake)
    t=run(city,'附近找个吃饭的地方，下午演出必须保留。')
    assert t['status']=='needs_confirmation' and get(city)==before
    assert [d['request_sequence'] for d in t['decisions']]==[2,3,4,5]
    assert all(d['source']=='mock_model' and d['legal'] and not d['fallback'] and d['tool_status'] for d in t['decisions'])
    assert all(e.get('source')!='live_model' for e in t['events'])


def test_illegal_preview_has_explicit_trace_and_no_tool_execution(city,monkeypatch):
    walking(city);before=get(city)
    monkeypatch.setattr(language,'available',lambda:True)
    def fake(system,payload,**kw):
        if 'eligible_actions' in payload:return {'tool':'preview_plan','place_id':'restaurant'},{'source':'mock_model'}
        return {'actions':[{'kind':'search_dining','quote':'找个吃饭的地方'}]},{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',fake)
    t=run(city,'找个吃饭的地方。')
    assert t['status']=='invalid_action' and len(t['decisions'])==2 and t['metrics']['tools']==0
    assert all(not d['legal'] and d['action']['tool']=='preview_plan' for d in t['decisions'])
    assert get(city)==before


def test_failed_understanding_keeps_diagnostics_and_never_claims_live_success(city,monkeypatch):
    walking(city);before=get(city)
    monkeypatch.setattr(language,'available',lambda:True)
    def fail(*a,**kw):raise HTTPException(503,detail={'code':'MODEL_HTTP_ERROR','source':'mock_model',
        'upstream_status':429,'error_type':'exceeded_current_quota_error','automatic_retries':0})
    monkeypatch.setattr(provider,'generate',fail)
    t=run(city,'找个吃饭的地方。')
    assert t['status']=='unavailable' and not t['decisions']
    assert t['events'][0]['provider_failure']['error_type']=='exceeded_current_quota_error'
    assert t['metrics']['model_calls']==1 and get(city)==before
    assert t['events'][0]['status']=='failed'


def test_changed_observation_changes_mock_action_without_relaxing_constraints(city,monkeypatch):
    walking(city);before=get(city)
    monkeypatch.setattr(language,'available',lambda:True)
    observed=[]
    def fake(system,payload,**kw):
        if 'eligible_actions' not in payload:return {'actions':[{'kind':'search_dining','quote':'找个吃饭的地方'}]},{'source':'mock_model'}
        obs=payload['observations'];observed.append(obs)
        if obs.get('search_dining',{}).get('status')=='no_candidates':return {'tool':'finish'},{'source':'mock_model'}
        for tool in ('search_dining','query_shows','verify_travel','preview_plan'):
            for action in payload['eligible_actions']:
                if action['tool']==tool:return action,{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',fake)
    good=run(city,'找个吃饭的地方，演出必须保留。')
    monkeypatch.setattr(toolbelt,'search_dining',lambda *a,**k:{'status':'no_candidates','code':'NO_VERIFIED_SUPPLY_OR_OPENING','items':[]})
    bad=run(city,'找个吃饭的地方，演出必须保留。')
    assert good['status']=='needs_confirmation' and bad['status']=='needs_data'
    assert bad['decisions'][-1]['action']['tool']=='finish' and bad['decisions'][-1]['legal']
    assert any(o.get('search_dining',{}).get('status')=='no_candidates' for o in observed)
    assert get(city)==before


def test_route_candidate_rejected_after_another_page_changes_version(city):
    walking(city);t=run(city,'附近找个吃饭的地方，演出必须看。')
    assert t['status']=='needs_confirmation'
    modify(city,{'operation':'set_anchor','anchor':{'place_id':'museum','confirmed_at':city['clock'][0].isoformat()}})
    changed=get(city)
    assert confirm(city,t['result']).status_code==409
    assert get(city)==changed
