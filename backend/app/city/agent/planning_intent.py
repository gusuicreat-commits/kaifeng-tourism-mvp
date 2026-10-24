"""Validate extracted planning fields; propose inputs to the existing workflow, never commit routes."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from fastapi import HTTPException
from ..schema import TZ
from .models import PlanningIntent, PlanningField
from .evidence import request_evidence, effective_clock, preference_evidence


def resolve_date(expression, today):
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


def grounded(planning, text, snapshot):
    from .understand import clock
    today=snapshot.envelope.identity.generated_at.astimezone(TZ).date()
    if planning.quote not in text:raise ValueError('planning quote absent')
    request_evidence(text,planning.quote)
    if planning.operation=='continue':
        if planning.fields or planning.choices:raise ValueError('continue cannot update')
        control=planning.quote.strip().rstrip('。！!')
        if control not in ('好','好的','可以','就这样','就这样吧','就按这个安排','生成安排','开始安排','帮我安排','安排','规划') and not re.search(r'(?:开始|生成|继续|帮我|请).{0,20}(?:安排|规划)',control):
            raise ValueError('continue needs an explicit planning request')
    seen={}
    for f in planning.fields:
        if f.quote not in text:raise ValueError('planning evidence absent')
        request_evidence(text,f.quote)
        if f.field in seen and seen[f.field]!=f.value:raise ValueError('conflicting planning values')
        seen[f.field]=f.value
        if f.field=='date':
            if f.value not in f.quote:raise ValueError('date expression absent')
            resolve_date(f.value,today)
        elif f.field in ('start_time','end_time'):
            if effective_clock(text,f.quote,clock)!=f.value:raise ValueError('invented time')
        elif f.field in ('city','origin','destination') and f.value not in f.quote:raise ValueError('invented place name')
        elif f.field in ('pace','objective','mode'):preference_evidence(f.field,f.value,f.quote)
        elif f.field=='budget_fen':
            amounts=re.findall(r'(\d+(?:\.\d{1,2})?)\s*(?:元|块)',f.quote)
            if len(amounts)!=1 or int(Decimal(amounts[0])*100)!=f.value:raise ValueError('invented budget')
    for choice in planning.choices:
        if choice.quote not in text or choice.name not in choice.quote:raise ValueError('material evidence absent')
        request_evidence(text,choice.quote)
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
    if re.fullmatch(r'今天|明天|后天|\d{4}-\d{2}-\d{2}',value):fields=[PlanningField(field='date',value=value,quote=value)]
    match=re.fullmatch(r'(\d{2}:\d{2})(?:到|—|-)(\d{2}:\d{2})',value)
    if match:fields=[PlanningField(field='start_time',value=match[1],quote=match[1]),PlanningField(field='end_time',value=match[2],quote=match[2])]
    if not fields:return None
    try:return grounded(PlanningIntent(quote=value,fields=tuple(fields)),text,snapshot)
    except ValueError:return None


def proposal(session, trip_id, snapshot, intent, text):
    from ...db import connect
    from .. import basket
    from ..exploration import Constraints
    p=intent.planning
    if p.operation=='weather_replan':
        weather_replan(p.quote)
        from .. import weather
        return weather.adjustment_preview(session,trip_id,snapshot)
    if any(c.field not in ('pace','objective') for c in intent.constraints) or any(a.kind in ('search_dining','progress','position','cancel','query') for a in intent.actions):
        return {'status':'needs_clarification','reply':'这句话还包含其他活动要求，需要一起确认；本次条件尚未修改。请补充具体活动或场次。'}
    saved=snapshot.envelope.planning_constraints
    base=saved.get('constraints') or {k:v for k,v in snapshot.draft.model_dump(mode='json').items() if k in Constraints.model_fields}
    c=Constraints.model_validate(base);values=c.model_dump(mode='json');today=snapshot.envelope.identity.generated_at.astimezone(TZ).date()
    fields={f.field:f.value for f in p.fields}
    # The application's city scope is not a navigable waypoint. Require an endpoint role
    # before interpreting that city name as an origin or return location.
    for slot,markers in (('origin',('从','起点')),('destination',('回','返程','终点','结束','住'))):
        if fields.get(slot) in ('开封','开封市') and not any(marker in text for marker in markers):
            fields['city']=fields.pop(slot)
    if 'city' in fields and fields['city'] not in ('开封','开封市'):
        return {'status':'needs_data','reply':'目前支持开封旅行；这句话中的其他城市安排尚未保存。'}
    for constraint in intent.constraints:
        if constraint.field in fields and fields[constraint.field]!=constraint.value:
            return {'status':'needs_clarification','reply':'这句话里有不同的游玩条件，请确认采用哪一个；本次尚未修改。'}
        fields[constraint.field]=constraint.value
    if p.operation=='update' and not fields and not p.choices:
        return {'status':'needs_clarification','reply':'你想哪天出发？也可以先告诉我想去的地方。'}
    if 'date' in fields:values['business_date']=resolve_date(fields['date'],today).isoformat()
    day=values['business_date']
    for field,key in (('start_time','starts_at'),('end_time','ends_at')):
        time=fields.get(field) or (datetime.fromisoformat(values[key]).astimezone(TZ).strftime('%H:%M') if values[key] else None)
        if fields.get(field) and not day:
            return {'status':'needs_clarification','reply':'你打算哪天去？先确定日期，再保留这段时间。'}
        values[key]=day+'T'+time+':00+08:00' if day and time else None
    if values['starts_at'] and values['ends_at'] and values['ends_at']<=values['starts_at']:
        return {'status':'needs_clarification','reply':'结束时间需要晚于开始时间'}
    for field,key in (('origin','origin_place_id'),('destination','destination_place_id')):
        if field not in fields:continue
        matches=[r for r in snapshot.planning.facts.values() if r.kind=='place' and r.rights_status=='permitted' and r.name==fields[field]]
        values[key]=matches[0].fact_id if len(matches)==1 else None
        values[field+'_text']=None if len(matches)==1 else fields[field]
    for field in ('pace','objective','budget_fen'):
        if field in fields:values[field]=fields[field]
    if 'mode' in fields:values['modes']=[fields['mode']]
    validated=Constraints.model_validate(values)
    choice_text=[]
    if p.choices:
        # Existing answer endpoint owns consent, atomic priority writes, and arranged-item locks.
        if p.fields or intent.constraints:return {'status':'needs_clarification','reply':'请先确认素材取舍，再调整日期或其他条件；本次尚未修改。'}
        with connect() as db:items=basket.snapshot(db,session,trip_id)['items']
        used=set()
        for choice in p.choices:
            matches=[i for i in items if i['name']==choice.name]
            if len(matches)!=1 or choice.name in used:return {'status':'needs_clarification','reply':'请明确要调整的素材名称；本次尚未修改。'}
            from ..exploration import reply_patch
            try:
                _,checked=reply_patch(choice.quote,items,c.model_dump(mode='json'))
            except (HTTPException,ValueError):
                return {'status':'needs_clarification','reply':'需要你明确确认这项素材的取舍；本次尚未修改。'}
            if checked.get(matches[0]['key'])!={'priority':choice.priority,'status':choice.status}:
                return {'status':'needs_clarification','reply':'这项素材的取舍还需要确认；本次尚未修改。'}
            used.add(choice.name)
            suffix='先放候补' if choice.status=='standby' else {'must':'必去','want':'改为想去','optional':'顺路看看'}[choice.priority]
            choice_text.append(choice.name+suffix)
    return {'status':'planning_input','reply':'已理解你的规划要求，接着核验安排。',
            'planning_action':{'trip_id':str(snapshot.envelope.identity.trip_id),'operation':p.operation,'constraints':validated.model_dump(mode='json'),
                'planning_version':saved.get('version',0),'basket_version':snapshot.envelope.basket['version'],
                'trip_version':snapshot.row['version'],'date_changed':'date' in fields,'choice_text':'，'.join(choice_text) or None}}
