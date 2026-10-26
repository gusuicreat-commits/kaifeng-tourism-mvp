"""Validate extracted planning fields; propose inputs to the existing workflow, never commit routes."""
import re
from datetime import date, datetime
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
        if f.field=='trip_days':
            if type(f.value) is not int or not 1<=f.value<=30:return 'invalid_trip_days'
            if '周末' in f.quote:return None if f.value==2 else 'trip_days_not_in_quote'
            numbers=[]
            for match in re.finditer(r'(\d+|[零一二两三四五六七八九十百千万]+)\s*(?:到|至|或)?\s*(\d+|[零一二两三四五六七八九十百千万]+)?\s*天',f.quote):
                values=[]
                for token in match.groups():
                    if token:
                        try:
                            if not token.isdigit() and not any(c in token for c in '十百千万') and len(token)>1:
                                values.extend(_CHINESE_DIGITS[c] for c in token)
                            else:values.append(_chinese_number(token))
                        except ValueError:continue
                numbers.extend(values)
            return None if not numbers or f.value==max(numbers) else 'trip_days_not_in_quote'
        if f.field=='budget_fen':
            amounts=_money_amounts(f.quote)
            # The model selects the amount; other numbers in its quote are not conflicting budgets.
            if f.value not in amounts:return 'budget_not_in_quote'
            if f.budget_basis is None:return 'budget_basis_missing'
            return 'per_person_budget' if f.budget_basis=='per_person' else None
    except ValueError:
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


def grounded(planning, text, snapshot, dropped=None):
    """Check evidence for every planning field.

    With ``dropped`` (a list), a field whose value is not supported by its quote is
    removed and recorded as ``{'field','check'}``, so the rest of the request still proceeds and
    only that field is asked again. Without it, any unsupported field raises.
    """
    today=snapshot.envelope.identity.generated_at.astimezone(TZ).date()
    if planning.quote not in text:raise ValueError('planning quote absent')
    request_evidence(text,planning.quote)
    if planning.operation=='continue':
        if planning.fields or planning.choices:raise ValueError('continue cannot update')
        control=planning.quote.strip().rstrip('。！!')
        if control not in ('好','好的','可以','就这样','就这样吧','就按这个安排','生成安排','开始安排','帮我安排','安排','规划') and not re.search(r'(?:开始|生成|继续|帮我|请).{0,20}(?:安排|规划)',control):
            raise ValueError('continue needs an explicit planning request')
    seen={};kept=[]
    for f in planning.fields:
        # A quote the model did not copy from the request only loses that field;
        # negation/hypothetical checks still protect the whole turn.
        if f.quote not in text:
            if dropped is None:raise ValueError('planning evidence absent')
            dropped.append({'field':f.field,'check':'quote_not_in_request'});continue
        request_evidence(text,f.quote)
        if f.field in seen and seen[f.field]!=f.value:raise ValueError('conflicting planning values')
        seen[f.field]=f.value
        check=_field_check(f,text,today)
        if check is None:
            kept.append(f)
        elif dropped is None:
            raise ValueError(check)
        else:
            dropped.append({'field':f.field,'check':check})
    kept_choices=[]
    for choice in planning.choices:
        if choice.quote not in text:
            if dropped is None:raise ValueError('material evidence absent')
            dropped.append({'field':'choice','check':'choice_not_in_quote'});continue
        if choice.name not in choice.quote and not any(
                choice.name in _fact_names(item,snapshot)
                and any(alias and len(alias)>1 and alias in choice.quote
                        for alias in _fact_names(item,snapshot))
                for item in snapshot.planning.facts.values()):
            if dropped is None:raise ValueError('material evidence absent')
            dropped.append({'field':'choice','check':'choice_not_in_quote'});continue
        request_evidence(text,choice.quote)
        kept_choices.append(choice)
    if len(kept)!=len(planning.fields) or len(kept_choices)!=len(planning.choices):
        return planning.model_copy(update={'fields':tuple(kept),'choices':tuple(kept_choices)})
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
    notes+=[f'预算按总额{f.value/100:g}元理解。' for f in p.fields if f.field=='budget_fen']
    for c in intent.constraints:
        if c.field=='food':
            if not any(c.value in (choice.name,*(a for f in _choice_facts(choice.name,snapshot) for a in _fact_names(f,snapshot))) for choice in p.choices):
                notes.append(f'想吃{c.value}：把有这道菜的店加进素材篮，就能排进行程。' if c.value else '想吃什么？告诉我菜名或店名。')
        elif c.field not in ('pace','objective','return_by'):
            notes.append(f'“{c.quote[:30]}”这次还没排进去，确认方案前请留意。')
    saved=snapshot.envelope.planning_constraints
    from .requirement_context import planning_values
    base=planning_values(snapshot)
    c=Constraints.model_validate(base);values=c.model_dump(mode='json');today=today or snapshot.envelope.identity.generated_at.astimezone(TZ).date()
    fields={f.field:f.value for f in p.fields}
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
    if p.operation=='update' and not fields and not p.choices:
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
        resolved=facts.place_by_name(snapshot.planning.facts, fields[field])
        values[key]=resolved.fact_id if resolved else None
        values[field+'_text']=None if resolved else fields[field]
    for field in ('pace','objective','budget_fen'):
        if field in fields:values[field]=fields[field]
    if 'mode' in fields:values['modes']=[fields['mode']]
    validated=Constraints.model_validate(values)
    changes=[];ambiguities=[]
    if p.choices:
        with connect() as db:items=basket.snapshot(db,session,trip_id)['items']
        used=set()
        for choice in p.choices:
            resolved=_choice_facts(choice.name,snapshot)
            # A dish (exact Food name/alias) is matched only as that dish: "灌汤包" must not
            # pick a shop whose name happens to contain it ("第一楼灌汤包总店"), or a wish to
            # eat it would become a change to that shop's priority.
            if any(isinstance(f,facts.Food) for f in resolved):
                foods={(f.kind,f.fact_id) for f in resolved if isinstance(f,facts.Food)}
                matches=[i for i in items if (i['fact_kind'],i['fact_id']) in foods]
                if not matches and _dish_served_by_basket(choice.name,snapshot):continue
            # A short name ("第一楼") may stand for one basket item's full name.
            else:matches=[i for i in items if choice.name in _material_names(i,snapshot)] or [i for i in items if any(i['fact_kind']==f.kind and i['fact_id']==f.fact_id for f in resolved)] or [i for i in items if any(choice.name in name for name in _material_names(i,snapshot))]
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
            if len(matches)!=1:
                notes.append(f'“{choice.name}”在素材篮里有好几项，是哪一个？')
                continue
            current=matches[0]
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
    if not state or state.operation!='skip' or not state.target_name:return None
    request_evidence(text,state.quote)
    query=state.target_name
    matches=[]
    for item in snapshot.materials:
        names=_material_names(item,snapshot)
        if query in names or any(len(query)>1 and query in name for name in names):matches.append(item)
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
