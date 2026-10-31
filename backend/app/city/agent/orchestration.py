"""Backend coordinator for the existing exploration workflow; never a solver/commit."""
import copy
import time
from datetime import date,datetime,timedelta
from uuid import UUID,uuid5
from fastapi import HTTPException
from .. import exploration,exploration_state,basket,state,travel_services
from ..schema import TZ
from .planning_intent import proposal
from .dates import resolve_date

QUICK_PLACE_ID='pl_mvp_kaifengfu_meeting'  # data/mvp/city-facts-reference-2026-10-25.json 开封府集合点，有坐标。
QUICK_FIELDS=frozenset({'business_date','starts_at','ends_at','origin_place_id','destination_place_id'})
LABELS={'date':'日期','start_time':'出发时间','end_time':'结束时间','origin':'出发地','destination':'返回地',
        'budget_fen':'预算','mode':'出行方式','pace':'节奏','objective':'偏好','city':'城市','trip_days':'游玩天数'}



def _ready_reply(result):
    """Say which wanted stops the default (first) alternative leaves out, so the visitor need not spot it."""
    first=(((result.get('planning') or {}).get('result') or {}).get('itineraries') or [{}])[0]
    kept={a.get('goal_id') for a in first.get('activities',())}
    names=result.get('activity_names') or {}
    left=[names[g] for g in result.get('goal_keys') or {} if g not in kept and names.get(g)]
    return '方案已准备好。'+(f"时间不够，这版没排进：{'、'.join(left)}。" if left else '')+'请点“确认方案”保存。'


def _missing_items(budget):
    """Group unknown budget items by kind, in visitor words, like the per-person check."""
    items=[i for i in budget['items'] if i.get('amount_fen') is None]
    tickets=list(dict.fromkeys(i['label'] for i in items if i['category']=='tickets'))
    parts=[n+'门票' for n in tickets[:4]]+(['等景点门票'] if len(tickets)>4 else [])
    if any(i['category']=='transport' for i in items):parts.append('部分交通费用')
    if any(i['category']=='dining' for i in items):parts.append('餐饮')
    lodging=[i for i in items if i['category']=='lodging']
    if lodging:parts.append('住宿' if all(i['label']=='住宿' for i in lodging) else '住宿（'+'、'.join(i['label'] for i in lodging if i['label']!='住宿')+'）')
    parts+=list(dict.fromkeys(i['label'] for i in items if i['category'] in ('other','spent')))
    # Requirements that are not cost items (e.g. a missing party size) carry no "：".
    parts+=[u for u in budget['unknown_items'] if '：' not in u]
    return '、'.join(parts) or '部分费用'


def _budget_reply(constraints,itinerary,days,notes,overnight=False):
    """The per-person budget check replaces the "recorded" note once a plan exists."""
    budget=itinerary.get('budget') if isinstance(itinerary,dict) else getattr(itinerary,'budget',None)
    if budget:
        if hasattr(budget,'model_dump'):budget=budget.model_dump()
        from .budget_check import recorded_note,_yuan
        clean=''.join(n for n in notes if n!=recorded_note(constraints))
        if budget['limit_fen'] is not None:clean+=f"全程预算{_yuan(budget['limit_fen'])}元，"
        missing=_missing_items(budget)
        suffix=f'；仍有未计入项，暂不能确认预算够用（未计入：{missing}）。' if budget['unknown_items'] else \
            '；当前核算超过预算，方案仍可确认。' if budget['status']=='over' else '；当前核算在预算内（含估计金额）。' if budget['status']=='within_budget' else '。'
        return clean+f"全程已计入费用约{_yuan(budget['known_total_fen'])}元"+suffix
    from .budget_check import check,recorded_note
    sentence=check(constraints,itinerary,days=days,overnight=overnight)
    recorded=recorded_note(constraints)
    return ''.join(n for n in notes if not (sentence and n==recorded))+sentence


def _quick_parts(added,prefix=''):
    parts=[]
    if any(field=='date' for field,_ in added):
        value=next(value for field,value in added if field=='date')
        parts.append(f'明天（{value.month}月{value.day}日）'+('开始' if prefix else ''))
    times={field for field,_ in added if field in ('start_time','end_time')}
    if times=={'start_time','end_time'}:parts.append(prefix+'9:00—20:00')
    elif 'start_time' in times:parts.append(prefix+'9:00 开始')
    elif 'end_time' in times:parts.append(prefix+'20:00 结束')
    places={field for field,_ in added if field in ('origin','destination')}
    place_prefix='' if prefix=='每天' else prefix
    if places=={'origin','destination'}:parts.append(place_prefix+'从开封府出发并返回')
    elif 'origin' in places:parts.append(place_prefix+'从开封府出发')
    elif 'destination' in places:parts.append(place_prefix+'最后回到开封府')
    return parts


def _quick_reply(added,daily=False,day_added=()):
    parts=_quick_parts(added,'每天' if daily else '')
    for index,fields in day_added:parts+=_quick_parts(fields,f'第{index}天')
    return '没说的我先按'+'、'.join(parts)+'安排了，想改直接告诉我。' if parts else ''


def _quick_defaults(result, missing, today):
    """Fill the bounded single-day endpoint/time gaps for quick interaction."""
    constraints=dict(result.get('constraints') or {})
    missing=set(missing)
    added=[]
    if 'business_date' in missing:
        tomorrow=today+timedelta(days=1)
        constraints['business_date']=tomorrow.isoformat()
        added.append(('date',tomorrow))
    day=constraints.get('business_date')
    if 'starts_at' in missing:
        constraints['starts_at']=day+'T09:00:00+08:00'
        added.append(('start_time',None))
    if 'ends_at' in missing:
        constraints['ends_at']=day+'T20:00:00+08:00'
        added.append(('end_time',None))
    if 'origin_place_id' in missing:
        constraints['origin_place_id']=QUICK_PLACE_ID
        constraints['origin_text']=None
        added.append(('origin',None))
    if 'destination_place_id' in missing:
        constraints['destination_place_id']=QUICK_PLACE_ID
        constraints['destination_text']=None
        added.append(('destination',None))
    return constraints,added,_quick_reply(added)


def _quick_multiday_defaults(values, today, snapshot):
    """Fill only unmentioned multi-day windows and endpoints for quick interaction."""
    from .multi_day_intent import apply_day_fields,link_overnights
    constraints=copy.deepcopy(values)
    added=[]
    start=date.fromisoformat(constraints['start_date']) if constraints.get('start_date') else None
    end=date.fromisoformat(constraints['end_date']) if constraints.get('end_date') else None
    trip_days=constraints.get('trip_days')
    trip_nights=constraints.get('trip_nights')
    duration=trip_days if trip_days is not None else trip_nights+1 if trip_nights is not None else None
    if not start and (duration is not None or end):
        candidate=today+timedelta(days=1)
        candidate_end=end or candidate+timedelta(days=duration-1)
        if candidate_end and 1<=(candidate_end-candidate).days+1<=3:
            start=candidate;end=candidate_end
            constraints['start_date']=start.isoformat()
            constraints['end_date']=end.isoformat()
            added.append(('date',start))
        elif candidate_end:
            constraints['clarifications']=list(constraints.get('clarifications') or ())
            constraints['clarifications'].append('目前支持连续1至3天，请缩小日期范围。')
    elif start and not end and duration is not None:
        candidate_end=start+timedelta(days=duration-1)
        if 1<=(candidate_end-start).days+1<=3:
            end=candidate_end;constraints['end_date']=end.isoformat()
    if not (start and end):return constraints,added,_quick_reply(added,daily=True)
    length=(end-start).days+1
    if trip_days is not None and trip_days!=length:
        constraints['clarifications'].append('日期范围和游玩天数不一致，以哪个为准？')
    if trip_nights is not None and trip_nights!=length-1:
        constraints['clarifications'].append('游玩天数和过夜晚数不一致，额外住宿如何安排？')
    existing={day['business_date']:day for day in constraints['days']}
    dates=[start+timedelta(days=i) for i in range((end-start).days+1)]
    constraints['days']=[dict(existing.get(d.isoformat()) or {'business_date':d.isoformat(),'day_id':'day:'+d.isoformat()}) for d in dates]
    constraints=apply_day_fields(constraints,snapshot.planning.facts)
    pending=constraints.get('pending_fields') or ()
    unresolved={f['field'] for f in pending if not f.get('day_ref') and not f.get('scope')}
    day_added=[]
    for index,day in enumerate(constraints['days'],1):
        fields=[]
        iso=day['business_date']
        for field,key,value in (
            ('start_time','starts_at',iso+'T09:00:00+08:00'),
            ('end_time','ends_at',iso+'T20:00:00+08:00')):
            if field not in unresolved and not day.get(key):
                other=day.get('ends_at' if key=='starts_at' else 'starts_at')
                if other and (datetime.fromisoformat(value)>=datetime.fromisoformat(other) if key=='starts_at'
                        else datetime.fromisoformat(value)<=datetime.fromisoformat(other)):
                    constraints['clarifications'].append(iso+'结束时间须晚于开始时间，请核对。');continue
                day[key]=value;fields.append((field,None))
        for field,key,text_key in (
            ('origin','origin_place_id','origin_text'),('destination','destination_place_id','destination_text')):
            if field not in unresolved and not day.get(key) and not day.get(text_key):
                day[key]=QUICK_PLACE_ID;day[text_key]=None;fields.append((field,None))
        day_added.append((index,fields))
    selected=[o for o in constraints['overnights'] if o['source_kind']=='selected_hotel']
    constraints=link_overnights(constraints)
    constraints['overnights']=[next((old for old in selected if old['night_date']==o['night_date']),o)
                               for o in constraints['overnights']]
    constraints['clarifications']=list(dict.fromkeys(constraints['clarifications']))
    common=set.intersection(*(set(field for field,_ in fields) for _,fields in day_added))
    shared=[item for item in day_added[0][1] if item[0] in common]
    partial=[(index,[item for item in fields if item[0] not in common]) for index,fields in day_added]
    reply=_quick_reply(added+shared,daily=True,day_added=partial)
    return constraints,added+[item for _,fields in day_added for item in fields],reply


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
    if (t.body.interaction_mode=='quick' and proposed.get('planning_action',{}).get('constraints',{}).get('schema_version')!='city-constraints/2'
            and any(f.field=='trip_days' and f.value is None for f in intent.planning.fields)):
        from .multi_day_intent import prepare
        proposed=prepare(session,t.trip_id,t.snapshot,intent,t.text,today=t.interpretation_date,notes=proposed.get('notes',()))
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
        quick_reply=''
        if t.body.interaction_mode=='quick':
            today=t.interpretation_date or t.snapshot.envelope.identity.generated_at.astimezone(TZ).date()
            quick_values,_,quick_reply=_quick_multiday_defaults(constraints.model_dump(mode='json'),today,t.snapshot)
            if quick_values!=constraints.model_dump(mode='json'):
                constraints=MultiDayConstraints.model_validate(quick_values)
                missing=_questions(constraints.model_dump(mode='json'))
            if (not constraints.start_date and not constraints.end_date and constraints.trip_days is None
                    and constraints.trip_nights is None and not constraints.clarifications):
                missing=['这趟行程玩几天，或哪天结束？']
        if missing or conflicts or proposed.get('basket_proposal') or proposed.get('basket_choices'):
            from ...db import connect
            with connect() as db:
                db.execute('BEGIN IMMEDIATE');guard(db)
                saved=exploration_state.save(db,t.trip_id,constraints.model_dump(mode='json'),None,t.snapshot.workspace.get('rounds',0))
            pending=proposed.get('basket_proposal') if not conflicts else None
            reply=quick_reply+(''.join(c['question'] for c in conflicts) if conflicts else
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
            'reply':quick_reply+('多天方案已准备好，请核对整套行程后确认保存。' if result.get('status') in ('preview','feasible') else
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
    quick_reply=''
    if (t.body.interaction_mode=='quick' and result.get('constraints',{}).get('schema_version')!='city-constraints/2'
            and missing and set(missing)<=QUICK_FIELDS):
        today=t.interpretation_date or t.snapshot.envelope.identity.generated_at.astimezone(TZ).date()
        quick_constraints,_,quick_reply=_quick_defaults(result,missing,today)
        planning_version=result['planning_version']
        quick_result=exploration.preview_plan(UUID(t.trip_id),exploration.Plan(
            request_id=uuid5(UUID(t.id),'planning-preview-quick'),expected_version=action['trip_version'],
            basket_version=basket_version,planning_version=result['planning_version'],
            constraints=exploration.Constraints.model_validate(quick_constraints)),session,execution_guard=guard)
        result=quick_result
        t.snapshot.workspace={'version':result['planning_version'],'constraints':result['constraints'],'result':result,'rounds':result['rounds']}
        env=t.snapshot.envelope
        t.snapshot.envelope=env.model_copy(update={'planning_constraints':{
            'version':result['planning_version'],'constraints':result['constraints'],'rounds':result['rounds']}})
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
    reply=quick_reply+reply
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
    from . import future_planning
    if future_planning.applies(t.snapshot,intent,t.text):
        from .service import _preview_result
        return _preview_result(future_planning.preview(session,t.trip_id,t.snapshot,intent,t.id,t.text),t.snapshot,intent)
    result=_execute(session,t,intent,proposed)
    if any(d['check']=='budget_basis_missing' for d in t.interpretation.get('dropped_fields',())):
        result['reply']+='这笔预算是所有人合计，还是每人？'
    return result
