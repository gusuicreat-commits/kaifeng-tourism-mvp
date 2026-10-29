"""Backend coordinator for the existing exploration workflow; never a solver/commit."""
import time
from uuid import UUID,uuid5
from fastapi import HTTPException
from .. import exploration,exploration_state,basket,state,travel_services
from ..schema import TZ
from .planning_intent import proposal
from .dates import resolve_date

LABELS={'date':'日期','start_time':'出发时间','end_time':'结束时间','origin':'出发地','destination':'返回地',
        'budget_fen':'预算','mode':'出行方式','pace':'节奏','objective':'偏好','city':'城市','trip_days':'游玩天数'}



def _ready_reply(result):
    """Say which wanted stops the default (first) alternative leaves out, so the visitor need not spot it."""
    first=(((result.get('planning') or {}).get('result') or {}).get('itineraries') or [{}])[0]
    kept={a.get('goal_id') for a in first.get('activities',())}
    names=result.get('activity_names') or {}
    left=[names[g] for g in result.get('goal_keys') or {} if g not in kept and names.get(g)]
    return '方案已准备好。'+(f"时间不够，这版没排进：{'、'.join(left)}。" if left else '')+'请点“确认方案”保存。'


def _budget_reply(constraints,itinerary,days,notes,overnight=False):
    """The per-person budget check replaces the "recorded" note once a plan exists."""
    budget=itinerary.get('budget') if isinstance(itinerary,dict) else getattr(itinerary,'budget',None)
    if budget:
        if hasattr(budget,'model_dump'):budget=budget.model_dump()
        amount=budget['known_total_fen']/100
        suffix='；仍有未计入项，暂不能确认预算够用。' if budget['unknown_items'] else '；当前核算超过预算。' if budget['status']=='over' else '；当前核算在预算内（含估计或手填金额）。' if budget['status']=='within_budget' else '。'
        from .budget_check import recorded_note
        clean=''.join(n for n in notes if n!=recorded_note(constraints))
        if budget['limit_fen'] is not None:clean+=f"全程预算 ¥{budget['limit_fen']/100:.2f}，"
        unknown='待核对：'+'；'.join(budget['unknown_items'][:4])+'。' if budget['unknown_items'] else ''
        return clean+f'全程已计入费用 ¥{amount:.2f}'+suffix+unknown
    from .budget_check import check,recorded_note
    sentence=check(constraints,itinerary,days=days,overnight=overnight)
    recorded=recorded_note(constraints)
    return ''.join(n for n in notes if not (sentence and n==recorded))+sentence


def _basket_reply(pending,snapshot):
    if not pending:return ''
    existing={(i['kind'],i['id']) for i in snapshot.materials}
    adding=[i['name'] for i in pending['items'] if i['operation']=='put' and (i['kind'],i['object_id']) not in existing]
    updating=[i['name'] for i in pending['items'] if i['operation']=='put' and (i['kind'],i['object_id']) in existing]
    standby=[i['name'] for i in pending['items'] if i['operation']=='standby']
    return (('要把'+'、'.join(adding)+'加入素材篮吗？' if adding else '')+
            ('要调整'+'、'.join(updating)+'的优先级吗？' if updating else '')+
            ('要把'+'、'.join(standby)+'改为候补吗？' if standby else '')+'确认后我接着排。')


def _execute(session,t,intent,proposed=None):
    if proposed is None:proposed=proposal(session,t.trip_id,t.snapshot,intent,t.text,today=t.interpretation_date)
    conflicts=t.interpretation.get('planning_conflicts',())
    action=proposed.get('planning_action')
    if not action:
        if conflicts:proposed.update(status='needs_clarification',reply=''.join(c['question'] for c in conflicts))
        return proposed
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
    if action['constraints'].get('schema_version')=='city-constraints/2':
        from ..engine.models import MultiDayConstraints,MultiDayRequirements,PreviewRequestV2
        from .multi_day_intent import _questions
        constraints=MultiDayConstraints.model_validate(action['constraints'])
        missing=_questions(action['constraints'])
        if missing or conflicts or proposed.get('basket_proposal') or proposed.get('basket_choices'):
            from ...db import connect
            with connect() as db:
                db.execute('BEGIN IMMEDIATE');guard(db)
                saved=exploration_state.save(db,t.trip_id,constraints.model_dump(mode='json'),None,t.snapshot.workspace.get('rounds',0))
            pending=proposed.get('basket_proposal') if not conflicts else None
            reply=''.join(c['question'] for c in conflicts) if conflicts else (
                ''.join(missing[:1])+_basket_reply(pending,t.snapshot))
            reply+=''.join(proposed.get('notes',[]))
            from ..assistant import context as assistant_context
            t.snapshot=assistant_context.load(session,t.trip_id,'modify',t.text)
            return {'status':'needs_clarification' if missing or conflicts or not pending else 'needs_confirmation',
                'reply':reply,'formal_state_changed':False,
                **{key:proposed[key] for key in ('basket_proposal','basket_choices') if key in proposed and not conflicts},
                'planning_workflow':{'trip_id':t.trip_id,'execution':'completed_backend','operation':action['operation'],
                    'date_changed':action['date_changed'],'result':{'status':'needs_clarification' if missing or conflicts else 'unknown',
                        'issues':[{'code':'PLANNING_FIELD_CONFLICT' if conflicts else 'BASKET_CONFIRMATION_REQUIRED'}] if conflicts or pending else [],
                        'constraints':constraints.model_dump(mode='json')}}}
        from ..engine.models import DayRequirements
        req_data=constraints.model_dump(mode='json')
        req_data['days']=[{k:v for k,v in d.items() if k in DayRequirements.model_fields} for d in req_data['days']]
        req_data.pop('trip_days',None);req_data.pop('trip_nights',None)
        for key in ('pending_fields','clarifications','range_impacts',*exploration.AGENT_ONLY):req_data.pop(key,None)
        req_data['schema_version']='city-engine/2'
        requirements=MultiDayRequirements.model_validate(req_data)
        from ...db import connect
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');guard(db)
            saved=exploration_state.save(db,t.trip_id,constraints.model_dump(mode='json'),None,t.snapshot.workspace.get('rounds',0))
        planning_version=saved['version']
        result=exploration.engine.preview(session,t.trip_id,PreviewRequestV2(request_id=uuid5(UUID(t.id),'planning-preview'),
            expected_version=action['trip_version'],requirements=requirements),planning_version=planning_version)
        t.snapshot.workspace={'version':result.get('planning_version',planning_version),'constraints':constraints.model_dump(mode='json'),
            'result':result,'rounds':result.get('rounds',0)}
        from ..assistant import context as assistant_context
        t.snapshot=assistant_context.load(session,t.trip_id,'modify',t.text)
        plan=(((result.get('result') or {}).get('itineraries') or [None])[0]) if result.get('status') in ('preview','feasible') else None
        return {'status':'needs_confirmation' if result.get('status') in ('preview','feasible') else 'needs_data',
            'reply':('多天方案已准备好，请核对整套行程后确认保存。' if result.get('status') in ('preview','feasible') else
                     result.get('reply') or '当前条件未产生可确认的多天路线，请核对缺项或约束冲突。')+_budget_reply(
                         constraints.model_dump(mode='json'),plan,[{'activities':d['itinerary']['activities'],'legs':d['itinerary']['legs'],
                             'starts_at':d['requirements']['starts_at'],'ends_at':d['requirements']['ends_at']} for d in (plan or {}).get('days',())],
                         proposed.get('notes',[]),overnight=bool((plan or {}).get('overnights'))),
            'formal_state_changed':False,'planning_workflow':{'trip_id':t.trip_id,'execution':'completed_backend',
                'operation':action['operation'],'date_changed':action['date_changed'],'result':result}}
    constraints=action['constraints']
    if proposed.get('basket_proposal') or proposed.get('basket_choices') or conflicts:
        from ...db import connect
        # Save only the understood conditions; no basket write or route is authorized yet.
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');guard(db)
            saved=exploration_state.save(db,t.trip_id,constraints,None,t.snapshot.workspace.get('rounds',0))
        t.snapshot.workspace=saved
        t.snapshot.envelope=t.snapshot.envelope.model_copy(update={'planning_constraints':{
            'version':saved['version'],'constraints':constraints,'rounds':saved['rounds']}})
        result=exploration.unknown(''.join(c['question'] for c in conflicts) if conflicts else '确认素材篮变更后接着安排。',
            code='PLANNING_FIELD_CONFLICT' if conflicts else 'BASKET_CONFIRMATION_REQUIRED')
        result.update(constraints=constraints,planning_version=saved['version'],rounds=saved['rounds'],
                      basket_version=basket_version,trip_version=action['trip_version'])
        # Confirming a basket card resumes planning in the UI; an unresolved field
        # must not silently resume using its previously saved value.
        pending_basket=proposed.get('basket_proposal') if not conflicts else None
        reply=_basket_reply(pending_basket,t.snapshot)
        if action['date_changed']:reply='日期已记为 '+constraints['business_date']+'。'+reply
        reply+=''.join(proposed.get('notes',[]))
        reply+=''.join(c['question'] for c in conflicts)
        return {'status':'needs_confirmation' if pending_basket else 'needs_clarification','reply':reply,'formal_state_changed':False,
                **{key:proposed[key] for key in ('basket_proposal','basket_choices') if key in proposed and not conflicts},
                'planning_workflow':{'trip_id':t.trip_id,'execution':'completed_backend','operation':action['operation'],
                    'date_changed':action['date_changed'],'result':result}}
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
        elif set(missing)>={'starts_at','ends_at'}:reply='当天几点开始，最晚几点结束？';asked={'start_time','end_time'}
        elif 'ends_at' in missing:reply='最晚几点结束？';asked={'end_time'}
        elif 'starts_at' in missing:reply='当天几点开始？';asked={'start_time'}
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
    dropped=[LABELS[f] for f in dict.fromkeys(d['field'] for d in t.interpretation.get('dropped_fields',()) if d['check'] not in ('budget_basis_missing','choice_not_in_quote'))
             if f in LABELS and f not in asked and not filled.get(f)]
    if dropped:reply+=f"另外，{'、'.join(dropped)}没听准，能再说一下吗？"
    plan=(((result.get('planning') or {}).get('result') or {}).get('itineraries') or [None])[0] if result['status']=='feasible' and not missing else None
    reply+=_budget_reply(final,plan,[{'activities':plan['activities'],'legs':plan['legs'],
        'starts_at':final.get('starts_at'),'ends_at':final.get('ends_at')}] if plan else [],proposed.get('notes',[]))
    return {'status':status,'reply':reply,'formal_state_changed':False,
        'planning_workflow':{'trip_id':t.trip_id,'execution':'completed_backend','operation':action['operation'],
            'date_changed':action['date_changed'],'result':result}}


def execute(session,t,intent,proposed=None):
    result=_execute(session,t,intent,proposed)
    if any(d['check']=='budget_basis_missing' for d in t.interpretation.get('dropped_fields',())):
        result['reply']+='这笔预算是所有人合计，还是每人？'
    return result
