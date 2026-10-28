"""Collect evidenced daily intentions in the shared workspace, never a formal route."""
import copy
from datetime import date,datetime,timedelta
from uuid import UUID,uuid5
from ..engine.models import MultiDayConstraints,TripGoal
from ..schema import TZ
from . import date_range


def is_multi(base,planning):
    return base.get('schema_version')=='city-constraints/2' or any(
        f.field in ('start_date','end_date','trip_nights') or f.field=='trip_days' and f.value and f.value>1
        or f.day_ref or f.scope for f in planning.fields) or any(c.day_ref for c in planning.choices)


def _questions(values):
    questions=list(values.get('clarifications') or [])
    if not values.get('start_date'):return questions or ['你打算哪天开始这趟行程？']
    if not values.get('end_date'):return questions or ['这趟行程玩几天，或哪天结束？']
    for f in values.get('pending_fields',[]):
        if not f.get('day_ref') and not f.get('scope'):
            label={'start_time':'出发时间','end_time':'结束时间','origin':'出发地','destination':'返回地'}[f['field']]
            questions.append(f'这个{label}用于哪一天，还是每天？')
    for d in values.get('days',[]):
        for key,label in (('starts_at','几点开始'),('ends_at','最晚几点结束')):
            if not d.get(key):questions.append(d['business_date']+label+'？')
    for a,b in zip(values.get('days',[]),values.get('days',[])[1:]):
        if not a.get('destination_place_id') or not b.get('origin_place_id'):
            questions.append(a['business_date']+'晚上在哪个已知地点过夜，并从那里开始第二天？')
        elif a['destination_place_id']!=b['origin_place_id']:
            questions.append(a['business_date']+'的终点和次日起点不同，请核对过夜地点。')
    days=values.get('days',[])
    if days:
        if not days[0].get('origin_place_id') and not days[0].get('origin_text'):questions.append('第一天从哪里出发？')
        if not days[-1].get('destination_place_id') and not days[-1].get('destination_text'):questions.append('最后一天回到哪里？')
    return list(dict.fromkeys(questions))


def lodging_questions(values,locations,catalog):
    from .planning_intent import _resolve_endpoint
    days=values.get('days',[]);questions=[]
    for location in locations:
        place,_=_resolve_endpoint(catalog,location)
        if len(days)>1 and (not place or any(a.get('destination_place_id')!=place.fact_id or b.get('origin_place_id')!=place.fact_id
                for a,b in zip(days,days[1:]))):
            questions.append(f'住宿位置“{location}”尚未与行程过夜地点对上，请确认在哪个已知地点过夜。')
    return questions


def prepare(session,trip_id,snapshot,intent,text,*,today=None,notes=()):
    from .requirement_context import planning_values
    from .planning_intent import proposal,_resolve_endpoint,_choice_facts,_fact_names
    p=intent.planning;today=today or snapshot.envelope.identity.generated_at.astimezone(TZ).date()
    base=planning_values(snapshot);multi=base.get('schema_version')=='city-constraints/2'
    values=MultiDayConstraints.model_validate(base if multi else {'schema_version':'city-constraints/2'}).model_dump(mode='json')
    before=copy.deepcopy(values);notes=list(notes);fields=p.fields
    range_fields=[f for f in fields if f.field in ('date','start_date','end_date','trip_days','trip_nights')]
    if range_fields:values['clarifications']=[]
    for c in intent.constraints:
        if c.field in ('pace','objective') and c.value is not None:
            if any(f.field==c.field and f.value!=c.value for f in fields):
                values['clarifications'].append('同一项'+('节奏' if c.field=='pace' else '偏好')+'说了两个不同的值，以哪个为准？')
            else:values[c.field]=c.value
    for f in range_fields:
        key='start_date' if f.field=='date' else f.field
        if f.value is None:values[key]=None;continue
        if f.field in ('trip_days','trip_nights'):values[key]=f.value;continue
        try:values[key]=date_range.field_date(f,today).isoformat()
        except ValueError:continue
    start=date.fromisoformat(values['start_date']) if values['start_date'] else None
    end=date.fromisoformat(values['end_date']) if values['end_date'] else None
    new_days=next((f for f in range_fields if f.field=='trip_days'),None)
    # A new duration changes the end only when no independent end date was supplied.
    if start and values['trip_days'] and not any(f.field=='end_date' for f in range_fields) and (new_days or not end):
        end=start+timedelta(days=values['trip_days']-1);values['end_date']=end.isoformat()
    weekend=next((e for f in range_fields for e in date_range.expressions(f.quote) if '周末' in e and not e.startswith(('下','下个'))),None)
    if weekend and today.weekday()==6 and (values['trip_days'] or 1)>1:
        values.update(start_date=None,end_date=None)
        start=end=None;values['clarifications'].append('从今天开始两天，还是下周末两天？')
    if start and start<today:values['clarifications'].append('首次出发日期不能在过去，请确认开始日期。')
    if start and end:
        length=(end-start).days+1
        if length<1:
            values['clarifications'].append('结束日期早于开始日期；若跨年，请写明两端年份。')
        elif length>3:values['clarifications'].append('目前支持连续1至3天，请缩小日期范围。')
        if values['trip_days'] is not None and values['trip_days']!=length:
            values['clarifications'].append('日期范围和游玩天数不一致，以哪个为准？')
        if values['trip_nights'] is not None and values['trip_nights']!=length-1:
            values['clarifications'].append('游玩天数和过夜晚数不一致，额外住宿如何安排？')
        if not 1<=length<=3:
            values.update(end_date=None,days=[],goals=[],overnights=[]);end=None
    if not start or not end:
        values.update(days=[],goals=[],overnights=[])
    else:
        dates=[start+timedelta(days=i) for i in range((end-start).days+1)]
        previous={d['business_date']:d for d in before['days']}
        if not multi and base.get('business_date') in {d.isoformat() for d in dates}:
            previous[base['business_date']]={k:v for k,v in base.items() if k in ('business_date','starts_at','ends_at','origin_place_id','destination_place_id','origin_text','destination_text')}
        values['days']=[previous.get(d.isoformat(),{'business_date':d.isoformat(),'day_id':'day:'+d.isoformat()}) for d in dates]
        removed={d['business_date'] for d in before['days']}-{d.isoformat() for d in dates}
        impacts=[*before['range_impacts'],*(f'移除日期 {d} 的日条件/端点需核对。' for d in sorted(removed))]
        for g in before['goals']:
            if set(g['allowed_dates'])&removed:impacts.append('目标 '+g['goal_id']+' 的日期被移除，需明确处理。')
        for o in before['overnights']:
            if o['night_date'] in removed or o['to_day_id'][4:] in removed:impacts.append('过夜点 '+o['overnight_id']+' 的日期被移除，需明确处理。')
        values['range_impacts']=list(dict.fromkeys(impacts))
        values['goals']=[g for g in before['goals'] if not set(g['allowed_dates'])&removed]
        values['overnights']=[o for o in before['overnights'] if o['night_date'] not in removed and o['to_day_id'][4:] not in removed]
    pending=list(values['pending_fields'])
    for f in fields:
        if f.field in ('start_time','end_time','origin','destination'):
            ref=date_range.day_reference(f.day_ref or f.quote,text,today) if f.day_ref else None
            item={'field':f.field,'value':f.value,'day_ref':ref,'scope':f.scope}
            pending=[v for v in pending if (v['field'],v.get('day_ref'),v.get('scope'))!=(f.field,ref,f.scope)]
            if f.scope:pending=[v for v in pending if v['field']!=f.field or v.get('day_ref') or v.get('scope')]
            pending.append(item)
        elif f.field in ('pace','objective') and f.value:values[f.field]=f.value
        elif f.field=='mode' and f.value:values['modes']=[f.value]
        elif f.field=='budget_fen':notes.append('多天全程预算尚未接通，这笔金额未用于多天求解。')
        elif f.field=='city' and f.value not in ('开封','开封市'):
            return {'status':'needs_data','reply':'目前只支持开封的行程。'}
    remaining=[]
    for f in pending:
        if not start or not end or not (f.get('scope') or f.get('day_ref')):
            remaining.append(f);continue
        selected=values['days'] if f.get('scope') else [d for d in values['days'] if date.fromisoformat(d['business_date'])==date_range.date_for_ref(f['day_ref'],start,end)]
        if not selected:
            remaining.append(f);values['clarifications'].append('指定的日不在这趟日期范围内，请核对。');continue
        for d in selected:
            key={'start_time':'starts_at','end_time':'ends_at','origin':'origin_place_id','destination':'destination_place_id'}[f['field']]
            if f['field'].endswith('_time'):
                d[key]=d['business_date']+'T'+f['value']+':00+08:00' if f['value'] else None
                if d.get('starts_at') and d.get('ends_at') and d['ends_at']<=d['starts_at']:
                    d[key]=None;values['clarifications'].append(d['business_date']+'结束时间须晚于开始时间，请核对。')
            else:
                place,_=_resolve_endpoint(snapshot.planning.facts,f['value']) if f['value'] else (None,False)
                d[key]=place.fact_id if place else None;d[f['field']+'_text']=None if place else f['value']
    values['pending_fields']=remaining
    # A known overnight boundary joins the two adjacent endpoints; no hotel selection is promoted.
    nights=[]
    for a,b in zip(values['days'],values['days'][1:]):
        place=a.get('destination_place_id');other=b.get('origin_place_id')
        if place and other and place==other:
            nights.append({'overnight_id':'night:'+a['business_date'],'from_day_id':'day:'+a['business_date'],
                'to_day_id':'day:'+b['business_date'],'night_date':a['business_date'],'place_id':place,'source_kind':'user_place'})
    values['overnights']=nights
    service=intent.service_query
    locations=[f.value for f in service.fields if f.field=='location_text'] if service and service.category=='hotels' else []
    saved_service=snapshot.services.get('requirements') or {}
    if not locations and saved_service.get('category')=='hotels' and saved_service.get('location_text'):
        locations=[saved_service['location_text']]
    values['clarifications']=[q for q in values['clarifications'] if not q.startswith('住宿位置“')]
    values['clarifications'].extend(lodging_questions(values,locations,snapshot.planning.facts))
    basket_result={}
    if p.choices:
        single=copy.copy(snapshot);single.workspace={'constraints':{},'version':snapshot.workspace.get('version',0)}
        single.envelope=snapshot.envelope.model_copy(update={'requirements':{}})
        basket_result=proposal(session,trip_id,single,intent.model_copy(update={'planning':p.model_copy(update={
            'fields':(),'choices':tuple(c.model_copy(update={'day_ref':None}) for c in p.choices)})}),text,today=today)
        notes.extend(basket_result.get('notes',[]))
        for choice in p.choices:
            if not choice.day_ref or not start or not end:continue
            day=date_range.date_for_ref(date_range.day_reference(choice.day_ref,text,today),start,end)
            if not start<=day<=end:
                values['clarifications'].append('目标指定的日期不在这趟行程内，请核对。');continue
            resolved=_choice_facts(choice.name,snapshot)
            if len(resolved)!=1:continue
            fact=resolved[0];kind=fact.kind;identity=fact.fact_id
            key=kind+':'+identity;gid='basket:'+uuid5(UUID(str(trip_id)),key).hex
            values['goals']=[g for g in values['goals'] if g['goal_id']!=gid]
            if choice.status=='standby':continue
            values['goals'].append(TripGoal(goal_id=gid,target_kind=kind,target_id=identity,
                required=choice.priority=='must',importance={'must':10,'want':6,'optional':1}[choice.priority],
                kind='dining' if kind=='food' else 'show' if kind in ('program','session') else 'visit',
                allowed_dates=(day,),fixed_date=day).model_dump(mode='json'))
    values['clarifications']=list(dict.fromkeys(values['clarifications']))
    validated=MultiDayConstraints.model_validate(values)
    return {'status':'planning_input','reply':'好的，正在安排。','notes':list(dict.fromkeys(notes)),
        **{k:v for k,v in basket_result.items() if k in ('basket_proposal','basket_choices')},
        'planning_action':{'trip_id':str(trip_id),'operation':p.operation,'constraints':validated.model_dump(mode='json'),
            'planning_version':snapshot.workspace.get('version',0),'basket_version':snapshot.envelope.basket['version'],
            'trip_version':snapshot.row['version'],'date_changed':bool(range_fields)}}
