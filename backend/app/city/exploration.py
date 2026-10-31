"""UI adapters to the existing planner and agent. No second solver or agent."""
import json
import time
from contextlib import ExitStack
from uuid import UUID, uuid5
from typing import Annotated, Literal
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, Discriminator, Field, Tag, field_validator, model_validator
from pydantic import ValidationError
from ..db import connect
from ..memory.session import current, Session, limit
from ..memory.storage import stamp
from . import facts
from . import basket, state, exploration_state
from .schema import Strict, EntityId
from .preferences import TripPreferences, CATEGORY_NAMES
from .engine import service as engine
from .engine.models import Goal, TripGoal, Requirements, PreviewRequest, ReplanRequest, MultiDayConstraints, SolveResult
from .versions import guard_single_day, read_requirements, single_day

router=APIRouter(prefix='/api/city/v1/trips/{trip_id}/exploration',tags=['Travel basket planning'])


class Constraints(Strict):
    preferences:TripPreferences | None = None
    business_date:date | None = None
    starts_at:AwareDatetime | None = None
    ends_at:AwareDatetime | None = None
    origin_place_id:EntityId | None = None
    destination_place_id:EntityId | None = None
    # Visitor-reported names are intentions, never fact IDs or solver inputs.
    origin_text:str | None=Field(default=None,max_length=120)
    destination_text:str | None=Field(default=None,max_length=120)
    pace:Literal['relaxed','standard','compact']='standard'
    objective:Literal['balanced','less_walking','less_waiting','lower_cost']='balanced'
    modes:tuple[Literal['walking','driving','transit'],...]=Field(default=('walking',),min_length=1,max_length=3)
    budget_fen:int | None=Field(default=None,ge=0,le=100000000)
    # A per-person budget is checked against the plan's known per-person costs after
    # preview; it never enters the solver, whose budget is a hard total.
    per_person_budget_fen:int | None=Field(default=None,ge=0,le=100000000)
    per_person_budget_daily:bool=False
    party_size:int | None=Field(default=None,ge=1,le=50)

    @field_validator('origin_text','destination_text',mode='before')
    @classmethod
    def trim_location(cls,value):
        return (value.strip() or None) if isinstance(value,str) else value

    @model_validator(mode='before')
    @classmethod
    def prefer_selected_place(cls,value):
        if isinstance(value,dict):
            value=dict(value)
            for endpoint in ('origin','destination'):
                if value.get(endpoint+'_place_id'):value[endpoint+'_text']=None
        return value


AGENT_ONLY=('per_person_budget_fen','per_person_budget_daily','party_size')


class Plan(Strict):
    request_id:UUID
    expected_version:int=Field(ge=1)
    basket_version:int=Field(ge=0)
    planning_version:int | None=Field(default=None,ge=0)
    constraints:Constraints


class PlanV2(Plan):
    constraints:MultiDayConstraints


class Conflict(Strict):
    goal_id:str
    key:str
    name:str
    reason_code:str
    reason:str


class ConstraintPatch(Strict):
    starts_at:AwareDatetime | None=None
    ends_at:AwareDatetime | None=None
    business_date:date | None=None


class NegotiationOption(Strict):
    id:str
    kind:Literal['priority','constraints']
    label:str
    requires_consent:bool=True
    patch:ConstraintPatch | None=None
    object_key:str | None=None
    object_keys:tuple[str,...] | None=None
    priority:Literal['want'] | None=None


class ConflictAdjustments(Strict):
    conflicts:tuple[Conflict,...]
    options:tuple[NegotiationOption,...]
    reply:str


def constraint_schema(value):
    c=value.get('constraints',{}) if isinstance(value,dict) else getattr(value,'constraints',None)
    return c.get('schema_version','legacy') if isinstance(c,dict) else 'city-constraints/2' if isinstance(c,MultiDayConstraints) else 'legacy'


PlanBody=Annotated[Annotated[Plan,Tag('legacy')]|Annotated[PlanV2,Tag('city-constraints/2')],Discriminator(constraint_schema)]


def unknown(message, ids=(), code='DATA_PENDING'):
    return {'status':'unknown','reply':message,'issues':[{'code':code,'message':message,'affected_ids':list(ids)}],
            'options':[],'planning':None,'formal_state_changed':False}


ENDPOINT_REPLACEMENT_WARNING='起终点按同一位置的园外集合点计算，避免把返回误算为再次入园。'


def planning_endpoint(items, place_id):
    place=items.get(('place',place_id))
    if not isinstance(place,facts.Place) or place.place_type!='entrance' or not place.parent_place_id:
        return place_id
    if (place.longitude is None or place.latitude is None or place.coordinate_system=='unknown'
            or not (-180<=place.longitude<=180 and -90<=place.latitude<=90)):
        return place_id
    outside=[p.fact_id for p in items.values() if isinstance(p,facts.Place)
             and p.parent_place_id is None and p.place_type=='transport'
             and (p.longitude,p.latitude,p.coordinate_system)==(place.longitude,place.latitude,place.coordinate_system)]
    return outside[0] if len(outside)==1 else place_id


UNSCHEDULED_REASON_RULES=(
    (frozenset({'OPENING_UNKNOWN','OPENING_AMBIGUOUS_OR_STALE'}),'opening_unknown','这天的营业时间资料不全'),
    (frozenset({'TIME_WINDOW_EMPTY'}),'closed_in_window','开放时间和你的出行时段对不上'),
    (frozenset({'SESSION_TIME_UNKNOWN','SESSION_OUTSIDE_OPENING_OR_GOAL_WINDOW','SESSION_NOT_READY'}),'no_session','这天没有可安排的场次'),
    (frozenset({'RESERVATION_NOT_CONFIRMED','TICKET_NOT_CONFIRMED'}),'access_unconfirmed','需要先确认预约或门票'),
    (frozenset({'COST_UNKNOWN'}),'cost_unknown','费用未知，按你的预算条件排不进'),
)
UNSCHEDULED_FALLBACK=('data_missing','位置或交通资料不全')
UNSCHEDULED_TOO_FAR=('too_far_alone','单独去也来不及：路程加停留超出你的时间段')
UNSCHEDULED_CONFLICT=('time_conflict','和其他地点放在一起时间不够')


def _unscheduled_reason(reasons):
    reasons=set(reasons)
    for expected,code,reason in UNSCHEDULED_REASON_RULES:
        if reasons & expected:return code,reason
    return UNSCHEDULED_FALLBACK if reasons else None


def _has_usable_transport(problem, goal_id):
    """Return false only when the prepared target has no known path to the day endpoints."""
    activities=[a for a in problem.activities if a.goal_id==goal_id]
    if not activities:return True
    origin=problem.requirements.origin_place_id;destination=problem.requirements.destination_place_id
    return any(problem.matrix.get((origin,a.place_id)) and problem.matrix.get((a.place_id,destination)) for a in activities)


def _unscheduled(solve_result, req, ctx, goal_keys, goal_meta):
    """Explain omitted basket goals without changing the solver's selected activities."""
    itineraries=solve_result.get('itineraries') or ()
    if not itineraries:return {}
    blocked={}
    for item in (solve_result.get('metrics') or {}).get('blocked_candidates') or ():
        blocked.setdefault(item.get('goal_id'),set()).update(item.get('reasons') or ())
    try:problem=engine.prepare(req,ctx)
    except Exception:problem=None
    solo_cache={};solo_count=0
    output={}
    for alternative_index,itinerary in enumerate(itineraries):
        kept={a.get('goal_id') for a in itinerary.get('activities') or ()}
        omitted=[gid for gid in goal_keys if gid not in kept and goal_meta.get(gid,{}).get('status') not in ('standby','arranged')]
        rows=[]
        for gid in omitted:
            meta=goal_meta.get(gid,{})
            classified=_unscheduled_reason(blocked.get(gid,()))
            if classified is None and problem is not None and not _has_usable_transport(problem,gid):
                classified=UNSCHEDULED_FALLBACK
            if classified is None:
                if gid in solo_cache:classified=solo_cache[gid]
                elif alternative_index==0 and solo_count<3:
                    solo_count+=1
                    goal=meta.get('goal')
                    try:
                        solo=engine.plan_trip(req.model_copy(update={'goals':(goal,), 'alternatives':1}),ctx)
                        classified=UNSCHEDULED_TOO_FAR if solo.status=='infeasible' else UNSCHEDULED_CONFLICT
                    except Exception:
                        classified=UNSCHEDULED_CONFLICT
                    solo_cache[gid]=classified
                else:classified=UNSCHEDULED_CONFLICT
            rows.append({'goal_id':gid,'key':meta.get('key',goal_keys[gid]),'name':meta.get('name') or '资料待添加',
                         'reason_code':classified[0],'reason':classified[1]})
        if rows:output[str(alternative_index)]=rows
    return output


# 前端预览等待 60 秒、对话步骤 55 秒（E17），给交通补齐与调整求解共留 15 秒。
CONFLICT_ADJUSTMENT_SECONDS=15
# 调整候选（延后、提前、换天、改为想去）共享的求解次数上限；单独试排诊断不计入。
MAX_CONFLICT_ADJUSTMENT_CANDIDATES=8


def _conflict_adjustments(solved,req,ctx,goal_keys,goal_meta,*,replanning=False,preferences=None):
    if solved.itineraries:return None
    required=[g for g in req.goals if g.required]
    if not required:return None
    issues={i.code for i in solved.issues};blocked={}
    for item in solved.metrics.get('blocked_candidates',()):
        blocked.setdefault(item.get('goal_id'),set()).update(item.get('reasons',()))
    temporal={'TIME_WINDOW_EMPTY','SESSION_OUTSIDE_OPENING_OR_GOAL_WINDOW'}
    def temporal_goals(goals,blocked):
        return [g for g in goals if g.required and blocked.get(g.goal_id,set()) & temporal
                and not blocked[g.goal_id]-temporal-{'NO_USABLE_VARIANT'}]
    affected=temporal_goals(required,blocked)
    hard=solved.status=='infeasible' and 'HARD_CONSTRAINT_CONFLICT' in issues
    if not hard and not ('REQUIRED_GOAL_UNAVAILABLE' in issues and affected):return None
    started=time.monotonic();count=0;rows=[];options=[];unfinished=False
    provider=None;quotes={};metrics={'cache_hits':0,'budget_skipped':0}
    def solve(candidate,*,diagnostic=False):
        nonlocal count,provider,unfinished
        problem=engine.prepare(candidate,ctx)
        if problem.issues:
            blocked={}
            for item in problem.blocked:
                blocked.setdefault(item.get('goal_id'),set()).update(item.get('reasons',()))
            # Known time conflicts cannot be repaired by fetching travel estimates.
            if temporal_goals(candidate.goals,blocked):
                return SolveResult(status='infeasible',computed_at=ctx.assessed_at)
            unfinished=True;return None
        remaining=CONFLICT_ADJUSTMENT_SECONDS-(time.monotonic()-started)
        if (not diagnostic and count>=MAX_CONFLICT_ADJUSTMENT_CANDIDATES) or remaining<0.05 or (provider and provider.request_count>=provider.max_requests):
            unfinished=True;return None
        candidate=candidate.model_copy(update={'alternatives':1,'solve_seconds':min(req.solve_seconds,1,remaining)})
        if provider is None and not ctx.synthetic and engine.live_travel.enabled():
            provider=engine.live_travel.provider_from_environment()
            resources.callback(provider.close)
            provider.max_requests=min(provider.max_requests,30)
        context=engine.live_travel.enrich(ctx,candidate,provider=provider,deadline=started+CONFLICT_ADJUSTMENT_SECONDS,
            clock=time.monotonic,quotes=quotes,metrics=metrics)
        if metrics['budget_skipped']:unfinished=True
        # Keep the solve budget for prepared candidates; missing facts remain unfinished.
        if engine.prepare(candidate,context).issues:
            unfinished=True;return None
        remaining=CONFLICT_ADJUSTMENT_SECONDS-(time.monotonic()-started)
        if remaining<0.05:
            unfinished=True;return None
        candidate=candidate.model_copy(update={'solve_seconds':min(candidate.solve_seconds,remaining)})
        if not diagnostic:count+=1
        result=engine.plan_trip(candidate,context)
        if result.status not in ('feasible','infeasible'):unfinished=True
        return result
    def name(g):
        target=ctx.facts.get(('show_session' if g.target_kind=='session' else g.target_kind,g.target_id))
        if isinstance(target,facts.ShowSession):target=ctx.facts.get(('program',target.program_id))
        return goal_meta.get(g.goal_id,{}).get('name') or getattr(target,'name','资料待添加')
    def included(result):
        return '、'.join(dict.fromkeys(goal_meta.get(a.goal_id,{}).get('name') or
            getattr(ctx.facts.get(('place',a.place_id)),'name','资料待添加') for a in result.itineraries[0].activities))
    with ExitStack() as resources:
        for goal in required if hard else affected:
            if hard:
                solo=solve(req.model_copy(update={'goals':(goal,)}),diagnostic=True)
                code,reason=(UNSCHEDULED_TOO_FAR if solo.status=='infeasible' else UNSCHEDULED_CONFLICT) if solo and solo.status in ('feasible','infeasible') else ('calculation_pending','单独安排尚未完成核算')
                others='、'.join(name(g) for g in required if g.goal_id!=goal.goal_id)
                if code=='time_conflict' and others:reason='和'+others+'放在一起时间不够'
            else:code,reason=_unscheduled_reason(blocked[goal.goal_id])
            rows.append({'goal_id':goal.goal_id,'key':goal_keys.get(goal.goal_id,goal.goal_id),
                         'name':name(goal),'reason_code':code,'reason':reason})
        ids={r['goal_id'] for r in rows};keys=[r['key'] for r in rows]
        relaxable=all(goal_meta.get(gid,{}).get('status')=='pending' and not goal_meta[gid].get('locked') and gid in goal_keys for gid in ids)
        names='、'.join(r['name'] for r in rows)
        # 改为想去与原时段相同，交通报价能命中缓存，先于时间方向求解，保证可行时一定出现。
        if relaxable:
            relaxed=[]
            for g in req.goals:
                if g.goal_id not in ids:relaxed.append(g);continue
                categories=goal_categories(g,ctx.facts) if preferences else set()
                if preferences and (categories&set(preferences.avoid) or _spice_exceeded(g,ctx.facts,preferences.spice)):continue
                relaxed.append(g.model_copy(update={'required':False,'importance':6+int(bool(preferences and categories&set(preferences.interests)))*2}))
            result=solve(req.model_copy(update={'goals':tuple(relaxed)})) if relaxed else None
            if result and result.status=='feasible' and result.itineraries:
                options.append({'id':'relax:'+keys[0] if len(keys)==1 else 'relax:conflicts','kind':'priority',
                    **({'object_key':keys[0]} if len(keys)==1 else {'object_keys':keys}),'priority':'want',
                    'label':'把'+names+'改为想去，'+('先排'+included(result) if included(result) else '本轮不安排地点'),
                    'requires_consent':True})
        local=ZoneInfo('Asia/Shanghai');start=req.starts_at.astimezone(local);end=req.ends_at.astimezone(local)
        for field,base,direction,bound,label,identity in (
            ('ends_at',end,1,end.replace(hour=22,minute=0,second=0,microsecond=0),'结束时间延后到 ','end'),
            ('starts_at',start,-1,start.replace(hour=7,minute=0,second=0,microsecond=0),'开始时间提前到 ','start')):
            if replanning and field=='starts_at':continue
            def value_at(minutes):return base+timedelta(minutes=direction*minutes)
            def usable(minutes):
                value=value_at(minutes)
                if direction==1 and value>bound:return False
                if direction==-1 and value<bound:return False
                return not (value<ctx.assessed_at and field=='starts_at')
            choices=[m for m in (180,120,60) if usable(m)]
            if not choices:continue
            largest=choices[0];smallest=choices[-1]
            middle=120 if 120 in choices and 120 not in (largest,smallest) else None
            def try_minutes(minutes):
                candidate=Requirements.model_validate(req.model_dump()|{field:value_at(minutes)})
                result=solve(candidate)
                return result,value_at(minutes)
            result,value=try_minutes(largest)
            if not (result and result.status=='feasible' and result.itineraries):
                # 最大变动仍不可行说明该方向无法修复，未算完也转向其他方向。
                continue
            if smallest!=largest:
                small_result,small_value=try_minutes(smallest)
                if small_result and small_result.status=='feasible' and small_result.itineraries:
                    result,value=small_result,small_value
                elif middle is not None:
                    mid_result,mid_value=try_minutes(middle)
                    if mid_result and mid_result.status=='feasible' and mid_result.itineraries:
                        result,value=mid_result,mid_value
            options.append({'id':'adjust:'+identity,'kind':'constraints','patch':{field:value.isoformat()},
                'label':label+value.strftime('%H:%M')+'（已核算能排进'+included(result)+'）','requires_consent':True})
        if not replanning and any(r['reason_code'] in ('closed_in_window','no_session') for r in rows):
            for days in (1,2):
                delta=timedelta(days=days);business_date=req.business_date+delta
                candidate=Requirements.model_validate(req.model_dump()|{'business_date':business_date,
                    'starts_at':req.starts_at+delta,'ends_at':req.ends_at+delta})
                result=solve(candidate)
                if result and result.status=='feasible' and result.itineraries:
                    options.append({'id':'adjust:date','kind':'constraints','patch':{'business_date':business_date.isoformat()},
                        'label':f'改到 {business_date.month} 月 {business_date.day} 日（已核算能排进'+included(result)+'）','requires_consent':True})
                    break
        summary='排不出方案：'+'；'.join(r['name']+'（'+r['reason']+'）' for r in rows)+'。'
        if options:reply=summary+'可以这样调整：'+'；或'.join(o['label'] for o in options)+'。选一个，我按它重新排。'
        elif unfinished:
            reply=summary+'延后、提前或换一天的方案这次没来得及核算完，可以把'+names+'改为想去，或稍后再试。'
        else:
            reply=summary+'本轮核算未找到延后、提前或换一天的可行方案，可以把'+names+'改为想去，或换个地点。'
        existing={o['id'] for o in options}
        options += [{'id':'relax:'+r['key'],'kind':'priority','object_key':r['key'],'priority':'want',
            'label':'允许将'+r['name']+'改为想去','requires_consent':True} for r in rows
            if goal_meta.get(r['goal_id'],{}).get('status')=='pending' and r['goal_id'] in goal_keys and 'relax:'+r['key'] not in existing][:2]
        return ConflictAdjustments(conflicts=rows,options=options,reply=reply).model_dump(mode='json',exclude_none=True)


@router.get('/constraints')
def saved_constraints(trip_id:UUID,s:Session=Depends(current)):
    with connect() as db:
        state.owned(db,s,trip_id)
        return exploration_state.read(db,trip_id)


@router.post('/preview')
def preview(trip_id:UUID,body:PlanBody,s:Session=Depends(current)):
    return preview_plan(trip_id,body,s)


def goal_categories(goal,items):
    target=items.get(('show_session' if goal.target_kind=='session' else goal.target_kind,goal.target_id))
    place=target if isinstance(target,facts.Place) else items.get(('place',getattr(target,'stage_place_id',getattr(target,'place_id',None))))
    categories=set()
    if goal.target_kind in ('session','program') or getattr(place,'place_type',None)=='stage':categories.add('shows')
    if getattr(place,'place_type',None)=='night_visit':categories.add('night')
    if goal.kind=='dining':categories.add('food')
    parent=items.get(('place',getattr(place,'parent_place_id',None)))
    if getattr(place,'place_type',None)=='scenic' or getattr(parent,'place_type',None)=='scenic':categories.add('scenic')
    return categories


def _spice_exceeded(goal,items,spice):
    rejected={'none':{'mild','hot'},'mild':{'hot'}}.get(spice,set())
    if not rejected:return False
    if goal.target_kind=='food':
        vendors=[items.get(('vendor',x.vendor_id)) for x in items.values() if isinstance(x,facts.Supply) and x.food_id==goal.target_id]
    elif goal.target_kind=='place' and getattr(items.get(('place',goal.target_id)),'place_type',None)=='restaurant':
        vendors=[x for x in items.values() if isinstance(x,facts.Vendor) and x.place_id==goal.target_id]
    else:return False
    return bool(vendors) and all(getattr(v,'spiciness',None) in rejected for v in vendors)


def _preference_result(result,excluded,notices,pending=()):
    if excluded or pending:
        plans=(result.get('planning') or {}).get('result',result.get('result') or {}).get('itineraries') or [None]
        existing=result.get('unscheduled',{})
        # Unmapped basket entries have no solver goal, so only attach them when
        # a real plan exists. An all-unmapped response keeps the existing
        # DATA_PENDING result instead of turning it into a fake explanation row.
        usable_pending=pending if any(plan is not None for plan in plans) else ()
        if excluded or usable_pending:
            result['unscheduled']={str(i):excluded+list(usable_pending)+existing.get(str(i),[]) for i in range(len(plans))}
    if notices:result['reply']=result.get('reply','请比较整套方案并确认，正式行程尚未改变。')+' '+''.join(notices)


def _unmapped_rows(collected):
    return tuple({'goal_id':'unmapped:'+item['key'],'key':item['key'],'name':item['name'],
                  'reason_code':'data_pending','reason':'资料待添加，暂时排不进行程'}
                 for item in collected['items'] if item['status']!='standby' and not item['fact_id'])


def _basket_goals(db,s,trip_id,goals,dates=None,*,ctx=None,preferences=None):
    collected=basket.snapshot(db,s,trip_id);ctx=ctx or engine.context(db)
    goals=list(goals);unmapped=[];key_by_goal={};goal_meta={};parent_groups={};excluded=[];notices=[]
    if dates is not None:
        active={'basket:'+uuid5(UUID(str(trip_id)),item['key']).hex for item in collected['items']
                if item['status']!='standby' and item['fact_id']}
        goals=[g for g in goals if not g.goal_id.startswith('basket:') or g.goal_id in active]
    for item in collected['items']:
        if item['status']=='standby':continue
        if not item['fact_id']:unmapped.append(item['key']);continue
        fk=item['fact_kind'];identity=item['fact_id'];fact=ctx.facts.get((fk,identity))
        if fk=='vendor':identity=fact.place_id;fk='place'
        if fk=='show_session':fk='session'
        gid='basket:'+uuid5(UUID(str(trip_id)),item['key']).hex
        existing=next((g for g in goals if g.goal_id==gid),None) if dates is not None else next(
            (g for g in goals if g.target_kind==fk and g.target_id==identity),None)
        priority={'required':item['priority']=='must','importance':{'must':10,'want':6,'optional':1}[item['priority']]}
        values={'goal_id':gid,'target_kind':fk,'target_id':identity,
            'kind':'show' if fk in ('session','program') else 'dining' if fk=='food' or getattr(ctx.facts.get(('place',identity)),'place_type',None)=='restaurant' else 'visit',**priority}
        candidate=existing or Goal(**values)
        must=priority['required'] or bool(dates is None and existing and existing.required)
        categories=goal_categories(candidate,ctx.facts) if preferences else set()
        avoided=next((k for k in CATEGORY_NAMES if preferences and k in categories and k in preferences.avoid),None)
        spicy=preferences and _spice_exceeded(candidate,ctx.facts,preferences.spice)
        if avoided or spicy:
            if must:
                notices.append('「'+item['name']+'」你设为必去，已保留，虽然它属于你说过不想安排的类别。')
            else:
                reason='你说过不想安排'+CATEGORY_NAMES[avoided] if avoided else '口味偏辣，和你说的“'+('不吃辣' if preferences.spice=='none' else '微辣可以')+'”不符'
                excluded.append({'goal_id':candidate.goal_id,'key':item['key'],'name':item['name'],'reason_code':'preference_excluded','reason':reason})
                goals=[g for g in goals if g.goal_id!=candidate.goal_id]
                continue
        if preferences and not must and categories&set(preferences.interests):priority['importance']+=2
        if existing:
            changes=priority if dates is not None else {'required':True} if priority['required'] else {'importance':priority['importance']} if preferences and not must and categories&set(preferences.interests) else {}
            goal=existing.model_copy(update=changes)
            goals=[goal if g.goal_id==existing.goal_id else g for g in goals]
        else:
            values.update(priority)
            goal=Goal(**values) if dates is None else TripGoal(**values,allowed_dates=dates,fixed_date=None)
            goals.append(goal)
        key_by_goal[goal.goal_id]=item['key']
        goal_meta[goal.goal_id]={'key':item['key'],'name':item['name'],'status':item['status'],'goal':goal,'locked':bool(existing and existing.required)}
        if (dates is not None or not existing) and getattr(fact,'parent_place_id',None):parent_groups[identity]=fact.parent_place_id
    # Until internal duration/path coverage can be reconciled, do not double count a park and its POIs.
    grouped=[child for child,parent in parent_groups.items() if any(g.target_kind=='place' and g.target_id==parent for g in goals)]
    return collected,ctx,goals,unmapped,key_by_goal,goal_meta,grouped,excluded,notices


def preview_plan(trip_id,body,s,*,execution_guard=None):
    if isinstance(body,PlanV2):
        from .engine.models import PreviewRequestV2,MultiDayRequirements,DayRequirements
        from .agent.multi_day_intent import link_overnights
        limit('exploration:'+s.id,20)
        with connect() as db:
            if execution_guard:execution_guard(db)
            row=state.owned(db,s,trip_id);state.check_version(row,body.expected_version);basket.guard(db,trip_id,body.basket_version)
            workspace=exploration_state.read(db,trip_id)
            if body.planning_version is not None:exploration_state.guard(db,trip_id,body.planning_version)
            from . import hotel_binding,travel_services
            projected,hotel_issues=hotel_binding.project(db,link_overnights(body.constraints.model_dump(mode='json')),
                                                        travel_services.read(db,trip_id))
            if not body.constraints.days:raise HTTPException(422,detail='请补齐多天每日时段、端点和过夜条件。')
            collected,_,goals,unmapped,_,_,grouped,excluded,notices=_basket_goals(db,s,trip_id,body.constraints.goals,
                tuple(d.business_date for d in body.constraints.days),preferences=body.constraints.preferences)
            pending=_unmapped_rows(collected)
            projected['goals']=[g.model_dump(mode='json') for g in goals]
        hotel_pending=[i for i in hotel_issues if i not in hotel_binding.blocking(hotel_issues)]
        hotel_issues=hotel_binding.blocking(hotel_issues)
        if hotel_issues:
            return unknown(hotel_issues[0]['message'],code=hotel_issues[0]['code'])|{
                'issues':hotel_issues,'constraints':projected,'planning_version':workspace['version'],
                'basket_version':body.basket_version,'trip_version':body.expected_version}
        from .budget_models import PricingOptions
        if projected.get('pricing') is None:
            with connect() as db:selected=(travel_services.read(db,trip_id).get('selection') or {}).get('hotels') or {}
            if selected.get('price_selection') or (projected.get('per_person_budget_fen') is not None and projected.get('party_size')):
                projected['pricing']=PricingOptions(party_size=projected.get('party_size'),
                    per_person_budget_fen=projected.get('per_person_budget_fen'),
                    per_person_daily=projected.get('per_person_budget_daily',False)).model_dump(mode='json')
        try:body=body.model_copy(update={'constraints':MultiDayConstraints.model_validate(projected)})
        except ValidationError:raise HTTPException(422,detail='请检查多天目标数量、日期范围和每日条件。') from None
        req_data=body.constraints.model_dump(mode='json')
        req_data['days']=[{k:v for k,v in d.items() if k in DayRequirements.model_fields} for d in req_data['days']]
        for key in ('preferences','trip_days','trip_nights','pending_fields','clarifications','range_impacts',*AGENT_ONLY):req_data.pop(key,None)
        req_data['schema_version']='city-engine/2'
        try:requirements=MultiDayRequirements.model_validate(req_data)
        except ValidationError:raise HTTPException(422,detail='请补齐多天每日时段、端点和过夜条件。') from None
        if grouped:result=unknown('景区与内部看点的时长、路径关系待添加；暂不重复计算成独立城市停靠点。',grouped,'PARENT_CHILD_PENDING')
        elif not goals:
            result=unknown('所选内容的地点、商家或场次资料待添加；暂不能核验完整路线。',unmapped) if unmapped else unknown(
                '请先在素材篮里加入想去的地点，再生成多天方案。',code='BASKET_EMPTY')
        else:
            result=engine.preview(s,trip_id,PreviewRequestV2(request_id=body.request_id,expected_version=body.expected_version,
                requirements=requirements),planning_version=workspace['version'])
        if result['status']=='unknown':result['status']='needs_data'
        _preference_result(result,excluded,notices,pending)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if execution_guard:execution_guard(db)
            state.check_version(state.owned(db,s,trip_id),body.expected_version);basket.guard(db,trip_id,body.basket_version)
            exploration_state.guard(db,trip_id,workspace['version'])
            saved=exploration_state.save(db,trip_id,body.constraints.model_dump(mode='json'),result,workspace['rounds'])
            # Saving the workspace bumps its version; the candidate must carry the version it
            # was saved at, or confirming it reports PLANNING_CHANGED (as the single-day path does).
            candidate=result.get('candidate_id')
            if candidate:
                row=db.execute('SELECT progress_json FROM city_engine_candidates WHERE id=?',(candidate,)).fetchone()
                extra=json.loads(row[0]);extra['planning_version']=saved['version']
                db.execute('UPDATE city_engine_candidates SET progress_json=? WHERE id=?',(json.dumps(extra),candidate))
        return result|{'constraints':body.constraints.model_dump(mode='json'),'planning_version':saved['version'],'hotel_notices':list(dict.fromkeys(i['message'] for i in hotel_pending)),
                       'basket_version':body.basket_version,'trip_version':body.expected_version}
    single_day(body.constraints)
    limit('exploration:'+s.id,20)
    data=body.model_dump(mode='json');scope='exploration:'+str(trip_id)
    with connect() as db:
        if execution_guard:execution_guard(db)
        row=state.owned(db,s,trip_id);guard_single_day(db,row);state.check_version(row,body.expected_version);basket.guard(db,trip_id,body.basket_version)
        cached=state.receipt(db,s,body.request_id,scope,data)
        if cached is not None:return cached
        workspace=exploration_state.read(db,trip_id)
        planning_version=workspace['version']
        if body.planning_version is not None:exploration_state.guard(db,trip_id,body.planning_version)
        draft=guard_single_day(db,row)
        ctx=engine.context(db)
        values=body.constraints.model_dump(exclude={'preferences','origin_text','destination_text',*AGENT_ONLY})
        endpoint_replaced=False
        for k in ('origin_place_id','destination_place_id'):
            if values[k]:
                endpoint=planning_endpoint(ctx.facts,values[k])
                endpoint_replaced|=endpoint!=values[k]
                values[k]=facts.trip_endpoint(ctx.facts,endpoint)
        missing=[k for k in ('business_date','starts_at','ends_at') if values[k] is None]
        missing += [k+'_place_id' for k in ('origin','destination') if not values[k+'_place_id'] and not getattr(body.constraints,k+'_text')]
        unmatched=[k for k in ('origin','destination') if not values[k+'_place_id'] and getattr(body.constraints,k+'_text')]
        # Retain all formal remaining goals, including locks. Saving intentions never edits these.
        old=engine.route_row(db,trip_id)
        original=read_requirements(old['requirements_json']) if old else None
        from .assistant.tools import requirements as draft_requirements
        if original:
            from types import SimpleNamespace
            goals=list(draft_requirements(SimpleNamespace(draft=draft,requirements=original,route={'itinerary':json.loads(old['itinerary_json'])})).goals)
        else:
            goals=[Goal(goal_id=x.stop_id,target_kind='session' if x.session_id else 'place',target_id=x.session_id or x.place_id,
                        kind=x.kind,required=x.locked,duration_seconds=x.duration_seconds,window_start=x.window_start,window_end=x.window_end)
                   for x in draft.stops if x.progress=='pending' and x.kind!='return']
        collected,ctx,goals,unmapped,key_by_goal,goal_meta,grouped,excluded,notices=_basket_goals(db,s,trip_id,goals,ctx=ctx,preferences=body.constraints.preferences)
        pending=_unmapped_rows(collected)
    if missing:
        result=unknown('请补充'+ '、'.join({'business_date':'日期','starts_at':'开始时间','ends_at':'结束时间','origin_place_id':'出发地点','destination_place_id':'返程地点'}[k] for k in missing),'','PERSONAL_CHOICE_MISSING')
        result['questions']=[{'field':k} for k in missing]
    elif unmatched:result=unknown('已记下出发和返回地点，位置资料待核验，暂不能生成路线。',code='ENDPOINT_MATCH_PENDING')
    elif grouped:result=unknown('景区与内部看点的时长、路径关系待添加；暂不重复计算成独立城市停靠点。',grouped,'PARENT_CHILD_PENDING')
    elif not goals:
        result=unknown('所选内容的地点、商家或场次资料待添加；暂不能核验完整路线。',unmapped) if unmapped else unknown(
            '先从详情页加入想去的地方或美食。',code='BASKET_EMPTY')
    else:
        try:req=Requirements.model_validate(values|{'goals':goals,'alternatives':3})
        except ValidationError:raise HTTPException(422,detail='请检查同日开始、结束时间、目标数量与交通方式。') from None
        diagnosis={}
        def explain(requirements,context,solved):
            adjustments=_conflict_adjustments(solved,requirements,context,key_by_goal,goal_meta,
                replanning=row['status'] in ('ACTIVE','PAUSED'),preferences=body.constraints.preferences)
            if adjustments:diagnosis.update(adjustments)
            return _unscheduled(solved.model_dump(mode='json'),requirements,context,key_by_goal,goal_meta)
        if row['status'] in ('ACTIVE','PAUSED'):
            plan=engine.preview(s,trip_id,ReplanRequest(request_id=uuid5(body.request_id,'engine'),expected_version=body.expected_version,requirements=req),replanning=True,planning_version=planning_version,explain=explain)
        else:plan=engine.preview(s,trip_id,PreviewRequest(request_id=uuid5(body.request_id,'engine'),expected_version=body.expected_version,requirements=req),planning_version=planning_version,explain=explain)
        if endpoint_replaced:plan['result']['warnings'].append(ENDPOINT_REPLACEMENT_WARNING)
        status=plan['result']['status'];options=[]
        if status=='infeasible':
            # These are requests for authorization, not feasible routes. Never auto-demote a must.
            options=[{'id':'relax:'+i['key'],'kind':'priority','object_key':i['key'],'priority':'want',
                      'label':'允许将'+i['name']+'改为想去','requires_consent':True} for i in collected['items'] if i['priority']=='must' and i['status']!='arranged'][:2]
            options.append({'id':'edit_time','kind':'constraints','label':'调整可用时段后重新计算','requires_consent':True})
        result={'status':'feasible' if status=='feasible' else 'infeasible' if status=='infeasible' else 'unknown',
                'reply':'请比较方案并确认，正式行程尚未改变。' if status=='feasible' else '已知硬约束存在冲突，请决定如何取舍。' if status=='infeasible' else '资料或计算条件不足，暂不能可靠判定。',
                'issues':plan['result']['issues'],'options':options,'planning':plan,'formal_state_changed':False,
                'goal_keys':key_by_goal,'activity_names':{a['goal_id']:getattr(ctx.facts.get(('place',a['place_id'])),'name','资料待添加') for i in plan['result']['itineraries'] for a in i['activities']}}
        result['unscheduled']=plan.get('unscheduled',{})
        if diagnosis:
            result.update(diagnosis,status='infeasible')
            reasons={r['goal_id']:r['name']+'：'+r['reason'] for r in diagnosis['conflicts']}
            result['issues']=[i|{'message':'；'.join(reasons[gid] for gid in i['affected_ids'] if gid in reasons)}
                if i['code']=='REQUIRED_GOAL_UNAVAILABLE' and any(gid in reasons for gid in i['affected_ids']) else i for i in result['issues']]
    _preference_result(result,excluded,notices,pending)
    if old and result.get('planning'):
        for a in json.loads(old['itinerary_json'])['activities']:
            result['activity_names'].setdefault(a['goal_id'],getattr(ctx.facts.get(('place',a['place_id'])),'name','资料待添加'))
    result.update(constraints=body.constraints.model_dump(mode='json'),basket_version=body.basket_version,trip_version=body.expected_version)
    try:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if execution_guard:execution_guard(db)
            state.check_version(state.owned(db,s,trip_id),body.expected_version);basket.guard(db,trip_id,body.basket_version)
            cached=state.receipt(db,s,body.request_id,scope,data)
            if cached is not None:return cached
            exploration_state.guard(db,trip_id,planning_version)
            saved=exploration_state.save(db,trip_id,result['constraints'],result,workspace['rounds'])
            candidate=(result.get('planning') or {}).get('candidate_id')
            if candidate:
                row=db.execute('SELECT progress_json FROM city_engine_candidates WHERE id=?',(candidate,)).fetchone()
                extra=json.loads(row[0]);extra['planning_version']=saved['version']
                db.execute('UPDATE city_engine_candidates SET progress_json=? WHERE id=?',(json.dumps(extra),candidate))
            state.remember(db,s,body.request_id,trip_id,scope,data,result)
    except HTTPException:
        candidate=(result.get('planning') or {}).get('candidate_id')
        if candidate:
            with connect() as db:
                db.execute('UPDATE city_engine_candidates SET consumed=1 WHERE id=? AND trip_id=?',(candidate,str(trip_id)))
        raise
    return result


class Answer(Strict):
    request_id:UUID
    expected_version:int=Field(ge=1)
    basket_version:int=Field(ge=0)
    planning_version:int=Field(ge=0)
    option_id:str | None=Field(default=None,max_length=200)
    text:str | None=Field(default=None,min_length=1,max_length=2000)


def reply_patch(text, items, constraints):
    """Conservative structured replies. Reuse V3 clock parsing; unclear clauses write nothing."""
    import re
    from .agent.understand import clock
    updates={};priorities={}
    for clause in filter(None,(c.strip() for c in re.split(r'[，,。；;！!\n]',text))):
        matches=[]
        for item in items:
            # Exact full names only. Ambiguous names and unsupported sentences require clarification.
            if clause in (item['name']+'必去',item['name']+'一定得去',item['name']+'一定要去'):
                matches.append((item,'must','pending'))
            elif clause in (item['name']+'可以不去',item['name']+'改为想去',item['name']+'想去'):
                matches.append((item,'want','pending'))
            elif clause in (item['name']+'顺路看看',item['name']+'改为顺路看看'):
                matches.append((item,'optional','pending'))
            elif clause in (item['name']+'先放候补',item['name']+'放候补'):
                matches.append((item,item['priority'],'standby'))
        if len(matches)==1:
            item,priority,status=matches[0]
            if item['status']=='arranged':raise HTTPException(422,detail='已安排活动请先在正式行程中确认调整，不能通过素材篮解除锁定。')
            if item['key'] in priorities:raise HTTPException(422,detail='同一项目出现多次取舍，请保留一个明确决定。')
            priorities[item['key']]={'priority':priority,'status':status}
        elif clause in ('轻松一点','轻松','别太累','适中','紧凑一点'):
            updates['pace']={'适中':'standard','紧凑一点':'compact'}.get(clause,'relaxed')
        elif clause in ('少走路','尽量少走路','控制费用','综合平衡'):
            updates['objective']={'控制费用':'lower_cost','综合平衡':'balanced'}.get(clause,'less_walking')
        elif re.fullmatch(r'(?:最晚)?(?:下午|晚上)?[\d一二两三四五六七八九十:：点半分]+(?:前)?(?:回酒店|返程|结束)',clause):
            value=clock(clause)
            if not value or not constraints['business_date']:raise HTTPException(422,detail='请先选择日期，并提供明确的返程时刻。')
            updates['ends_at']=str(constraints['business_date'])+'T'+value+':00+08:00'
        else:raise HTTPException(422,detail='这句取舍尚不能明确识别。请使用完整项目名，例如“清明上河园必去”，或直接调整条件；其他问题可继续问小汴梁。')
    if not updates and not priorities:raise HTTPException(422,detail='请填写具体取舍。')
    return Constraints.model_validate(constraints|updates),priorities


@router.post('/answer')
def answer(trip_id:UUID,body:Answer,s:Session=Depends(current)):
    return answer_choice(trip_id,body,s)


def answer_choice(trip_id,body,s,*,execution_guard=None):
    if bool(body.option_id)==bool(body.text):raise HTTPException(422,detail='请选择一个选项或提交一句具体取舍。')
    data=body.model_dump(mode='json');scope='exploration_answer:'+str(trip_id)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if execution_guard:execution_guard(db)
        row=state.owned(db,s,trip_id);state.check_version(row,body.expected_version)
        cached=state.receipt(db,s,body.request_id,scope,data)
        if cached is not None:return cached
        basket.guard(db,trip_id,body.basket_version);exploration_state.guard(db,trip_id,body.planning_version)
        workspace=exploration_state.read(db,trip_id);collected=basket.snapshot(db,s,trip_id)
        if not workspace['constraints']:raise HTTPException(422,detail='请先核验规划条件，再提交取舍。')
        multi=workspace['constraints'] and workspace['constraints'].get('schema_version')=='city-constraints/2'
        if multi:
            saved=exploration_state.save(db,trip_id,workspace['constraints'],workspace.get('result'),workspace['rounds']+1)
            response=saved|{'formal_state_changed':False}
            state.remember(db,s,body.request_id,trip_id,scope,data,response)
            return response
        constraints=Constraints.model_validate(workspace['constraints']);priorities={}
        if body.option_id:
            result=workspace['result'] or {}
            option=next((o for o in result.get('options',[]) if o['id']==body.option_id),None)
            if not option or result.get('basket_version')!=body.basket_version or result.get('trip_version')!=body.expected_version:
                raise HTTPException(409,detail='此协商选项已失效，请重新核验。')
            if workspace['rounds']>=2:raise HTTPException(422,detail='请直接调整时间或素材篮，不再重复追问。')
            if option['kind']!='priority':raise HTTPException(422,detail='请在规划条件中调整可用时段。')
            for key in option.get('object_keys') or [option['object_key']]:
                priorities[key]={'priority':option['priority'],'status':'pending'}
        else:
            try:constraints,priorities=reply_patch(body.text,collected['items'],constraints.model_dump(mode='json'))
            except ValidationError:raise HTTPException(422,detail='规划条件格式不正确，请检查时间。') from None
        # Reject invalid horizons before changing any priority.
        if constraints.starts_at and constraints.ends_at and constraints.ends_at<=constraints.starts_at:
            raise HTTPException(422,detail='结束时间须晚于开始时间；未保存本次取舍。')
        for key,patch in priorities.items():
            row=db.execute('SELECT data_json FROM city_basket_items WHERE trip_id=? AND object_key=?',(str(trip_id),key)).fetchone()
            item=json.loads(row[0]);item.update(patch,source='assistant')
            db.execute('UPDATE city_basket_items SET data_json=?,updated_at=? WHERE trip_id=? AND object_key=?',
                       (json.dumps(item,ensure_ascii=False),stamp(),str(trip_id),key))
        if priorities:
            db.execute('UPDATE city_baskets SET version=version+1,updated_at=? WHERE trip_id=?',(stamp(),str(trip_id)))
        saved=exploration_state.save(db,trip_id,constraints.model_dump(mode='json'),None,workspace['rounds']+1)
        response=saved|{'basket':basket.snapshot(db,s,trip_id),'formal_state_changed':False}
        state.remember(db,s,body.request_id,trip_id,scope,data,response)
        return response


@router.post('/cancel')
def cancel(trip_id:UUID,body:Answer,s:Session=Depends(current)):
    data=body.model_dump(mode='json');scope='exploration_cancel:'+str(trip_id)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');state.owned(db,s,trip_id)
        cached=state.receipt(db,s,body.request_id,scope,data)
        if cached is not None:return cached
        exploration_state.guard(db,trip_id,body.planning_version)
        saved=exploration_state.read(db,trip_id)
        response=exploration_state.save(db,trip_id,saved['constraints'],None,0)
        state.remember(db,s,body.request_id,trip_id,scope,data,response)
        return response


def answer_focus(text,snapshot):
    """Conservative detail-page lookup; missing content is not supplied by a model."""
    focus=snapshot.envelope.page_focus
    if not focus or not any(w in text for w in ('这里','这家','这道','这个','有哪些演出','值得看','游玩多久')):return None
    name=focus['name']
    if not focus['fact_id']:
        return {'status':'needs_data','reply':name+'：详细介绍、实景素材及可核验游览/营业资料待添加。已识别当前页面，不会根据主题插画推断时间或路线。','references':[],'page_focus':focus}
    item=snapshot.planning.facts.get((focus['fact_kind'],focus['fact_id']))
    if focus['fact_kind']=='vendor':item=snapshot.planning.facts.get(('place',item.place_id))
    if any(w in text for w in ('多久','时长')):
        return {'status':'needs_data','reply':name+'：建议游览或用餐时长待添加。规划器的默认停留参数不能作为景区实测游玩时长。','page_focus':focus}
    if any(w in text for w in ('演出','节目')) and isinstance(item,facts.Place):
        def belongs(pid):
            seen=set()
            while pid and pid not in seen:
                if pid==item.fact_id:return True
                seen.add(pid);pid=getattr(snapshot.planning.facts.get(('place',pid)),'parent_place_id',None)
            return False
        shows=[x for x in snapshot.planning.facts.values() if isinstance(x,facts.ShowSession) and belongs(x.stage_place_id)
               and x.business_date==snapshot.draft.business_date and facts.status(x,snapshot.planning.facts,facts.now())['ready']]
        return {'status':'answered' if shows else 'needs_data','reply':name+'：'+('找到'+str(len(shows))+'场适用的已核验演出。' if shows else '适用日期的演出场次待添加。'),
                'references':[{'kind':'show_session','id':x.fact_id} for x in shows[:6]],'page_focus':focus}
    return {'status':'needs_data','reply':name+'：这项详细介绍或营业、供应、交通资料待添加，请先保留为旅行意向。','page_focus':focus}
