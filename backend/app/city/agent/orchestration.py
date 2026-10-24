"""Backend coordinator for the existing exploration workflow; never a solver/commit."""
import time
from uuid import UUID,uuid5
from fastapi import HTTPException
from .. import exploration,exploration_state,basket,state,travel_services
from ..schema import TZ
from .planning_intent import proposal


def execute(session,t,intent):
    proposed=proposal(session,t.trip_id,t.snapshot,intent,t.text,today=t.interpretation_date)
    action=proposed.get('planning_action')
    if not action:return proposed
    planning_version=action['planning_version'];basket_version=action['basket_version']
    service_version=t.snapshot.services.get('version',0)
    def guard(db):
        if t.cancelled:raise HTTPException(409,detail={'code':'AGENT_CANCELLED'})
        if time.monotonic()-t.started>=t.budget.max_seconds:raise HTTPException(429,detail={'code':'AGENT_DEADLINE'})
        state.check_version(state.owned(db,session,t.trip_id),action['trip_version'])
        basket.guard(db,t.trip_id,basket_version)
        exploration_state.guard(db,t.trip_id,planning_version)
        if travel_services.read(db,t.trip_id)['version']!=service_version:
            raise HTTPException(409,detail={'code':'SERVICE_CONDITIONS_CHANGED'})
    constraints=action['constraints']
    if action.get('choice_text'):
        saved=exploration.answer_choice(UUID(t.trip_id),exploration.Answer(
            request_id=uuid5(UUID(t.id),'planning-choice'),expected_version=action['trip_version'],
            basket_version=basket_version,planning_version=planning_version,text=action['choice_text']),session,execution_guard=guard)
        planning_version=saved['version'];basket_version=saved['basket']['version'];constraints=saved['constraints']
    result=exploration.preview_plan(UUID(t.trip_id),exploration.Plan(
        request_id=uuid5(UUID(t.id),'planning-preview'),expected_version=action['trip_version'],
        basket_version=basket_version,planning_version=planning_version,constraints=constraints),session,execution_guard=guard)
    # Accept only this workflow's own increments. Subsequent _fresh still rejects
    # any external change; it must not replace old interpretation with new state.
    t.snapshot.workspace={'version':result['planning_version'],'constraints':result['constraints'],'result':result,'rounds':result['rounds']}
    env=t.snapshot.envelope
    t.snapshot.envelope=env.model_copy(update={
        'planning_constraints':{'version':result['planning_version'],'constraints':result['constraints'],'rounds':result['rounds']},
        'basket':env.basket|{'version':basket_version}})
    missing=[q['field'] for q in result.get('questions',[])]
    if missing:
        if 'business_date' in missing:reply='你打算哪天去？'
        elif any(k in missing for k in ('starts_at','ends_at')):reply='当天几点开始，最晚几点结束？'
        elif set(missing)>={'origin_place_id','destination_place_id'}:reply='从哪里出发，最后返回哪里？'
        else:reply='从哪里出发？' if 'origin_place_id' in missing else '最后回到哪里？'
        status='needs_clarification'
    else:
        reply='方案已准备好，请点“确认方案”保存。' if result['status']=='feasible' else result['reply']
        status='answered' if result['status']=='feasible' else 'needs_clarification' if result['status']=='infeasible' else 'needs_data'
    if action['date_changed']:reply='日期已记为 '+result['constraints']['business_date']+'。'+reply
    return {'status':status,'reply':reply,'formal_state_changed':False,
        'planning_workflow':{'trip_id':t.trip_id,'execution':'completed_backend','operation':action['operation'],
            'date_changed':action['date_changed'],'result':result}}
