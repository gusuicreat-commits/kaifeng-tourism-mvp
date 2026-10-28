"""Validate extracted planning fields; propose inputs to the existing workflow, never commit routes."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from .. import facts
from ..schema import TZ
from .models import PlanningIntent, PlanningField, BasketProposal, BasketProposalItem, BasketChoicePrompt, MaterialChoice, Action, Understanding
from .dates import DATE_EXPRESSION, find_date_expressions, resolve_date
from .evidence import request_evidence, effective_clock, preference_evidence


def _fact_names(fact,snapshot):
    if isinstance(fact,facts.Vendor):fact=snapshot.planning.facts.get(('place',fact.place_id))
    return tuple(name for name in (getattr(fact,'name',None),*getattr(fact,'aliases',())) if name)


def _field_check(f, text, today):
    """Name of the check this field's value fails against its own quote, or None."""
    from .understand import clock_grounded
    try:
        if f.value is None:return None
        if f.field in ('start_date','end_date'):
            from .date_range import field_date
            field_date(f,today);return None
        if f.field=='date':
            if f.value in f.quote:
                resolve_date(f.value,today);return None
            # The model may normalize the quoted expression ("明晚" -> "明天" or ISO); both must name the same day.
            expressions=find_date_expressions(f.quote)
            try:value=date.fromisoformat(str(f.value))
            except ValueError:value=resolve_date(str(f.value),today)
            return None if expressions and resolve_date(expressions[-1],today)==value else 'date_not_in_quote'
        if f.field in ('start_time','end_time'):return None if clock_grounded(f.value,f.quote,text) else 'clock_not_in_quote'
        if f.field in ('city','origin','destination'):return None if f.value in f.quote else 'place_not_in_quote'
        if f.field in ('pace','objective','mode'):
            preference_evidence(f.field,f.value,f.quote);return None
        if f.field in ('trip_days','trip_nights'):
            from .date_range import counts
            if f.field=='trip_days' and '周末' in f.quote:
                return None if f.value==2 else 'trip_days_not_in_quote'
            numbers=counts(f.quote,'天' if f.field=='trip_days' else '(?:晚|夜)')
            return None if numbers and f.value==max(numbers) else f.field+'_not_in_quote'
        if f.field=='budget_fen':
            # The model selects the amount; other numbers in its quote are not conflicting budgets.
            amounts=_money_amounts(f.quote)
            if f.value not in amounts:return 'budget_not_in_quote'
            if f.budget_basis is None:return 'budget_basis_missing'
    except ValueError as exc:
        if f.field in ('start_date','end_date'):return str(exc)
        return 'date_unresolved' if f.field=='date' else 'preference_not_in_quote'
    return None


_CHINESE_DIGITS={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}


def _chinese_number(value):
    if value.isdigit():return int(value)
    if not value or any(c not in _CHINESE_DIGITS and c not in '十百千万' for c in value):raise ValueError('invalid number')
    units={'十':10,'百':100,'千':1000,'万':10000};total=0;section=0;number=0
    for char in value:
        if char in _CHINESE_DIGITS:number=_CHINESE_DIGITS[char]
        else:
            unit=units[char]
            if unit==10000:
                section=(section+number)*unit;total+=section;section=number=0
            else:
                section+=(number or 1)*unit;number=0
    return total+section+number


def _money_amounts(quote):
    values=[]
    for match in re.finditer(r'(?<!\d)(\d+(?:\.\d{1,2})?|[零一二两三四五六七八九十百千万]+)',quote):
        token=match[1]
        if re.match(r'\s*个?\s*[天晚夜人位间房]',quote[match.end():]):continue
        try:
            values.append(int(Decimal(token)*100) if token[0].isdigit() else _chinese_number(token)*100)
        except (InvalidOperation,ValueError):continue
    return values


def _choice_in_quote(choice,snapshot):
    return choice.name in choice.quote or any(
        choice.name in _fact_names(item,snapshot)
        and any(len(alias)>1 and alias in choice.quote for alias in _fact_names(item,snapshot))
        for item in snapshot.planning.facts.values())


def _short_choice_matches(choice,snapshot,items=None):
    if _choice_in_quote(choice,snapshot):return None
    if choice.status!='standby' and not choice.priority_stated:return []
    # The visitor's short form is a common substring (>= 2 characters, so it contains a
    # two-character window) of the quote and the name the model chose. Basket items
    # containing that short form are the candidates: one is used, several are asked.
    # Windows come from the chosen name only, so another place mentioned in the same
    # sentence ("夜市不去了，上河园留着") is not a candidate.
    chosen={choice.name,*(name for fact in _choice_facts(choice.name,snapshot) for name in _fact_names(fact,snapshot))}
    windows={name[i:i+2] for name in chosen for i in range(len(name)-1) if name[i:i+2] in choice.quote}
    # Items covering more of the visitor's fragments win ("双桥的夜市": 双桥夜市 over 双桥会馆).
    score={item['key']:sum(any(w in name for name in _material_names(item,snapshot)) for w in windows)
           for item in (snapshot.materials if items is None else items)}
    best=max(score.values(),default=0)
    return [item for item in (snapshot.materials if items is None else items) if best and score[item['key']]==best]


def grounded(planning, text, snapshot, dropped=None, meta=None, corrections=()):
    """Check evidence for every planning field.

    With ``dropped`` (a list), a field whose value is not supported by its quote is
    removed and recorded as ``{'field','check'}``, so the rest of the request still proceeds and
    only that field is asked again. Without it, any unsupported field raises.
    """
    today=snapshot.envelope.identity.generated_at.astimezone(TZ).date()
    if planning.quote not in text:raise ValueError('planning quote absent')
    request_evidence(text,planning.quote)
    if planning.operation=='continue':
        if planning.fields or planning.choices:
            planning=planning.model_copy(update={'operation':'update'})
            return grounded(planning,text,snapshot,dropped,meta,corrections)
        control=planning.quote.strip().rstrip('。！!')
        if control not in ('好','好的','可以','就这样','就这样吧','就按这个安排','生成安排','开始安排','帮我安排','安排','规划') and not re.search(r'(?:开始|生成|继续|帮我|请).{0,20}(?:安排|规划)',control):
            raise ValueError('continue needs an explicit planning request')
    from .date_range import day_key,day_reference,scope_check,daily_evidence,multiple_days
    from .requirement_context import planning_values
    range_fields=[f for f in planning.fields if f.field in ('date','start_date','end_date','trip_days')
                  and f.quote in text and _field_check(f,text,today) is None]
    multi=any(f.field in ('start_time','end_time','origin','destination') and not f.scope and not f.day_ref for f in planning.fields) \
        and multiple_days(planning_values(snapshot),range_fields,today)
    seen={};kept=[]
    def drop(item,field,check,part):
        dropped.append({'field':field,'check':check})
        if meta is not None:
            from .understand import _drop, _drop_category
            sequence=planning.fields if part=='fields' else planning.choices
            _drop(meta,f'planning.{part}[{sequence.index(item)}]',_drop_category(check),check,item.quote,field=field)
    for f in planning.fields:
        # A quote the model did not copy from the request only loses that field;
        # negation/hypothetical checks still protect the whole turn.
        if f.quote not in text:
            if dropped is None:raise ValueError('planning evidence absent')
            drop(f,f.field,'quote_not_in_request','fields');continue
        try:
            request_evidence(text,f.quote)
            scope_check(f,text)
            if f.day_ref:day_reference(f.day_ref or f.quote,text,today)
            key=(f.field,day_key(f,today))
            check=_field_check(f,text,today)
        except ValueError as exc:check=str(exc)
        if check is not None:
            if dropped is None:raise ValueError(check)
            drop(f,f.field,check,'fields');continue
        if multi and f.field in ('start_time','end_time','origin','destination') and not f.scope and not f.day_ref \
                and len({item.value for item in planning.fields if item.field==f.field})==1 and daily_evidence(f.quote,text,f):
            f=f.model_copy(update={'scope':'every_day'});key=(f.field,day_key(f,today))
            if meta is not None:meta.setdefault('recovered_fields',[]).append({'field':f.field,'scope':'every_day','source':'sentence_scope','quote':f.quote})
        value=f.value
        if value is not None and f.field in ('date','start_date','end_date'):
            from .date_range import resolve
            value=resolve(str(value),today)
        previous=seen.get(key,[])
        if previous:
            if any(f.quote in c.after and all(item.quote in c.before for item,_ in previous) for c in corrections):
                previous=[]
            elif any(old==value for _,old in previous):continue
        seen[key]=[*previous,(f,value)]
    for (field,_),entries in seen.items():
        if len(entries)==1:
            kept.append(entries[0][0]);continue
        if dropped is None:raise ValueError('conflicting planning values')
        items=[item for item,_ in entries]
        unique=[str(item.value) for item in items]
        if meta is not None:
            ref=items[0].day_ref or ('每天' if items[0].scope else '')
            meta.setdefault('planning_conflicts',[]).append({'field':field,**({'day_ref':ref} if ref else {}),
                'question':ref+'你说的是“'+'”还是“'.join(unique)+'”？'})
        for item in items:drop(item,field,'conflicting_values','fields')
    kept_choices=[]
    for choice in planning.choices:
        if choice.quote not in text:
            if dropped is None:raise ValueError('material evidence absent')
            drop(choice,'choice','choice_not_in_quote','choices');continue
        matches=_short_choice_matches(choice,snapshot)
        if matches==[]:
            if dropped is None:raise ValueError('material evidence absent')
            drop(choice,'choice','choice_not_in_quote','choices');continue
        try:
            request_evidence(text,choice.quote)
            if choice.day_ref:day_reference(choice.day_ref,text,today)
        except ValueError as exc:
            if dropped is None:raise
            drop(choice,'choice',str(exc),'choices');continue
        if matches and meta is not None:
            meta.setdefault('short_name_matches',[]).append({'name':choice.name,'method':'common_substring',
                'material_keys':[item['key'] for item in matches]})
        kept_choices.append(choice)
    if tuple(kept)!=planning.fields or tuple(kept_choices)!=planning.choices:
        planning=planning.model_copy(update={'fields':tuple(kept),'choices':tuple(kept_choices)})
    if meta is not None:
        base=planning_values(snapshot)
        departure=_now_departure(planning,text,snapshot,base)
        if departure and base.get('business_date') in (None,today.isoformat()):
            meta.setdefault('recovered_fields',[]).extend(
                {'field':field,'source':'now_departure','quote':departure[0]} for field in ('date','start_time'))
    return planning


def weather_replan(quote):
    if quote not in ('可以，按天气调整行程','按天气重新核算行程'):
        raise ValueError('weather replan requires explicit consent')
    return PlanningIntent(operation='weather_replan',quote=quote)


def explicit_round_trip(text):
    """A complete, literal endpoint control; never salvage part of a larger request."""
    value=text.strip().rstrip('。！!')
    match=re.fullmatch(r'从([\w·（）() -]{1,120})出发(?:和|并)(?:返回|返程)',value)
    if not match:return None
    name=match[1].strip()
    if not name or re.search(r'不是|不要|不从|不去|别去|取消|还是|或者|改成|改为|出发|返回',name):return None
    return PlanningIntent(quote=value,fields=tuple(
        PlanningField(field=role,value=name,quote=name) for role in ('origin','destination')))


def offline(text, snapshot):
    """Only unambiguous controls and bare structured answers work without a model."""
    value=text.strip().rstrip('。！!')
    endpoints=explicit_round_trip(text)
    if endpoints:return grounded(endpoints,text,snapshot)
    if value in ('好','好的','可以','就这样','就这样吧','就按这个安排','生成安排','开始安排','帮我安排','安排','规划'):
        return PlanningIntent(operation='continue',quote=value)
    fields=[]
    if len(find_date_expressions(value))==1:
        try:
            resolve_date(value, snapshot.envelope.identity.generated_at.astimezone(TZ).date())
        except ValueError:
            pass
        else:
            fields=[PlanningField(field='date',value=value,quote=value)]
    match=re.fullmatch(r'(\d{2}:\d{2})(?:到|—|-)(\d{2}:\d{2})',value)
    if match:fields=[PlanningField(field='start_time',value=match[1],quote=match[1]),PlanningField(field='end_time',value=match[2],quote=match[2])]
    if not fields:return None
    try:return grounded(PlanningIntent(quote=value,fields=tuple(fields)),text,snapshot)
    except ValueError:return None


def _dish_served_by_basket(name, snapshot):
    """A dish named as a choice is already covered when a basket shop serves it."""
    from .models import Constraint
    from .understand import food_covered_by_material
    return food_covered_by_material(Constraint(field='food',value=name,strength='soft',quote=name),None,snapshot)


def _choice_facts(name,snapshot):
    catalog=snapshot.planning.facts
    canonical=facts.place_by_name(catalog,name)
    places=[canonical] if canonical else [p for p in catalog.values() if isinstance(p,facts.Place) and facts.planning_visible(p)
        and name in _fact_names(p,snapshot)]
    foods=[f for f in catalog.values() if isinstance(f,facts.Food) and facts.planning_visible(f) and name in _fact_names(f,snapshot)]
    if not places and not foods and len(name)>1:
        places=[p for p in catalog.values() if isinstance(p,facts.Place) and facts.planning_visible(p) and name in _fact_names(p,snapshot)[0]]
    seen=set();result=[]
    for item in (*places,*foods):
        key=(item.kind,item.fact_id)
        if key not in seen:seen.add(key);result.append(item)
    return result


_ENTRY_SUFFIX = re.compile(r'(?:大门口|门口|门前|大门|正门|入口)\s*$')


def _resolve_endpoint(catalog, name):
    """Resolve an endpoint literally, with one bounded entrance suffix fallback."""
    resolved=facts.place_by_name(catalog,name)
    if resolved:return resolved,False
    stripped=_ENTRY_SUFFIX.sub('',name).strip()
    if not stripped or stripped==name:return None,False
    resolved=facts.place_by_name(catalog,stripped)
    return (resolved,True) if resolved else (None,False)


def _material_names(item,snapshot):
    fact=snapshot.planning.facts.get((item['fact_kind'],item['fact_id']))
    return {item['name'],*_fact_names(fact,snapshot)}


def _choice_attitude(choice,planning,resolved,matches,snapshot):
    if any(choice.quote in f.quote for f in planning.fields):return False
    names={choice.name}
    for fact in resolved:names.update(_fact_names(fact,snapshot))
    for item in matches:names.update(_material_names(item,snapshot))
    pattern='|'.join(re.escape(name) for name in sorted(names,key=len,reverse=True) if name)
    return bool(re.search(r'\w',re.sub(pattern,'',choice.quote)))


PREFERENCE_LABELS={'pace':{'relaxed':'放松','standard':'标准','compact':'紧凑'},
                   'objective':{'balanced':'均衡','less_walking':'少走路','less_waiting':'少等待','lower_cost':'省钱'}}


def preference_notes(intent):
    """Name every model-classified preference so the visitor sees it before confirming."""
    fields=intent.planning.fields if intent.planning else ()
    notes=[f'已按“{f.quote}”调整为{PREFERENCE_LABELS[f.field].get(f.value,f.value)}。' for f in fields if f.field in PREFERENCE_LABELS]
    planned={f.field for f in fields if f.field in PREFERENCE_LABELS}
    notes+=[f'已按“{c.quote}”调整为{PREFERENCE_LABELS[c.field].get(c.value,c.value)}。' for c in intent.constraints
            if c.field in PREFERENCE_LABELS and c.field not in planned and c.value is not None]
    return notes


def per_person_budget(values,planning,text):
    """Record a stated per-person budget and party size in ``values``; return the note for the visitor."""
    from .budget_check import party_size,daily,recorded_note
    party=party_size(text)
    if party:values['party_size']=party
    field=next((f for f in planning.fields if f.field=='budget_fen' and f.budget_basis=='per_person'),None)
    if field is None:return []
    values.update(per_person_budget_fen=field.value,per_person_budget_daily=daily(field.quote,text))
    return [recorded_note(values)]


_NOW_DEPARTURE=re.compile(r'(?:现在|马上|立刻|立即|这就|这会儿|即刻).{0,20}?(?:出发|走|动身|出门|过去|启程)')


def _now_departure(planning,text,snapshot,base):
    from .multi_day_intent import is_multi
    if is_multi(base,planning) or any(f.field in ('date','start_time') and f.value is not None for f in planning.fields):return None
    moment=snapshot.envelope.identity.generated_at.astimezone(TZ)
    start=moment.replace(second=0,microsecond=0)+timedelta(minutes=5-moment.minute%5)
    if start<moment+timedelta(minutes=1):start+=timedelta(minutes=5)
    if start.date()!=moment.date():return None
    for clause in re.split(r'[，,。；;！!？?\n]',text):
        clause=clause.strip()
        if not _NOW_DEPARTURE.search(clause) or find_date_expressions(clause):continue
        try:request_evidence(text,clause)
        except ValueError:continue
        return clause,start
    return None


def proposal(session, trip_id, snapshot, intent, text, *, today=None):
    from ...db import connect
    from .. import basket
    from ..exploration import Constraints
    p=intent.planning
    if p.operation=='weather_replan':
        weather_replan(p.quote)
        from .. import weather
        return weather.adjustment_preview(session,trip_id,snapshot)
    if any(a.kind in ('progress','position','cancel') for a in intent.actions):
        return {'status':'needs_clarification','reply':'标记进度、位置或取消，请单独说一下。'}
    # Requirements the basket workflow cannot take yet are reported back instead of
    # blocking the date/time/endpoint update the visitor also asked for.
    notes=preference_notes(intent)
    notes+=[f'预算按总额{f.value/100:g}元理解。' for f in p.fields if f.field=='budget_fen' and f.budget_basis!='per_person']
    for c in intent.constraints:
        if c.field=='food':
            if not any(c.value in (choice.name,*(a for f in _choice_facts(choice.name,snapshot) for a in _fact_names(f,snapshot))) for choice in p.choices):
                notes.append(f'想吃{c.value}：把有这道菜的店加进素材篮，就能排进行程。' if c.value else '想吃什么？告诉我菜名或店名。')
        elif c.field not in ('pace','objective','return_by'):
            notes.append(f'“{c.quote[:30]}”这次还没排进去，确认方案前请留意。')
    saved=snapshot.envelope.planning_constraints
    from .requirement_context import planning_values
    base=planning_values(snapshot)
    from .multi_day_intent import is_multi,prepare
    if is_multi(base,p):
        return prepare(session,trip_id,snapshot,intent,text,today=today,notes=notes)
    c=Constraints.model_validate(base);values=c.model_dump(mode='json');today=today or snapshot.envelope.identity.generated_at.astimezone(TZ).date()
    fields={f.field:f.value for f in p.fields if not (f.field=='budget_fen' and f.budget_basis=='per_person')}
    # The application's city scope is not a navigable waypoint. Require an endpoint role
    # before interpreting that city name as an origin or return location.
    for slot,markers in (('origin',('从','起点')),('destination',('回','返程','终点','结束','住'))):
        if fields.get(slot) in ('开封','开封市') and not any(marker in text for marker in markers):
            fields['city']=fields.pop(slot)
    # "…出发，十点前回来" returns to the starting point unless another endpoint is named.
    if 'origin' in fields and 'destination' not in fields and re.search(r'回来|返回|原路回|回到出发',text) and not re.search(r'回(?:到|去)?(?:酒店|宾馆|住处|民宿)',text):
        fields['destination']=fields['origin']
    if 'city' in fields and fields['city'] not in ('开封','开封市'):
        return {'status':'needs_data','reply':'目前只支持开封的行程。'}
    for constraint in intent.constraints:
        # "十点前回来" is the end of the planning window when no end time was stated.
        slot={'return_by':'end_time'}.get(constraint.field,constraint.field)
        if slot not in ('pace','objective','end_time') or constraint.value is None:continue
        if slot in fields and fields[slot]!=constraint.value:
            return {'status':'needs_clarification','reply':'同一项条件说了两个不同的值，以哪个为准？'}
        fields[slot]=constraint.value
    departure=_now_departure(p,text,snapshot,base)
    if departure:
        if values['business_date'] and values['business_date']!=today.isoformat():
            saved_day=date.fromisoformat(values['business_date'])
            return {'status':'needs_clarification','reply':f'已保存的日期是{saved_day.month}月{saved_day.day}日，现在出发是指今天吗？'}
        fields.update(date=today.isoformat(),start_time=departure[1].strftime('%H:%M'))
        notes.append(f"按现在出发：今天 {fields['start_time']} 开始。")
    if p.operation=='update' and not fields and not p.choices and not any(f.field=='budget_fen' for f in p.fields):
        return {'status':'needs_clarification','reply':'你想哪天出发？也可以先告诉我想去的地方。'}
    if 'date' in fields:values['business_date']=resolve_date(fields['date'],today).isoformat()
    day=values['business_date']
    for field,key in (('start_time','starts_at'),('end_time','ends_at')):
        time=fields.get(field) or (datetime.fromisoformat(values[key]).astimezone(TZ).strftime('%H:%M') if values[key] else None)
        if fields.get(field) and not day:
            return {'status':'needs_clarification','reply':'你打算哪天去？'}
        values[key]=day+'T'+time+':00+08:00' if day and time else None
    if values['starts_at'] and values['ends_at'] and values['ends_at']<=values['starts_at']:
        return {'status':'needs_clarification','reply':'结束时间要晚于出发时间，再确认一下？'}
    for field,key in (('origin','origin_place_id'),('destination','destination_place_id')):
        if field not in fields:continue
        resolved,normalized=_resolve_endpoint(snapshot.planning.facts,fields[field])
        values[key]=resolved.fact_id if resolved else None
        values[field+'_text']=None if resolved else fields[field]
        if normalized:notes.append(f"{'起点' if field=='origin' else '终点'}按{resolved.name}处理。")
    for field in ('pace','objective','budget_fen'):
        if field in fields:values[field]=fields[field]
    if 'mode' in fields:values['modes']=[fields['mode']]
    notes+=per_person_budget(values,p,text)
    validated=Constraints.model_validate(values)
    changes=[];ambiguities=[];asked=set()
    if p.choices:
        with connect() as db:items=basket.snapshot(db,session,trip_id)['items']
        used=set()
        for choice in p.choices:
            resolved=_choice_facts(choice.name,snapshot)
            short_matches=_short_choice_matches(choice,snapshot,items)
            # A dish (exact Food name/alias) is matched only as that dish: "灌汤包" must not
            # pick a shop whose name happens to contain it ("第一楼灌汤包总店"), or a wish to
            # eat it would become a change to that shop's priority.
            if short_matches is not None:
                matches=short_matches
            elif any(isinstance(f,facts.Food) for f in resolved):
                foods={(f.kind,f.fact_id) for f in resolved if isinstance(f,facts.Food)}
                matches=[i for i in items if (i['fact_kind'],i['fact_id']) in foods]
                if not matches and _dish_served_by_basket(choice.name,snapshot):continue
            # A short name ("第一楼") may stand for one basket item's full name.
            else:matches=[i for i in items if choice.name in _material_names(i,snapshot)] or [i for i in items if any(i['fact_kind']==f.kind and i['fact_id']==f.fact_id for f in resolved)] or [i for i in items if len(choice.name)>1 and any(choice.name in name for name in _material_names(i,snapshot))]
            if choice.name in used:continue
            used.add(choice.name)
            if not _choice_attitude(choice,p,resolved,matches,snapshot):
                if matches:continue
                choice=choice.model_copy(update={'priority':'want','status':'pending'})
            if not matches and _dish_served_by_basket(choice.name,snapshot):continue
            if not matches:
                if choice.status=='standby':
                    notes.append(f'“{choice.name}”不在素材篮里，无需改为候补。');continue
                if len(resolved)!=1:
                    candidates=[{'kind':f.kind,'object_id':f.fact_id,'name':_fact_names(f,snapshot)[0],'address':getattr(f,'address',None)} for f in resolved[:3]]
                    if candidates:
                        ambiguities.append(BasketChoicePrompt(query=choice.name,quote=choice.quote,
                            priority=choice.priority,candidates=tuple(candidates)).model_dump(mode='json'))
                        notes.append(f'“{choice.name}”对应多个对象，请选一个：'+ '、'.join(f['name'] for f in candidates)+'。')
                        if len({f['name'] for f in candidates})==1 and not all(f['address'] for f in candidates):
                            notes.append('资料里有重复记录，部分记录没有地址等区分信息。')
                    else:notes.append(f'资料里没有找到“{choice.name}”，暂不能加入素材篮。')
                    continue
                f=resolved[0]
                changes.append(BasketProposalItem(operation='put',kind=f.kind,object_id=f.fact_id,name=_fact_names(f,snapshot)[0],
                    priority=choice.priority,quote=choice.quote))
                continue
            if len(matches)>1:
                # The model read the visitor's short form as all of its candidates
                # ("夜市不去了" with two night markets, one choice each): each choice keeps
                # its own exact item and the visitor confirms them together. A model
                # that picked only some of them is not trusted to choose: ask once.
                exact=[i for i in matches if choice.name in _material_names(i,snapshot)]
                if len(exact)==1 and all(any(c.name in _material_names(i,snapshot) for c in p.choices) for i in matches):
                    matches=exact
            if len(matches)!=1:
                key=(choice.quote,frozenset(i['key'] for i in matches))
                if key in asked:continue
                asked.add(key)
                notes.append(f"素材篮里有{'、'.join(i['name'] for i in matches)}，你说的是哪一个？" if short_matches
                             else f'“{choice.name}”在素材篮里有好几项，是哪一个？')
                candidates=[{'kind':i['kind'],'object_id':i['id'],'name':i['name'],
                    'address':getattr(snapshot.planning.facts.get((i['fact_kind'],i['fact_id'])),'address',None)} for i in matches]
                ambiguities.append(BasketChoicePrompt(query=choice.name,quote=choice.quote,
                    priority=choice.priority,priority_stated=choice.priority_stated,
                    operation='standby' if choice.status=='standby' else 'put',candidates=tuple(candidates)).model_dump(mode='json'))
                continue
            current=matches[0]
            if not choice.priority_stated:
                choice=choice.model_copy(update={'priority':current['priority']})
            if choice.status=='standby':
                if current['status']=='arranged':
                    notes.append(f"{current['name']}已在正式行程里，途中想跳过可以说'跳过{current['name']}'。");continue
                if current['status']!='standby':
                    changes.append(BasketProposalItem(operation='standby',kind=current['kind'],object_id=current['id'],
                        name=current['name'],priority=choice.priority,quote=choice.quote))
                continue
            # A choice that already matches the basket is a harmless echo of the
            # existing selection, not a material change. It can accompany date/
            # time fields in a single-utterance plan.
            if current['priority']==choice.priority and current['status']==choice.status:
                continue
            changes.append(BasketProposalItem(operation='standby' if choice.status=='standby' else 'put',kind=current['kind'],
                object_id=current['id'],name=current['name'],priority=choice.priority,quote=choice.quote))
    changes=list({(i.kind,i.object_id):i for i in changes}.values())
    return {'status':'planning_input','reply':'好的，正在安排。','notes':notes,
            **({'basket_proposal':BasketProposal(items=tuple(changes),basket_version=snapshot.envelope.basket['version']).model_dump(mode='json')} if changes else {}),
            **({'basket_choices':ambiguities} if ambiguities else {}),
            'planning_action':{'trip_id':str(snapshot.envelope.identity.trip_id),'operation':p.operation,'constraints':validated.model_dump(mode='json'),
                'planning_version':saved.get('version',0),'basket_version':snapshot.envelope.basket['version'],
                'trip_version':snapshot.row['version'],'date_changed':'date' in fields}}


def pending_skip(intent, snapshot, text):
    """Turn a modelled skip into a basket standby proposal when no formal stop matches."""
    state=intent.state_request
    # Before departure a pending basket item is skipped or cancelled the same way: standby.
    if not state or state.operation not in ('skip','cancel') or not state.target_name:return None
    request_evidence(text,state.quote)
    query=state.target_name
    matches=[]
    for item in snapshot.materials:
        names=_material_names(item,snapshot)
        if query in names or any(len(query)>1 and query in name for name in names):matches.append(item)
    # The model may name the full place ("鼓楼夜市") while the visitor said a short form
    # ("夜市") that fits several basket items; candidates then follow the visitor's words.
    short=_short_choice_matches(MaterialChoice(name=query,priority='want',status='standby',quote=state.quote),snapshot)
    if short is not None:matches=short
    unique={item['key']:item for item in matches}
    route_version=snapshot.envelope.identity.route_version
    route_ids={value for s in snapshot.draft.stops for value in (getattr(s,'place_id',None),getattr(s,'session_id',None)) if value}
    for item in unique.values():
        fact=snapshot.planning.facts.get((item['fact_kind'],item['fact_id']))
        route_target=getattr(fact,'place_id',None) if item['fact_kind']=='vendor' else item['fact_id']
        if route_version>0 and (item['status']=='arranged' or route_target in route_ids):return None
    if len(unique)>1:
        return Understanding(unresolved=('素材篮里有多个匹配项，请选一个：'+'、'.join(i['name'] for i in unique.values())+'。',))
    if not unique:return None
    item=next(iter(unique.values()))
    if not any(name[i:i+2] in state.quote for name in _material_names(item,snapshot) for i in range(len(name)-1)):
        return None
    if item['status']=='standby':return None
    choice=MaterialChoice(name=item['name'],priority=item['priority'],status='standby',quote=state.quote)
    planning=PlanningIntent(quote=state.quote,choices=(choice,))
    return Understanding(actions=(Action(kind='modify',quote=state.quote),),planning=planning)
