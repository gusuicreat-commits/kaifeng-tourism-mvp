"""Backend coordinator for the existing exploration workflow; never a solver/commit."""
import time
from uuid import UUID,uuid5
from fastapi import HTTPException
from .. import exploration,exploration_state,basket,state,travel_services
from ..schema import TZ
from .planning_intent import proposal,trip_days
from .dates import resolve_date

LABELS={'date':'日期','start_time':'出发时间','end_time':'结束时间','origin':'出发地','destination':'返回地',
        'budget_fen':'预算','mode':'出行方式','pace':'节奏','objective':'偏好','city':'城市'}



def _ready_reply(result):
    """Say which wanted stops the default (first) alternative leaves out, so the visitor need not spot it."""
    first=(((result.get('planning') or {}).get('result') or {}).get('itineraries') or [{}])[0]
    kept={a.get('goal_id') for a in first.get('activities',())}
    names=result.get('activity_names') or {}
    left=[names[g] for g in result.get('goal_keys') or {} if g not in kept and names.get(g)]
    return '方案已准备好。'+(f"时间不够，这版没排进：{'、'.join(left)}。" if left else '')+'请点“确认方案”保存。'


def _execute(session,t,intent):
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
    asked=set()
    if missing:
        if 'business_date' in missing:reply='你打算哪天去？';asked={'date'}
        elif any(k in missing for k in ('starts_at','ends_at')):reply='当天几点开始，最晚几点结束？';asked={'start_time','end_time'}
        elif set(missing)>={'origin_place_id','destination_place_id'}:reply='从哪里出发，最后返回哪里？';asked={'origin','destination'}
        elif 'origin_place_id' in missing:reply='从哪里出发？';asked={'origin'}
        else:reply='最后回到哪里？';asked={'destination'}
        status='needs_clarification'
    else:
        reply=_ready_reply(result) if result['status']=='feasible' else result['reply']
        status='answered' if result['status']=='feasible' else 'needs_clarification' if result['status']=='infeasible' else 'needs_data'
    if action['date_changed']:reply='日期已记为 '+result['constraints']['business_date']+'。'+reply
    # Ask only about fields still empty after the workflow (e.g. a return point inferred from "回来" is filled).
    final=result['constraints'] or {}
    filled={'date':final.get('business_date'),'start_time':final.get('starts_at'),'end_time':final.get('ends_at'),
            'origin':final.get('origin_place_id') or final.get('origin_text'),
            'destination':final.get('destination_place_id') or final.get('destination_text'),'budget_fen':final.get('budget_fen')}
    dropped=[LABELS.get(f,f) for f in dict.fromkeys(d['field'] for d in t.interpretation.get('dropped_fields',()) if d['check']!='per_person_budget')
             if not f.startswith('service.') and f not in asked and not filled.get(f)]
    if dropped:reply+=f"另外，{'、'.join(dropped)}没听准，能再说一下吗？"
    if proposed.get('notes'):reply+=''.join(proposed['notes'])
    return {'status':status,'reply':reply,'formal_state_changed':False,
        'planning_workflow':{'trip_id':t.trip_id,'execution':'completed_backend','operation':action['operation'],
            'date_changed':action['date_changed'],'result':result}}


def execute(session,t,intent):
    result=_execute(session,t,intent)
    if intent.planning and intent.planning.operation!='weather_replan' and trip_days(t.text)>1:
        from .requirement_context import planning_values
        day=planning_values(t.snapshot).get('business_date')
        field=next((f for f in intent.planning.fields if f.field=='date'),None)
        if field:day=resolve_date(field.value,t.interpretation_date or t.snapshot.envelope.identity.generated_at.astimezone(TZ).date()).isoformat()
        result['reply']=f"目前只能先排第一天（{day or '日期待补充'}），多天行程还在开发中。"+result['reply'].removeprefix(f'日期已记为 {day}。')
    if any(d['check']=='per_person_budget' for d in t.interpretation.get('dropped_fields',())):
        result['reply']+='按人预算暂时不能用于规划，先不按预算筛选。'
    return result
