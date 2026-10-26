"""Validate extracted planning fields; propose inputs to the existing workflow, never commit routes."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from fastapi import HTTPException
from .. import facts
from ..schema import TZ
from .models import PlanningIntent, PlanningField
from .evidence import request_evidence, effective_clock, preference_evidence


_RELATIVE_DAY={'今晚':'今天','今早':'今天','今夜':'今天','今日':'今天','明晚':'明天','明早':'明天'}
DATE_EXPRESSION=r'(?:今天|明天|后天|今晚|今早|今夜|今日|明晚|明早|(?:本|这|下)周[一二三四五六日天]|\d{4}[-年]\d{1,2}[-月]\d{1,2}日?|\d{1,2}月\d{1,2}日?)'


def resolve_date(expression, today):
    expression=re.sub(r'(?:早上|上午|中午|下午|傍晚|晚上|夜里)$','',expression.strip())
    expression=_RELATIVE_DAY.get(expression,expression)
    if expression in ('今天','明天','后天'):
        return today+timedelta(days=('今天','明天','后天').index(expression))
    match=re.fullmatch(r'(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?',expression)
    if match:return date(*(int(x) for x in match.groups()))
    match=re.fullmatch(r'(\d{1,2})月(\d{1,2})日?',expression)
    if match:return date(today.year,int(match[1]),int(match[2]))
    match=re.fullmatch(r'(本|这|下)周([一二三四五六日天])',expression)
    if match:
        weekday='一二三四五六日'.index('日' if match[2]=='天' else match[2])
        return today+timedelta(days=weekday-today.weekday()+(7 if match[1]=='下' else 0))
    raise ValueError('unsupported date expression')


def _field_check(f, text, today):
    """Name of the check this field's value fails against its own quote, or None."""
    from .understand import clock_grounded
    try:
        if f.field=='date':
            if f.value in f.quote:
                resolve_date(f.value,today);return None
            # The model may normalize a relative date to ISO; the quoted expression must resolve to it.
            expressions=re.findall(DATE_EXPRESSION,f.quote)
            return None if expressions and resolve_date(expressions[-1],today).isoformat()==str(f.value) else 'date_not_in_quote'
        if f.field in ('start_time','end_time'):return None if clock_grounded(f.value,f.quote,text) else 'clock_not_in_quote'
        if f.field in ('city','origin','destination'):return None if f.value in f.quote else 'place_not_in_quote'
        if f.field in ('pace','objective','mode'):
            preference_evidence(f.field,f.value,f.quote);return None
        if f.field=='budget_fen':
            amounts=re.findall(r'(\d+(?:\.\d{1,2})?)\s*(?:元|块)',f.quote)
            return None if len(amounts)==1 and int(Decimal(amounts[0])*100)==f.value else 'budget_not_in_quote'
    except ValueError:
        return 'date_unresolved' if f.field=='date' else 'preference_not_in_quote'
    return None


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
    for choice in planning.choices:
        if choice.quote not in text:raise ValueError('material evidence absent')
        if choice.name not in choice.quote and not any(
                getattr(item,'name',None)==choice.name
                and any(alias and len(alias)>1 and alias in choice.quote
                        for alias in getattr(item,'aliases',()))
                for item in snapshot.planning.facts.values()):
            raise ValueError('material evidence absent')
        request_evidence(text,choice.quote)
    return planning if len(kept)==len(planning.fields) else planning.model_copy(update={'fields':tuple(kept)})


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
    if re.fullmatch(r'今天|明天|后天|\d{4}-\d{2}-\d{2}',value):fields=[PlanningField(field='date',value=value,quote=value)]
    match=re.fullmatch(r'(\d{2}:\d{2})(?:到|—|-)(\d{2}:\d{2})',value)
    if match:fields=[PlanningField(field='start_time',value=match[1],quote=match[1]),PlanningField(field='end_time',value=match[2],quote=match[2])]
    if not fields:return None
    try:return grounded(PlanningIntent(quote=value,fields=tuple(fields)),text,snapshot)
    except ValueError:return None


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
    notes=[]
    for c in intent.constraints:
        if c.field=='food':
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
    choice_text=[]
    if p.choices:
        with connect() as db:items=basket.snapshot(db,session,trip_id)['items']
        used=set()
        for choice in p.choices:
            matches=[i for i in items if i['name']==choice.name]
            if len(matches)!=1 or choice.name in used:return {'status':'needs_clarification','reply':'素材篮里没找到这一项，是哪个地点？'}
            used.add(choice.name)
            current=matches[0]
            # A choice that already matches the basket is a harmless echo of the
            # existing selection, not a material change. It can accompany date/
            # time fields in a single-utterance plan.
            if current['priority']==choice.priority and current['status']==choice.status:
                continue
            # A real material change cannot be combined with date/time in one
            # shot; the existing answer endpoint owns consent and atomic writes.
            if p.fields or any(c.field in ('pace','objective','return_by') for c in intent.constraints):
                return {'status':'needs_clarification','reply':'先确认素材的取舍，再改日期和时间吧。'}
            # The quote itself must authorize the stated priority; a "必去" text
            # cannot be silently downgraded to "想去" by the model.
            from ..exploration import reply_patch
            try:
                _,checked=reply_patch(choice.quote,items,c.model_dump(mode='json'))
            except (HTTPException,ValueError):
                return {'status':'needs_clarification','reply':'这一项是必去、想去，还是先放候补？'}
            if checked.get(matches[0]['key'])!={'priority':choice.priority,'status':choice.status}:
                return {'status':'needs_clarification','reply':'这一项是必去、想去，还是先放候补？'}
            suffix='先放候补' if choice.status=='standby' else {'must':'必去','want':'改为想去','optional':'顺路看看'}[choice.priority]
            choice_text.append(choice.name+suffix)
    return {'status':'planning_input','reply':'好的，正在安排。','notes':notes,
            'planning_action':{'trip_id':str(snapshot.envelope.identity.trip_id),'operation':p.operation,'constraints':validated.model_dump(mode='json'),
                'planning_version':saved.get('version',0),'basket_version':snapshot.envelope.basket['version'],
                'trip_version':snapshot.row['version'],'date_changed':'date' in fields,'choice_text':'，'.join(choice_text) or None}}
