"""Edit one unstarted future day; all other dated snapshots stay immutable."""
from fastapi import HTTPException
from ..schema import TZ
from ..state_schema import CityDraftV2
from ..versions import read_draft
from .models import MultiDayItinerary
from .multi_day import day_requirements,applicable


def target_day(draft,plan,moment,day_id):
    if not isinstance(draft,CityDraftV2) or not isinstance(plan,MultiDayItinerary):
        raise HTTPException(422,detail='请先确认一套多日行程，再选择未来日期调整。')
    day=next((d for d in draft.days if d.day_id==day_id),None)
    saved=next((d for d in plan.days if d.day_id==day_id),None)
    if not day or not saved or day.business_date<=moment.astimezone(TZ).date():
        raise HTTPException(422,detail='请选择本次行程中尚未到来的一个日期；今天请使用今天剩余调整。')
    if (day.status!='pending' or saved.status!='pending' or any(s.progress!='pending' for s in day.stops)
            or saved.itinerary.completed_prefix or saved.itinerary.ongoing):
        raise HTTPException(422,detail='所选日期已有执行记录或已关闭，不能作为未开始的未来日改写。')
    return day,saved


def overnight_identity(items):
    return tuple(tuple(getattr(o,k) for k in ('overnight_id','from_day_id','to_day_id','night_date','place_id','source_kind','service_selection_id','service_version','binding_revision','check_in','check_out')) for o in items)


def mutable_ids(original,previous,day_id):
    locked=set()
    for d in previous.days:
        if d.day_id==day_id:continue
        locked.update(g.goal_id for g in d.requirements.goals)
        locked.update(s.stop_id for s in d.itinerary.completed_prefix)
        if d.itinerary.ongoing:locked.add(d.itinerary.ongoing['stop_id'])
    date=next(d.business_date for d in original.days if d.day_id==day_id)
    return {g.goal_id for g in original.goals if g.goal_id not in locked and date in g.allowed_dates
            and g.fixed_date in (None,date)}


def prepare_future(row,body,ctx,previous,original):
    draft=read_draft(row['draft_json']);day,current=target_day(draft,previous,ctx.assessed_at,body.day_id)
    if body.ongoing or body.end_ongoing or body.restore_route_version or body.spent_cost_fen is not None or body.spend_as_of is not None:
        raise HTTPException(422,detail='未来单日调整不记录实际进度、当前活动或费用，也不恢复其他日期的历史版本。')
    req=body.requirements
    if overnight_identity(req.overnights)!=overnight_identity(original.overnights):
        raise HTTPException(409,detail='单日调整不能改写已确认的过夜地点或酒店衔接。')
    if req.model_dump(exclude={'days','goals','solve_seconds','alternatives'})!=original.model_dump(exclude={'days','goals','solve_seconds','alternatives'}):
        raise HTTPException(409,detail='只改所选未来日时，全程范围、预算、已支付记录、交通偏好和酒店边界保持锁定。')
    old_days={d.day_id:d for d in original.days};new_days={d.day_id:d for d in req.days}
    if set(new_days)!=set(old_days) or any(new_days[k]!=v for k,v in old_days.items() if k!=day.day_id):
        raise HTTPException(409,detail='只能修改所选未来日的条件，其他日期必须保持原样。')
    target=new_days[day.day_id]
    # Inner endpoints participate in an overnight boundary shared with a locked
    # neighbour. Only the first departure / last return may change independently.
    for o in original.overnights:
        if ((o.from_day_id==day.day_id and target.destination_place_id!=o.place_id)
                or (o.to_day_id==day.day_id and target.origin_place_id!=o.place_id)):
            raise HTTPException(409,detail='更换过夜地点会影响相邻日期；本次单日调整请保留已确认的酒店衔接。')
    mutable=mutable_ids(original,previous,day.day_id)
    old={g.goal_id:g for g in original.goals};new={g.goal_id:g for g in req.goals};skipped=set(body.skipped_goal_ids)
    if not skipped<={gid for gid in mutable if old[gid].required} or skipped&set(new):
        raise HTTPException(422,detail='取消必去项必须明确指定本日原有必去目标，并从新安排中移除。')
    for gid,g in old.items():
        if gid not in mutable and new.get(gid)!=g:raise HTTPException(409,detail='其他日期的目标已锁定，不能删除、改写或移到所选日期。')
        if gid in mutable and g.required and gid not in skipped:
            other=new.get(gid)
            if not other or not other.required or any(getattr(other,k)!=getattr(g,k) for k in ('target_kind','target_id','fixed_place_id','fixed_session_id')):
                raise HTTPException(409,detail='原有必去目标必须保留；如需取消，请明确勾选取消必去项并核对预览。')
    selected=[]
    for gid,g in new.items():
        if gid not in old or gid in mutable:
            if gid not in old and (g.allowed_dates!=(day.business_date,) or g.fixed_date not in (None,day.business_date)):
                raise HTTPException(409,detail='新增目标必须明确只属于所选未来日。')
            if gid in old and (g.allowed_dates!=old[gid].allowed_dates or g.fixed_date!=old[gid].fixed_date):
                raise HTTPException(409,detail='本次不能把已有目标移动到其他日期。')
            if not applicable(g,target):raise HTTPException(422,detail='目标时间窗不在所选未来日的游玩时段内。')
            selected.append(g)
    if len(set(body.fixed_goal_ids))!=len(body.fixed_goal_ids) or not set(body.fixed_goal_ids)<={g.goal_id for g in selected}:
        raise HTTPException(422,detail='指定顺序只能包含所选日期中保留的目标，且不能重复。')
    objective=target.objective or current.requirements.objective
    if req.pricing and req.pricing.hard_limit:objective='lower_cost'
    daily=day_requirements(req,target,selected,objective,req.solve_seconds)
    daily=daily.model_copy(update={'alternatives':req.alternatives})
    return daily,(),None,tuple(body.fixed_goal_ids),current,day
