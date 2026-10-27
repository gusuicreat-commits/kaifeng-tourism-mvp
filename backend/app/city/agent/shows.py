"""Dated catalog show queries, independent of saved itinerary targets."""
from datetime import datetime
from ...memory.context import redact
from .. import facts
from ..assistant import context, interpret
from ..schema import TZ


def query(action, snapshot):
    from .understand import clock_values
    from .dates import find_date_expressions, resolve_date
    items=snapshot.planning.facts;moment=snapshot.planning.assessed_at.astimezone(TZ)
    if action.target_name and action.target_name not in action.quote:
        return {'status':'needs_clarification','reply':'请明确本轮要查询的景区或节目名称。'}
    pool=[p for p in items.values() if isinstance(p,(facts.Program,facts.Place))
          and facts.planning_visible(p) and not getattr(p,'superseded_by',None)
          and (isinstance(p,facts.Program) and p.place_id or isinstance(p,facts.Place) and p.place_type=='scenic')]
    named=[p for p in pool if facts.name_matches(p,action.quote)]
    t=action.target_name;names=lambda p:(p.name,*p.aliases)
    # Visitors shorten catalog names (五鼠闹东京, 铁塔) or prefix the park (万岁山打铁花). The
    # target is already grounded in the quote, so a two-character-or-longer containment either
    # way identifies the record; exact names still win.
    hits=([p for p in pool if t in names(p)] or [p for p in pool if len(t)>=2 and any(
        len(n)>=2 and (t in n or n in t) for n in names(p))]) if t else []
    places=[p for p in hits if isinstance(p,facts.Place)]
    parks={p.fact_id for p in (*named,*places) if isinstance(p,facts.Place)}
    inside=lambda p:not parks or any(park in snapshot.planning.parks(p.place_id) for park in parks)
    named=[p for p in named if isinstance(p,facts.Place) or inside(p)]
    progs=[p for p in hits if isinstance(p,facts.Program) and inside(p)]
    # Preserve the existing itinerary-reference query for a pronoun such as “那场”.
    if not named and not t and interpret.refers_back(action.quote):return None
    # A stage name (英武楼 for 英武楼演出点) narrows to that venue's sessions when no park or
    # program carries the name.
    stages=[p for p in items.values() if isinstance(p,facts.Place) and p.place_type=='stage' and facts.planning_visible(p)
            and not p.superseded_by and len(t)>=2 and any(len(n)>=2 and (t in n or n in t) for n in names(p))] if t and not progs and not places else []
    if t and not progs and not places and not stages:
        return {'status':'needs_data','reply':'没有对应的景区或节目资料，请核对名称。'}
    if t and not progs and len(places)>1:
        return {'status':'needs_clarification','reply':'名称对应多个景区，请说明是哪一个。'}
    try:days={resolve_date(e,moment.date()) for e in find_date_expressions(action.quote)}-{None}
    except ValueError:return {'status':'needs_clarification','reply':'演出日期没核对上，请说明要查哪一天（YYYY-MM-DD）。'}
    if len(days)>1:
        return {'status':'needs_clarification','reply':'请明确要查哪一天的演出。'}
    day=days.pop() if days else moment.date()
    if day<moment.date():
        return {'status':'needs_clarification','reply':'只能查询今天及以后的演出场次。'}
    clocks=list(dict.fromkeys(clock_values(action.quote)))
    if len(clocks)>1:
        return {'status':'needs_clarification','reply':'请明确从哪个时刻之后查询演出。'}
    # A later day starts from its midnight; today never lists sessions already begun.
    threshold=moment if day==moment.date() else datetime.combine(day,datetime.min.time(),TZ)
    if clocks:
        threshold=max(threshold,datetime.combine(day,datetime.strptime(clocks[0],'%H:%M').time(),TZ))
    programs={p.fact_id:p for p in items.values() if isinstance(p,facts.Program) and facts.planning_visible(p)}
    ids={p.fact_id for p in (progs or ([] if places else named)) if isinstance(p,facts.Program)}
    def matches(session):
        program=programs.get(session.program_id)
        return program and (not ids or program.fact_id in ids) and (not stages or session.stage_place_id in {p.fact_id for p in stages}) and (
            not parks or any(park in snapshot.planning.parks(session.stage_place_id)
                             or park in snapshot.planning.parks(program.place_id) for park in parks))
    sessions=[s for s in items.values() if isinstance(s,facts.ShowSession) and matches(s)
              and facts.planning_visible(s) and not s.is_demo]
    today=[s for s in sessions if s.business_date==day]
    if not today:
        return {'status':'needs_data','reply':'这几天的场次还没有资料，节目介绍里的时间只供参考。','facts':[],'references':[]}
    upcoming=sorted((s for s in today if s.business_status=='active' and s.starts_at>threshold),key=lambda s:(s.starts_at,s.fact_id))
    if not upcoming:
        return {'status':'answered','reply':'该时刻之后没有更多已记录的演出场次。','facts':[],'references':[]}
    values=[];lines=[]
    for session in upcoming:
        program=programs[session.program_id];stage=items.get(('place',session.stage_place_id))
        # A readable dated announcement does not require route coordinates, but every
        # dependency still needs permission and current provenance.
        view=context.fact_view(session,items,session.starts_at)
        def readable(item, seen):
            key=(item.kind,item.fact_id)
            if key in seen:return True
            seen.add(key)
            if not facts.planning_visible(item) or any(i!='LOCATION_UNKNOWN' for i in facts.gaps(item,items,session.starts_at)):
                return False
            return all(ref in items and readable(items[ref],seen) for ref in facts.references(item))
        if not stage or not readable(session,set()):continue
        end=session.ends_at.astimezone(TZ).strftime('%H:%M') if session.ends_at else '结束待确认'
        lines.append(f'{redact(program.name)}｜{redact(stage.name)}｜{session.starts_at.astimezone(TZ).strftime("%H:%M")}–{end}')
        sources=[items.get(('source',sid)) for sid in session.source_ids]
        if any(s and '海报未标注日期' in (s.name or '') for s in sources):
            lines[-1] += '（海报未标注日期，按采集日记录）'
        values.append(view)
    if not values:
        return {'status':'needs_data','reply':'当天有场次记录，但来源、有效期或资料尚不完整，暂不能给出可用场次。','facts':[],'references':[]}
    label='当天' if day==moment.date() else f'{day.month}月{day.day}日'
    return {'status':'answered','reply':f'按{label}场次资料（未核验参考资料不代表现场保证）：\n'+'\n'.join(lines),
            'facts':values,'references':[{'kind':'show_session','id':s['id']} for s in values[:6]]}
