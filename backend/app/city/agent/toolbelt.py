"""Evidence-bound read tools and one preview adapter; no commit in this registry."""
import re
from datetime import datetime, timedelta
from uuid import UUID, uuid5
from fastapi import HTTPException
from .. import facts
from ..assistant import context, interpret, tools as v2tools
from ..engine import service as engine
from ..engine.models import Goal, PreviewRequest, ReplanRequest, Requirements
from ..engine.travel import TravelTimeService, travel_seconds, entry_deadline
from ..schema import TZ
from .models import BasketProposal, BasketProposalItem


def values(intent,field):return [c for c in intent.constraints if c.field==field]


def protected_shows(snapshot,intent):
    shows=[s for s in snapshot.draft.stops if s.kind=='show' and s.progress=='pending']
    if any(c.value=='referenced' for c in values(intent,'preserve_shows')):
        ids={r.id for r in interpret.resolve_reference('那场演出',snapshot,'show_session')}
        if len(ids)!=1:raise HTTPException(422,detail='请明确需要保留的场次。')
        selected=[s for s in shows if s.session_id in ids]
        shows=[s for s in shows if s.locked or s in selected]
    return shows


def query_shows(snapshot,intent):
    output=[];ctx=snapshot.planning
    for stop in protected_shows(snapshot,intent):
        s=ctx.facts.get(('show_session',stop.session_id))
        if not s or not s.ends_at or not ctx.ready(s,s.starts_at,s.ends_at):
            return {'status':'needs_data','code':'SHOW_NOT_VERIFIED','items':[]}
        output.append(context.fact_view(s,ctx.facts,ctx.assessed_at))
    return {'status':'ok' if output else 'needs_data','code':None if output else 'SHOW_REFERENCE_MISSING','items':output}


def requested_foods(snapshot,intent):
    """Dishes the visitor named in a dining search or food wish, resolved to food facts."""
    words={str(c.value) for c in values(intent,'food') if c.value}
    texts=[t for a in intent.actions if a.kind=='search_dining' for t in (a.quote,a.target_name) if t]
    return [f for f in snapshot.planning.facts.values() if isinstance(f,facts.Food) and facts.planning_visible(f)
            and (f.name in words or any(n in t for t in texts for n in (f.name,*f.aliases) if n and len(n)>1))]


def general_dining(snapshot,action):
    if action.near:return False
    if action.kind!='search_dining' and not (action.kind=='query' and action.purpose=='dining'):return False
    if not action.target_name:return action.purpose=='dining'
    target=action.target_name
    # The model decides whether a phrase is a generic request.  A non-near
    # target is specific only when facts can resolve it to one concrete dish or
    # restaurant; no second keyword parser guesses at its meaning.
    matches=[r for r in snapshot.planning.facts.values()
             if facts.planning_visible(r) and (isinstance(r,facts.Food) or isinstance(r,facts.Place) and r.place_type=='restaurant')
             and (getattr(r,'name',None)==target or target in getattr(r,'aliases',()))]
    return not matches


def _supply_readable(ctx,record,seen=None):
    # A dish-to-shop lookup needs supply evidence, but no navigable coordinates.
    if record is None or not facts.planning_visible(record):return False
    if any(g!='LOCATION_UNKNOWN' for g in facts.gaps(record,ctx.facts,ctx.assessed_at)):return False
    seen=set() if seen is None else seen
    for ref in facts.references(record):
        if ref in seen:continue
        seen.add(ref)
        if not _supply_readable(ctx,ctx.facts.get(ref),seen):return False
    return True


def find_food(snapshot,intent,*,general=False):
    """Read-only dish-to-shop references, without requiring a position or route."""
    ctx=snapshot.planning;now=ctx.assessed_at;today=now.astimezone(TZ).date()
    basket={(m['fact_kind'],m['fact_id']) for m in snapshot.materials if m['status']!='standby'}
    items=[];lines=[];reference=False
    unknown_targets=[]
    if general:
        for action in intent.actions:
            if not action.target_name:continue
            if not any(facts.planning_visible(r) and (isinstance(r,facts.Food) or isinstance(r,facts.Place) and r.place_type=='restaurant')
                       and (getattr(r,'name',None)==action.target_name or action.target_name in getattr(r,'aliases',()))
                       for r in ctx.facts.values()):unknown_targets.append(action.target_name)
    foods=sorted((r for r in ctx.facts.values() if isinstance(r,facts.Food) and facts.planning_visible(r)),key=lambda r:r.fact_id) if general else requested_foods(snapshot,intent)
    for food in foods:
        shops=[];seen=set()
        for supply in sorted((r for r in ctx.facts.values() if isinstance(r,facts.Supply) and r.food_id==food.fact_id),key=lambda r:r.fact_id):
            vendor=ctx.facts.get(('vendor',supply.vendor_id))
            if not vendor or vendor.place_id in seen or not _supply_readable(ctx,supply):continue
            seen.add(vendor.place_id)
            windows=[f"{r.opens_at.astimezone(TZ):%H:%M}–{r.closes_at.astimezone(TZ):%H:%M}" for r in sorted(ctx.rules(vendor.place_id,today),key=lambda r:r.opens_at or now)
                     if r.opens_at and r.closes_at and facts.planning_visible(r)]
            name=context.label(ctx.facts,vendor.place_id)
            in_basket=('vendor',vendor.fact_id) in basket or ('place',vendor.place_id) in basket
            items.append({'food_id':food.fact_id,'vendor_id':vendor.fact_id,'place_id':vendor.place_id,'name':name,
                          'today_hours':windows,'in_basket':in_basket,'supply_id':supply.fact_id,'source_ids':list(supply.source_ids),
                          'validation_status':supply.validation_status,'planning_status':supply.planning_status})
            reference=reference or supply.planning_status=='reference'
            shops.append(name+(f"（今天 {'、'.join(windows)}）" if windows else '')+('，已在素材篮' if in_basket else ''))
            if general and len(shops)==2:break
        if general and not shops:continue
        lines.append(f"{food.name}可以去：{'；'.join(shops[:5])}。" if shops else f"暂时没有{food.name}的店铺资料。")
        if general and len(lines)==5:break
    missing=''.join(f'资料里暂时没有{target}。' for target in dict.fromkeys(unknown_targets))
    if not lines:
        if general:return {'status':'needs_data','code':'NO_SUPPLY_REFERENCE','items':[],'reply':missing+'暂时没有带供应记录的特色菜与店铺资料。','formal_state_changed':False}
        return {'status':'needs_clarification','code':'FOOD_NOT_NAMED','items':[],'reply':'想吃哪道菜？告诉我菜名就能帮你找店。'}
    tail='想去哪家可以加入素材篮。' if general else '想排进行程，在店铺详情页加入素材篮就行。' if items and not all(i['in_basket'] for i in items) else ''
    result={'status':'answered' if items else 'needs_data','code':None if items else 'NO_SUPPLY_REFERENCE','items':items,
            'reply':missing+('以下是资料中的供应参考，尚未现场核实，当前供应请向店家确认。' if reference else '')+''.join(lines)+tail,'formal_state_changed':False}
    # A named dish with one unambiguous Food record can itself become a basket
    # candidate. Vendor/place results remain read-only until the visitor confirms.
    if not general and len(foods)==1 and items:
        food=foods[0];all_basket={(m['fact_kind'],m['fact_id']) for m in snapshot.materials}
        covered=('food',food.fact_id) in all_basket or any(
            ('vendor',i['vendor_id']) in all_basket or ('place',i['place_id']) in all_basket for i in items)
        if not covered:
            quote=next((a.quote for a in intent.actions if a.kind=='search_dining' and a.quote),food.name)
            result['basket_proposal']=BasketProposal(
                items=(BasketProposalItem(operation='put',kind='food',object_id=food.fact_id,name=food.name,
                                          priority='want',quote=quote),),
                basket_version=snapshot.envelope.basket['version']).model_dump(mode='json')
            result['reply']+='确认后可以把这道菜加入素材篮，再继续排。'
    return result


def search_dining(snapshot,intent,relax=False):
    ctx=snapshot.planning;req=v2tools.requirements(snapshot)
    requested=values(intent,'food')
    if relax and any(c.strength not in ('optional','soft') for c in requested):raise HTTPException(422,detail='未授权放宽菜品要求。')
    names={str(c.value) for c in requested if c.value} if not relax else set()
    target_names={a.target_name for a in intent.actions if a.kind=='search_dining' and a.target_name}
    target_places={r.fact_id for r in ctx.facts.values() if isinstance(r,facts.Place) and r.place_type=='restaurant'
                   and facts.planning_visible(r) and (r.name in target_names or any(alias in target_names for alias in r.aliases))}
    foods=[r for r in ctx.facts.values() if isinstance(r,facts.Food) and r.name in names and ctx.ready(r,req.starts_at,req.ends_at)]
    if names and len({r.name for r in foods})!=len(names):return {'status':'no_candidates','code':'FOOD_NOT_VERIFIED','items':[]}
    output=[]
    for p in sorted(ctx.facts.values(),key=lambda r:r.fact_id):
        if not isinstance(p,facts.Place) or p.place_type!='restaurant' or not ctx.ready(p,req.starts_at,req.ends_at):continue
        if target_places and p.fact_id not in target_places:continue
        vendors=[v for v in ctx.facts.values() if isinstance(v,facts.Vendor) and v.place_id==p.fact_id and ctx.ready(v,req.starts_at,req.ends_at)]
        supplies=[s for s in ctx.facts.values() if isinstance(s,facts.Supply) and s.vendor_id in {v.fact_id for v in vendors}
                  and ctx.ready(s,req.starts_at,req.ends_at)]
        if foods and any(f.fact_id not in {s.food_id for s in supplies} for f in foods):continue
        rules=[r for r in ctx.rules(p.fact_id,req.business_date) if ctx.ready(r,req.starts_at,req.ends_at)]
        if not rules:continue
        evidence=[p,*vendors,*supplies,*rules,*foods]
        output.append({'place_id':p.fact_id,'name':context.label(ctx.facts,p.fact_id),'food_ids':[f.fact_id for f in foods],
                       'relaxed_optional_food':relax,'facts':[context.fact_view(x,ctx.facts,ctx.assessed_at) for x in evidence[:12]]})
        if len(output)==6:break
    return {'status':'ok' if output else 'no_candidates','code':None if output else 'NO_VERIFIED_SUPPLY_OR_OPENING','items':output}


def apply_constraints(snapshot,intent,restaurant=None):
    req=v2tools.requirements(snapshot);goals=list(req.goals);updates={};preserved=[]
    for c in intent.constraints:
        if c.field in ('pace','objective') and c.value:updates[c.field]=c.value
        if c.field=='walking_seconds' and c.value:
            if c.strength=='hard':updates['max_walking_seconds']=min(updates.get('max_walking_seconds',req.max_walking_seconds or 43200),int(c.value))
            else:updates['objective']='less_walking'
        if c.field=='return_by' and c.value:
            updates['ends_at']=datetime.combine(req.business_date,datetime.min.time(),TZ).replace(hour=int(c.value[:2]),minute=int(c.value[3:]))
    if values(intent,'preserve_shows'):
        preserved=[s.stop_id for s in protected_shows(snapshot,intent)]
        if not preserved:raise HTTPException(422,detail='没有可以保留的正式演出，请明确场次。')
    if restaurant:
        meals=[g for g in goals if g.kind=='dining']
        if len(meals)>1:raise HTTPException(422,detail='存在多次用餐，请先明确调整哪一次。')
        if meals:
            meal=meals[0]
            current=next((s for s in snapshot.draft.stops if s.stop_id==meal.goal_id),None)
            if current and current.locked and current.place_id!=restaurant:raise HTTPException(409,detail='不能替换已锁定餐饮地点，请明确取舍。')
            # Preserve original goal identity and food semantics when pinning a realization.
            if meal.target_kind=='food':meal=meal.model_copy(update={'fixed_place_id':restaurant,'required':True})
            else:meal=meal.model_copy(update={'target_kind':'place','target_id':restaurant,'fixed_place_id':restaurant,
                'fixed_session_id':None,'required':True,'duration_seconds':meal.duration_seconds or 2700})
            goals=[meal if g.goal_id==meal.goal_id else g for g in goals]
        else:goals.append(Goal(goal_id='v3:meal',target_kind='place',target_id=restaurant,kind='dining',required=True,duration_seconds=2700))
    for c in values(intent,'meal_at'):
        meals=[g for g in goals if g.kind=='dining']
        if len(meals)!=1 or not c.value:raise HTTPException(422,detail='请明确需要调整的用餐活动和时间。')
        t=datetime.combine(req.business_date,datetime.min.time(),TZ).replace(hour=int(c.value[:2]),minute=int(c.value[3:]))
        meal=meals[0].model_copy(update={'required':True,'window_start':t,'window_end':t+timedelta(seconds=meals[0].duration_seconds or 2700)})
        goals=[meal if g.goal_id==meal.goal_id else g for g in goals]
    goals=[g.model_copy(update={'required':True}) if g.goal_id in preserved else g for g in goals]
    return Requirements.model_validate(req.model_dump()|updates|{'goals':goals}),preserved


def verify_travel(snapshot,intent,restaurant,shows):
    if snapshot.envelope.position['status']!='fresh':return {'status':'needs_data','code':'FRESH_POSITION_REQUIRED'}
    req,_=apply_constraints(snapshot,intent,restaurant);moment=snapshot.planning.assessed_at
    req=Requirements.model_validate(req.model_dump()|{'starts_at':moment+timedelta(seconds=120),'origin_place_id':snapshot.envelope.position['place_id']})
    service=TravelTimeService(snapshot.planning)
    def arc(a,b):
        choices=[j for mode in req.modes for j in service.index.get((a,b,mode),()) if service.usable(j,req)]
        return min(choices,key=travel_seconds) if choices else None
    first=arc(req.origin_place_id,restaurant)
    if first is None:return {'status':'needs_data','code':'TRAVEL_ORIGIN_TO_MEAL_UNKNOWN'}
    arrival=req.starts_at+timedelta(seconds=travel_seconds(first));duration=next(g.duration_seconds or 2700 for g in req.goals if g.kind=='dining')
    openings=snapshot.planning.rules(restaurant,req.business_date)
    starts=[max(arrival,r.opens_at) for r in openings if r.opens_at and snapshot.planning.ready(r,req.starts_at,req.ends_at)
            and max(arrival,r.opens_at)+timedelta(seconds=duration)<=r.closes_at]
    if not starts:return {'status':'conflict','code':'MEAL_OPENING_CONFLICT'}
    start=min(starts)
    meal_goal=next(g for g in req.goals if g.kind=='dining')
    if meal_goal.window_start:start=max(start,meal_goal.window_start)
    meal=[c for c in values(intent,'meal_at') if c.value]
    if meal:
        t=datetime.combine(req.business_date,datetime.min.time(),TZ).replace(hour=int(meal[-1].value[:2]),minute=int(meal[-1].value[3:]))
        if t<start:return {'status':'conflict','code':'MEAL_TIME_CONFLICT'}
        start=t
    end=start+timedelta(seconds=duration)
    if meal_goal.window_end and end>meal_goal.window_end:return {'status':'conflict','code':'MEAL_WINDOW_CONFLICT'}
    if not any(r.opens_at and r.opens_at<=start and end<=r.closes_at for r in openings):
        return {'status':'conflict','code':'MEAL_OPENING_CONFLICT'}
    # Dynamic exploration checks the next protected show; the solver checks every later goal and return.
    future=sorted([s for s in shows if datetime.fromisoformat(s['starts_at'])>moment],key=lambda s:s['starts_at'])
    if not future:return {'status':'needs_data','code':'FUTURE_SHOW_REQUIRED'}
    show=future[0];second=arc(restaurant,show['stage_place_id'])
    if second is None:return {'status':'needs_data','code':'TRAVEL_MEAL_TO_SHOW_UNKNOWN'}
    deadline=datetime.fromisoformat(show['starts_at'])-timedelta(seconds=req.show_buffer_seconds)
    if show.get('admission_deadline'):deadline=min(deadline,datetime.fromisoformat(show['admission_deadline']))
    for j in (first,second):
        cutoff=entry_deadline(j,req,snapshot.planning)
        reaches=arrival if j is first else end+timedelta(seconds=travel_seconds(j))
        if cutoff and reaches>cutoff:return {'status':'conflict','code':'ADMISSION_CONFLICT'}
    walking=sum(travel_seconds(j) for j in (first,second) if j.travel.mode=='walking')
    limits=[c for c in values(intent,'walking_seconds') if c.value]
    if any(c.strength=='hard' and walking>int(c.value) for c in limits):return {'status':'conflict','code':'WALKING_LIMIT'}
    if end+timedelta(seconds=travel_seconds(second))>deadline:return {'status':'conflict','code':'SHOW_DEADLINE_CONFLICT'}
    return {'status':'ok','place_id':restaurant,'meal_start':start.isoformat(),'meal_end':end.isoformat(),
            'walking_seconds':walking,'show_id':show['id'],'show_deadline':deadline.isoformat(),
            'soft_walking_preference_met':all(walking<=int(c.value) for c in limits),
            'journeys':[{'id':j.journey_id,'upper_seconds':travel_seconds(j),'source':j.travel.evidence.source,
                         'checked_at':j.travel.evidence.checked_at.isoformat(),'revision':j.revision} for j in (first,second)],
            'scope':'meal_to_next_show_only; full itinerary needs independent planning validation'}


def preview(session,trip_id,snapshot,intent,request_id,restaurant=None):
    req,preserved=apply_constraints(snapshot,intent,restaurant)
    if restaurant:
        # Keep the meal before the protected show, not just somewhere in the same day.
        shows=protected_shows(snapshot,intent)
        sessions=[snapshot.planning.facts.get(('show_session',s.session_id)) for s in shows]
        if sessions:
            end=min(s.starts_at for s in sessions if s)
            req=req.model_copy(update={'goals':tuple(g.model_copy(update={'window_end':min(g.window_end,end) if g.window_end else end}) if g.kind=='dining' else g for g in req.goals)})
    replanning=snapshot.row['status'] in ('ACTIVE','PAUSED')
    cls=ReplanRequest if replanning else PreviewRequest
    extra={'fixed_goal_ids':tuple(preserved)} if replanning else {}
    body=cls(request_id=uuid5(UUID(str(request_id)),'v3:preview'),expected_version=snapshot.row['version'],requirements=req,**extra)
    result=engine.preview(session,trip_id,body,replanning=replanning)
    if result.get('changes'):result['changes']=v2tools.changes_for_remaining(result['changes'],snapshot.draft)
    return result
