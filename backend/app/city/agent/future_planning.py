"""Patch an explicitly named future day using grounded interpretation and saved requirements."""
from datetime import datetime
from uuid import UUID,uuid5
from ..assistant import tools
from ..engine import service as engine
from ..engine.models import MultiDayRequirements,TripGoal
from ..schema import TZ
from . import date_range


def applies(snapshot,intent,text):
    if not snapshot.multi_requirements or not snapshot.multi_itinerary:return False
    # Confirmed trips use an explicit dated edit; fresh planning stays with the existing workflow.
    return bool(intent.planning and (snapshot.row['status']!='DRAFT' or date_range.expressions(text)
        or date_range.DAY_NUMBER.search(text)))


def preview(session,trip_id,snapshot,intent,request_id,text):
    from .planning_intent import _resolve_endpoint,_choice_facts
    day_id=tools.selected_future_day_scope(snapshot,text)
    full=snapshot.multi_requirements;day=next(d for d in full.days if d.day_id==day_id)
    today=snapshot.planning.assessed_at.astimezone(TZ).date();updates={};goals=list(full.goals);cancel=[]
    allowed={'date','start_time','end_time','pace','objective','mode','origin','destination'}
    def clarify(message):return {'status':'needs_clarification','reply':message}
    for field in intent.planning.fields:
        if field.field not in allowed or field.scope or field.value is None:
            return clarify('本次只修改所选未来日的时间、地点或偏好；全程预算、日期范围和酒店请另行核对。')
        if field.day_ref:
            ref=date_range.date_for_ref(date_range.day_reference(field.day_ref,text,today),full.start_date,full.end_date)
            if ref!=day.business_date:return clarify('本轮包含不同日期的条件，请一次选择一个未来日。')
        if field.field=='date':continue
        if field.field in ('start_time','end_time'):
            updates['starts_at' if field.field=='start_time' else 'ends_at']=datetime.fromisoformat(day.business_date.isoformat()+'T'+field.value+':00+08:00')
        elif field.field in ('origin','destination'):
            place,_=_resolve_endpoint(snapshot.planning.facts,field.value)
            if not place:return clarify('地点未能唯一匹配，请在行程页选择已有地点。')
            updates[field.field+'_place_id']=place.fact_id
        elif field.field=='mode':updates['modes']=(field.value,)
        else:updates[field.field]=field.value
    from ..engine.future_day import mutable_ids
    mutable=mutable_ids(full,snapshot.multi_itinerary,day_id)
    for choice in intent.planning.choices:
        matches=_choice_facts(choice.name,snapshot)
        if len(matches)!=1:return clarify('目标名称未能唯一匹配，请在行程页选择已有目标。')
        fact=matches[0];kind='session' if fact.kind=='show_session' else 'place' if fact.kind=='vendor' else fact.kind
        target=fact.place_id if fact.kind=='vendor' else fact.fact_id
        existing=[g for g in goals if g.goal_id in mutable and g.target_kind==kind and g.target_id==target]
        if choice.status=='standby':
            if len(existing)!=1:return clarify('请明确所选日期中要取消的目标。')
            if existing[0].required:cancel.append(existing[0].goal_id)
            goals=[g for g in goals if g.goal_id!=existing[0].goal_id]
        elif existing:
            if choice.priority_stated:
                goals=[g.model_copy(update={'required':choice.priority=='must'}) if g==existing[0] else g for g in goals]
        else:
            if fact.kind not in ('place','show_session','food','vendor','program'):return clarify('此目标暂不能直接排入，请在素材篮核对。')
            goals.append(TripGoal(goal_id='future:'+uuid5(UUID(str(request_id)),fact.fact_id).hex,
                target_kind=kind,target_id=target,kind='show' if kind in ('session','program') else 'dining' if kind=='food' or fact.kind=='vendor' else 'visit',
                required=choice.priority=='must',allowed_dates=(day.business_date,),fixed_date=day.business_date))
    for constraint in intent.constraints:
        key=constraint.field
        if constraint.value is None:return clarify('请明确需要修改的每日条件。')
        if key in ('pace','objective'):
            if key in updates and updates[key]!=constraint.value:return clarify('同一条件出现不同取值，请确认。')
            updates[key]=constraint.value
        elif key=='walking_seconds':
            updates['max_walking_seconds']=constraint.value if constraint.strength=='hard' else day.max_walking_seconds
            if constraint.strength!='hard':updates['objective']='less_walking'
        elif key=='return_by':
            value=datetime.fromisoformat(day.business_date.isoformat()+'T'+constraint.value+':00+08:00')
            if 'ends_at' in updates and updates['ends_at']!=value:return clarify('结束时间出现不同取值，请确认。')
            updates['ends_at']=value
        else:return clarify('此条件请在行程页选择目标和时间后核对。')
    if len(cancel)>1:return clarify('请逐项确认取消必去目标。')
    changed=MultiDayRequirements.model_validate(full.model_dump()|{'days':[d.model_copy(update=updates) if d.day_id==day_id else d for d in full.days],'goals':goals})
    body=tools.adjustment_request(snapshot,changed,uuid5(UUID(str(request_id)),'future-plan'),scope='selected_future_day',day_id=day_id,skipped_goal_ids=tuple(cancel))
    result=engine.preview(session,trip_id,body,replanning=True)
    result['adjustment_notes']=[f'本次仅调整{day.business_date.isoformat()}。']
    return result
