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
from . import dining


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
    if dining.restaurant_query(snapshot,intent):return dining.recommend(snapshot,intent)
    result=_find_food(snapshot,intent,general=general)
    missing=dining.usable(snapshot.planning,dining.conditions(intent))[1]
    if missing:result['reply']=dining.unapplied(missing)+result['reply']
    return result


def _find_food(snapshot,intent,*,general=False):
    ctx=snapshot.planning;now=ctx.assessed_at;today=now.astimezone(TZ).date()
    basket={(m['fact_kind'],m['fact_id']) for m in snapshot.materials if m['status']!='standby'}
    items=[];lines=[];reference=False
    # A target with no record may be a dish missing from the data (螺蛳粉) or a generic
    # phrase the model kept as a name (开封特色美食). The list therefore ends with a note
    # that is true in both cases instead of claiming the phrase is absent.
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
    missing_note='没有列出的菜暂时没有资料。' if unknown_targets else ''
    if not lines:
        if general:return {'status':'needs_data','code':'NO_SUPPLY_REFERENCE','items':[],'reply':'暂时没有带供应记录的特色菜与店铺资料。','formal_state_changed':False}
        return {'status':'needs_clarification','code':'FOOD_NOT_NAMED','items':[],'reply':'想吃哪道菜？告诉我菜名就能帮你找店。'}
    tail='想去哪家可以加入素材篮。' if general else '想排进行程，在店铺详情页加入素材篮就行。' if items and not all(i['in_basket'] for i in items) else ''
    result={'status':'answered' if items else 'needs_data','code':None if items else 'NO_SUPPLY_REFERENCE','items':items,
            'reply':('以下是资料中的供应参考，尚未现场核实，当前供应请向店家确认。' if reference else '')+''.join(lines)+missing_note+tail,'formal_state_changed':False}
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
    if not relax:
        for food in requested_foods(snapshot,intent):
            if not ctx.ready(food,req.starts_at,req.ends_at):return {'status':'no_candidates','code':'FOOD_NOT_VERIFIED','items':[]}
            if food not in foods:foods.append(food)
    output=[];(filters,missing),rejected=dining.usable(ctx,dining.conditions(intent)),{}
    for p in sorted(ctx.facts.values(),key=lambda r:r.fact_id):
        if not isinstance(p,facts.Place) or p.place_type!='restaurant' or not ctx.ready(p,req.starts_at,req.ends_at):continue
        if target_places and p.fact_id not in target_places:continue
        vendors=[v for v in ctx.facts.values() if isinstance(v,facts.Vendor) and v.place_id==p.fact_id and ctx.ready(v,req.starts_at,req.ends_at)]
        if filters:
            for v in vendors:
                for problem in dining.failures(v,filters):rejected[problem]=rejected.get(problem,0)+1
            vendors=[v for v in vendors if not dining.failures(v,filters)]
            if not vendors:continue
        supplies=[s for s in ctx.facts.values() if isinstance(s,facts.Supply) and s.vendor_id in {v.fact_id for v in vendors}
                  and ctx.ready(s,req.starts_at,req.ends_at)]
        if foods and any(f.fact_id not in {s.food_id for s in supplies} for f in foods):continue
        rules=[r for r in ctx.rules(p.fact_id,req.business_date) if ctx.ready(r,req.starts_at,req.ends_at)]
        if not rules:continue
        evidence=[p,*vendors,*supplies,*rules,*foods]
        output.append({'place_id':p.fact_id,'name':context.label(ctx.facts,p.fact_id),'food_ids':[f.fact_id for f in foods],
                       'relaxed_optional_food':relax,'facts':[context.fact_view(x,ctx.facts,ctx.assessed_at) for x in evidence[:12]]})
        if filters:output[-1]['restaurants']=[dining.details(v,ctx) for v in vendors]
    if any('max_fen' in f for f in filters):
        from ..restaurant import price_range
        output.sort(key=lambda i:min(price_range(v['price_band'])[1] for v in i['restaurants']))
    return {'status':'ok' if output else 'no_candidates','code':None if output else 'NO_VERIFIED_SUPPLY_OR_OPENING','items':output[:6],
            'reply':dining.interpretation(filters)+dining.unapplied(missing)+('没有商家满足全部条件。'+('；'.join(rejected) if rejected else '商家来源或营业资料不足，未确认')+'。' if filters and not output else ''),
            'excluded':rejected}


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
    v2tools.require_single_day(snapshot)
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


def preview_adjustment(session,trip_id,snapshot,value,request_id,text=None):
    """Project an evidenced in-trip request onto the existing replan/confirm gate."""
    from ..engine.models import OngoingInput
    v2tools.current_day_scope(snapshot,text if text is not None else value.quote)
    if snapshot.row['status'] not in ('ACTIVE','PAUSED') or not snapshot.route.get('itinerary'):
        return {'status':'needs_clarification','reply':'请先开始今天已确认的行程，再修改剩余安排。'}
    if snapshot.envelope.position['status']!='fresh':
        return {'status':'needs_clarification','reply':'请先确认你现在所在的地点，再重排剩余行程。'}
    req=v2tools.requirements(snapshot);moment=snapshot.planning.assessed_at
    pending=[s for s in snapshot.draft.stops if s.progress=='pending' and s.kind!='return']
    position=snapshot.envelope.position['place_id'];route=snapshot.route['itinerary']
    current=pending[0] if pending and pending[0].place_id==position else None
    matches=[]
    if value.next_stop:
        remaining=pending[1:] if snapshot.envelope.progress.get('ongoing') else pending
        matches=remaining[:1]
        if not matches:return {'status':'needs_clarification','reply':'今天已没有下一站，请核对当前进度。'}
        # "下一站" is the reference; a name beside it may only repeat that stop or the place
        # the visitor stands at (the live model copies it). Any other name is a conflict.
        if value.target_name:
            names=lambda pid:(getattr(snapshot.planning.facts.get(('place',pid)),'name',None),*getattr(snapshot.planning.facts.get(('place',pid)),'aliases',()))
            if value.target_name not in (*names(matches[0].place_id),*names(position)):
                return {'status':'needs_clarification','reply':'你说的是下一站，还是另一站？请再说一下。'}
    elif value.target_name:
        for stop in pending:
            place=snapshot.planning.facts.get(('place',stop.place_id))
            show=snapshot.planning.facts.get(('show_session',stop.session_id))
            program=snapshot.planning.facts.get(('program',show.program_id)) if show else None
            if any(value.target_name in (getattr(f,'name',None),*getattr(f,'aliases',())) for f in (place,program) if f):matches.append(stop)
        if len(matches)!=1:return {'status':'needs_clarification','reply':'请明确今天剩余行程中的哪一站；不会替你选择或改动已完成记录。'}
    extra={};notes=([f'本次仅调整{snapshot.draft.business_date.isoformat()}今天剩余，其余日期保持原计划。'] if getattr(snapshot,'multi_requirements',None) else []);seconds=(value.minutes or 0)*60
    if value.operation=='skip_replan':
        stop=matches[0];extra['skipped_goal_ids']=(stop.stop_id,)
        req=req.model_copy(update={'goals':tuple(g for g in req.goals if g.goal_id!=stop.stop_id)})
        notes.append(('这是必去项目，' if stop.locked else '')+f'将跳过{context.label(snapshot.planning.facts,stop.place_id)}，确认后才生效。')
    if value.operation=='extend':
        if not current or matches and matches[0].stop_id!=current.stop_id:
            return {'status':'needs_clarification','reply':'当前地点无法对应正在游玩的第一项未完成活动，请确认地点和活动。'}
        if current.kind=='show':return {'status':'needs_clarification','reply':'演出有固定场次，不能延长公告时长；可以在演出后加休息。'}
        old=next((s for s in route['activities'] if s['goal_id']==current.stop_id),None)
        reserved=route.get('ongoing')
        end=reserved['ends_at'] if reserved and reserved['stop_id']==current.stop_id else old['departs_at'] if old else None
        if not end:return {'status':'needs_clarification','reply':'无法确定当前活动原来的剩余时长，请先核对进度。'}
        remaining=max(0,int((datetime.fromisoformat(end)-moment).total_seconds()))+seconds
        if remaining>21600:return {'status':'needs_clarification','reply':'当前站剩余时间太长，请缩短停留或调整结束时间。'}
        extra['ongoing']=OngoingInput(stop_id=current.stop_id,remaining_seconds=remaining)
        notes.append(f'{context.label(snapshot.planning.facts,position)}在原剩余时长上多待{value.minutes}分钟，预计至{(moment+timedelta(seconds=remaining)).astimezone(TZ):%H:%M}。')
    if value.operation=='rest':
        if matches and (not current or matches[0].stop_id!=current.stop_id):
            return {'status':'needs_clarification','reply':'休息只能放在当前确认地点之后，请核对你现在的位置。'}
        start=moment+timedelta(seconds=120)
        ongoing=snapshot.envelope.progress.get('ongoing')
        if ongoing:
            reserved=route.get('ongoing');old=next((s for s in route['activities'] if s['goal_id']==ongoing['stop_id']),None)
            end=reserved['ends_at'] if reserved else old['departs_at'] if old else None
            if not end or datetime.fromisoformat(end)<=moment:
                return {'status':'needs_clarification','reply':'当前活动预计结束时刻已过，请先报告剩余时长或完成进度。'}
            start=max(start,datetime.fromisoformat(end))
        goal=Goal(goal_id='rest:'+uuid5(UUID(str(request_id)),'rest').hex,target_kind='place',target_id=position,
                  kind='rest',required=True,duration_seconds=seconds,window_start=start,window_end=start+timedelta(seconds=seconds))
        req=req.model_copy(update={'goals':(*req.goals,goal)})
        notes.append(f'在{context.label(snapshot.planning.facts,position)}之后休息{value.minutes}分钟；未指定或核实座椅设施。')
    if value.operation=='delay':notes.append(f'按你报告已延误{value.minutes}分钟，从当前实际时刻{moment.astimezone(TZ):%H:%M}重排，已过去的时间不会重复累加。')
    body=v2tools.adjustment_request(snapshot,req,uuid5(UUID(str(request_id)),'adjustment'),**extra)
    result=engine.preview(session,trip_id,body,replanning=True)
    departure=moment+timedelta(seconds=extra['ongoing'].remaining_seconds if extra.get('ongoing') else 120)
    if value.operation=='rest':departure=max(departure,goal.window_end)
    travel=TravelTimeService(snapshot.planning)
    plans=result.get('result',{}).get('itineraries',())
    if getattr(snapshot,'multi_requirements',None):
        plans=[next(d['itinerary'] for d in p['days'] if d['day_id']==snapshot.day_id) for p in plans]
    selected={a['goal_id'] for a in plans[0]['activities']} if plans else set()
    if plans and plans[0].get('ongoing'):selected.add(plans[0]['ongoing']['stop_id'])
    for stop in pending:
        if stop.kind!='show' or stop.stop_id in extra.get('skipped_goal_ids',()) or stop.stop_id in selected:continue
        show=snapshot.planning.facts.get(('show_session',stop.session_id))
        if not show:continue
        deadline=show.starts_at-timedelta(seconds=req.show_buffer_seconds)
        if show.admission_deadline:deadline=min(deadline,show.admission_deadline)
        journeys=[j for mode in req.modes for j in travel.index.get((position,stop.place_id,mode),())
                  if travel.usable(j,req.model_copy(update={'starts_at':departure}))]
        earliest=departure+timedelta(seconds=min(map(travel_seconds,journeys))) if journeys else departure if position==stop.place_id else None
        if deadline<departure or earliest and earliest>deadline or plans:
            program=snapshot.planning.facts.get(('program',show.program_id))
            reason='已赶不上到场截止时间' if deadline<departure else '按现有交通估时赶不上到场截止时间' if earliest and earliest>deadline else '本版因剩余约束未排入'
            notes.append(f"{getattr(program,'name',None) or context.label(snapshot.planning.facts,stop.place_id)}的{show.starts_at.astimezone(TZ):%H:%M}场次{reason}；可查询下一场，或明确放弃这场后重新预览。")
    def changes(change,plan):
        change=v2tools.changes_for_remaining(change,snapshot.draft)
        ongoing=plan.get('ongoing')
        return change|{'removed':[g for g in change['removed'] if not ongoing or g!=ongoing['stop_id']]}
    result['alternative_changes']=[changes(c,p) for c,p in zip(result.get('alternative_changes',()),plans)]
    if result.get('changes'):result['changes']=result['alternative_changes'][0]
    result['adjustment_notes']=notes
    return result
