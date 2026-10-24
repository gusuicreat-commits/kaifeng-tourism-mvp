"""UI adapters to the existing planner and agent. No second solver or agent."""
import json
from uuid import UUID, uuid5
from typing import Literal
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, Field, field_validator, model_validator
from pydantic import ValidationError
from ..db import connect
from ..memory.session import current, Session, limit
from ..memory.storage import stamp
from . import basket, state, exploration_state
from .schema import Strict, EntityId
from .state_schema import CityDraft
from .engine import service as engine
from .engine.models import Goal, Requirements, PreviewRequest, ReplanRequest

router=APIRouter(prefix='/api/city/v1/trips/{trip_id}/exploration',tags=['Travel basket planning'])


class Constraints(Strict):
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


class Plan(Strict):
    request_id:UUID
    expected_version:int=Field(ge=1)
    basket_version:int=Field(ge=0)
    planning_version:int | None=Field(default=None,ge=0)
    constraints:Constraints


def unknown(message, ids=(), code='DATA_PENDING'):
    return {'status':'unknown','reply':message,'issues':[{'code':code,'message':message,'affected_ids':list(ids)}],
            'options':[],'planning':None,'formal_state_changed':False}


@router.get('/constraints')
def saved_constraints(trip_id:UUID,s:Session=Depends(current)):
    with connect() as db:
        state.owned(db,s,trip_id)
        return exploration_state.read(db,trip_id)


@router.post('/preview')
def preview(trip_id:UUID,body:Plan,s:Session=Depends(current)):
    return preview_plan(trip_id,body,s)


def preview_plan(trip_id,body,s,*,execution_guard=None):
    limit('exploration:'+s.id,20)
    data=body.model_dump(mode='json');scope='exploration:'+str(trip_id)
    with connect() as db:
        if execution_guard:execution_guard(db)
        row=state.owned(db,s,trip_id);state.check_version(row,body.expected_version);basket.guard(db,trip_id,body.basket_version)
        cached=state.receipt(db,s,body.request_id,scope,data)
        if cached is not None:return cached
        workspace=exploration_state.read(db,trip_id)
        planning_version=workspace['version']
        if body.planning_version is not None:exploration_state.guard(db,trip_id,body.planning_version)
        collected=basket.snapshot(db,s,trip_id);draft=CityDraft.model_validate_json(row['draft_json'])
        ctx=engine.context(db)
        values=body.constraints.model_dump(exclude={'origin_text','destination_text'})
        missing=[k for k in ('business_date','starts_at','ends_at') if values[k] is None]
        missing += [k+'_place_id' for k in ('origin','destination') if not values[k+'_place_id'] and not getattr(body.constraints,k+'_text')]
        unmatched=[k for k in ('origin','destination') if not values[k+'_place_id'] and getattr(body.constraints,k+'_text')]
        goals=[];unmapped=[];key_by_goal={};parent_groups={}
        # Retain all formal remaining goals, including locks. Saving intentions never edits these.
        old=engine.route_row(db,trip_id)
        original=Requirements.model_validate_json(old['requirements_json']) if old else None
        from .assistant.tools import requirements as draft_requirements
        if original:
            from types import SimpleNamespace
            goals=list(draft_requirements(SimpleNamespace(draft=draft,requirements=original,route={'itinerary':json.loads(old['itinerary_json'])})).goals)
        else:
            goals=[Goal(goal_id=x.stop_id,target_kind='session' if x.session_id else 'place',target_id=x.session_id or x.place_id,
                        kind=x.kind,required=x.locked,duration_seconds=x.duration_seconds,window_start=x.window_start,window_end=x.window_end)
                   for x in draft.stops if x.progress=='pending' and x.kind!='return']
        for item in collected['items']:
            if item['status']=='standby':continue
            if not item['fact_id']:unmapped.append(item['key']);continue
            fk=item['fact_kind'];identity=item['fact_id'];fact=ctx.facts.get((fk,identity))
            if fk=='vendor':identity=fact.place_id;fk='place'
            if fk=='show_session':fk='session'
            existing=next((g for g in goals if g.target_kind==fk and g.target_id==identity),None)
            if existing:
                if item['priority']=='must':goals=[g.model_copy(update={'required':True}) if g.goal_id==existing.goal_id else g for g in goals]
                key_by_goal[existing.goal_id]=item['key'];continue
            gid='basket:'+uuid5(UUID(str(trip_id)),item['key']).hex
            key_by_goal[gid]=item['key']
            goals.append(Goal(goal_id=gid,target_kind=fk,target_id=identity,
                kind='show' if fk in ('session','program') else 'dining' if fk=='food' or getattr(fact,'place_type',None)=='restaurant' else 'visit',
                required=item['priority']=='must',importance={'must':10,'want':6,'optional':1}[item['priority']]))
            if getattr(fact,'parent_place_id',None):parent_groups[identity]=fact.parent_place_id
        # Until internal duration/path coverage can be reconciled, do not double count a park and its POIs.
        grouped=[child for child,parent in parent_groups.items() if any(g.target_kind=='place' and g.target_id==parent for g in goals)]
    if missing:
        result=unknown('请补充'+ '、'.join({'business_date':'日期','starts_at':'开始时间','ends_at':'结束时间','origin_place_id':'出发地点','destination_place_id':'返程地点'}[k] for k in missing),'','PERSONAL_CHOICE_MISSING')
        result['questions']=[{'field':k} for k in missing]
    elif unmatched:result=unknown('已记下出发和返回地点，位置资料待核验，暂不能生成路线。',code='ENDPOINT_MATCH_PENDING')
    elif unmapped:result=unknown('所选内容的地点、商家或场次资料待添加；暂不能核验完整路线。',unmapped)
    elif grouped:result=unknown('景区与内部看点的时长、路径关系待添加；暂不重复计算成独立城市停靠点。',grouped,'PARENT_CHILD_PENDING')
    elif not goals:result=unknown('先从详情页加入想去的地方或美食。',code='BASKET_EMPTY')
    else:
        try:req=Requirements.model_validate(values|{'goals':goals,'alternatives':3})
        except ValidationError:raise HTTPException(422,detail='请检查同日开始、结束时间、目标数量与交通方式。') from None
        if row['status'] in ('ACTIVE','PAUSED'):
            plan=engine.preview(s,trip_id,ReplanRequest(request_id=uuid5(body.request_id,'engine'),expected_version=body.expected_version,requirements=req),replanning=True,planning_version=planning_version)
        else:plan=engine.preview(s,trip_id,PreviewRequest(request_id=uuid5(body.request_id,'engine'),expected_version=body.expected_version,requirements=req),planning_version=planning_version)
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
        state.check_version(state.owned(db,s,trip_id),body.expected_version)
        cached=state.receipt(db,s,body.request_id,scope,data)
        if cached is not None:return cached
        basket.guard(db,trip_id,body.basket_version);exploration_state.guard(db,trip_id,body.planning_version)
        workspace=exploration_state.read(db,trip_id);collected=basket.snapshot(db,s,trip_id)
        if not workspace['constraints']:raise HTTPException(422,detail='请先核验规划条件，再提交取舍。')
        constraints=Constraints.model_validate(workspace['constraints']);priorities={}
        if body.option_id:
            result=workspace['result'] or {}
            option=next((o for o in result.get('options',[]) if o['id']==body.option_id),None)
            if not option or result.get('basket_version')!=body.basket_version or result.get('trip_version')!=body.expected_version:
                raise HTTPException(409,detail='此协商选项已失效，请重新核验。')
            if workspace['rounds']>=2:raise HTTPException(422,detail='请直接调整时间或素材篮，不再重复追问。')
            if option['kind']!='priority':raise HTTPException(422,detail='请在规划条件中调整可用时段。')
            priorities[option['object_key']]={'priority':option['priority'],'status':'pending'}
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
    from . import facts
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
