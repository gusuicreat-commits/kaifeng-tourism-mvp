"""Apply grounded daily preferences to an explicitly authorized remaining-trip preview."""
from datetime import datetime
from uuid import UUID,uuid5
from ..assistant import tools
from ..engine import service as engine
from ..engine.models import MultiDayRequirements,ReplanRequestV2
from ..schema import TZ
from . import date_range


def preview(session,trip_id,snapshot,intent,request_id,text):
    day_id=tools.continuous_scope(snapshot,text)
    full=snapshot.multi_requirements;today=snapshot.planning.assessed_at.astimezone(TZ).date()
    active={d.day_id:d for d in snapshot.multi_draft.days if d.business_date>=today and d.status!='closed'}
    changes={k:{} for k in active};goals=list(full.goals)
    def clarify(message):return {'status':'needs_clarification','reply':message}
    for f in intent.planning.fields:
        if f.field=='date' and f.value and date_range.resolve(str(f.value),today)==today:continue
        if f.field not in ('pace','objective','mode','start_time','end_time') or f.value is None:return clarify('请修改每日时间、节奏或交通偏好；全程日期、预算和酒店边界保持原选择。')
        targets=list(active)
        if f.day_ref:
            date=date_range.date_for_ref(date_range.day_reference(f.day_ref,text,today),full.start_date,full.end_date)
            targets=[k for k,d in active.items() if d.business_date==date]
            if not targets:return clarify('该日期不属于今天及后续未开始的行程。')
        if f.field in ('start_time','end_time'):
            if f.field=='start_time' and day_id in targets:return clarify('今天剩余从当前位置和当前时间继续；开始时间请指定未来日期。')
            for key in targets:
                changes[key]['starts_at' if f.field=='start_time' else 'ends_at']=datetime.fromisoformat(active[key].business_date.isoformat()+'T'+str(f.value)+':00+08:00')
        else:
            for key in targets:changes[key]['modes' if f.field=='mode' else f.field]=(f.value,) if f.field=='mode' else f.value
    for c in intent.constraints:
        if c.field in ('pace','objective') and c.value is not None:
            for key in active:
                if c.field in changes[key] and changes[key][c.field]!=c.value:return clarify('同一偏好出现不同取值，请按日期明确条件。')
                changes[key][c.field]=c.value
        else:return clarify('请在行程页核对这项具体条件，避免扩大修改范围。')
    if intent.planning.choices:return clarify('请在对应日期单独调整目标取舍，再预览今天及后续天；本次没有修改素材篮或目标。')
    req=MultiDayRequirements.model_validate(full.model_dump()|{'days':[d.model_copy(update=changes.get(d.day_id,{})) for d in full.days],'goals':goals})
    body=ReplanRequestV2(request_id=uuid5(UUID(str(request_id)),'suffix-plan'),expected_version=snapshot.row['version'],requirements=req,scope='current_and_future',day_id=day_id)
    result=engine.preview(session,trip_id,body,replanning=True);result['adjustment_notes']=['今天剩余及后续天一起重新核算，已完成记录与固定安排保留。']
    return result
