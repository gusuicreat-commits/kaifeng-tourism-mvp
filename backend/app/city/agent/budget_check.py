"""Per-person budget: party size from the visitor's own words, and a check of a preview's known costs.

The solver's budget is a hard total that fails on unknown costs. A per-person budget is
instead compared after preview with the plan's known per-visitor costs
(``known_cost_upper_fen``: transport legs plus entry boundaries and priced stops), and
the reply names what that sum leaves out.
"""
import re
from datetime import datetime

NUMBER=r'(\d{1,2}|[一二两三四五六七八九十]{1,3})'
# A count followed by a person unit. Room and portion units ("两人间", "三人份") are not travellers.
_MEMBERS=re.compile(NUMBER+r'\s*(?:个|位|名)?\s*(大人|成人|小孩|孩子|儿童|老人)')
_PEOPLE=re.compile(NUMBER+r'\s*(个\s*人|人|位|口)(?!\s*[间房份床])')
# "我们三个" / "一家三口": the group pronoun carries the person sense of a bare classifier.
_GROUP=re.compile(r'(?:我们|咱们|咱|一行|一家|全家)\s*'+NUMBER+r'\s*(?:个|口)(?!\s*(?:景点|地方|景区|小时|钟头|项目|店|天|晚|点|人|位))')
_PAIR=re.compile(r'(?:我们|咱们|咱|你们|他们|她们)\s*(俩|仨)')
_DAILY=re.compile(r'每\s*(?:一\s*)?天|每日')


def _count(token):
    from .planning_intent import _chinese_number
    try:return _chinese_number(token)
    except ValueError:return None


def party_size(text):
    """Travellers the visitor counted out loud, or None when no number was said or counts disagree.

    One is never a party size: "一人800" and "一个人不超过200" state the per-person basis,
    and a party of one needs no conversion.
    """
    members=[_count(m[1]) for m in _MEMBERS.finditer(text)]
    generic={_count(m[1]) for m in (*_PEOPLE.finditer(text),*_GROUP.finditer(text))}
    generic|={{'俩':2,'仨':3}[m[1]] for m in _PAIR.finditer(text)}
    generic-={None,1}
    if None in members:return None
    total=sum(members) if members else None
    if len(generic)>1:return None
    if generic:
        value=next(iter(generic))
        return value if total is None or total==value else None
    return total if total and total<=50 else None


def daily(quote,text=''):
    """Per-day only with "每天/每日" in the quote or in the comma clause holding it.

    The clause stops at any comma, so "两天都去万岁山，每人800" stays a whole-trip budget.
    """
    if _DAILY.search(quote):return True
    at=text.find(quote)
    if at<0:return False
    bounds=r'[，,。！!？?；;\n]'
    start=max((m.end() for m in re.finditer(bounds,text[:at])),default=0)
    end=next((at+len(quote)+m.start() for m in re.finditer(bounds,text[at+len(quote):])),len(text))
    return bool(_DAILY.search(text[start:end]))


def _yuan(fen):
    value=fen/100
    return f'{value:.0f}' if abs(value-round(value))<.005 else f'{value:.1f}'


def recorded_note(values):
    """The budget understood this turn, before any plan exists."""
    budget=values.get('per_person_budget_fen')
    if budget is None:return ''
    party=values.get('party_size')
    scope='每天' if values.get('per_person_budget_daily') else ''
    note=f'预算按每人{scope}{_yuan(budget)}元记下'
    if party:note+=f'（{party}人合计{scope}{_yuan(budget*party)}元，按每人×{party}人换算）'
    return note+'，方案出来后按已知费用核对。'


def _unknown_items(days,catalog,costs_known):
    items=[]
    taxi=[];tolls=[]
    def add(label):
        if label not in items:items.append(label)
    for day in days:
        for activity in day['activities']:
            if activity['kind']=='dining':add('餐饮');continue
            if not costs_known(activity['place_id'],day):
                # Only a scenic area sells admission; a market or stage cost is just "费用".
                place=catalog.get(('place',activity['place_id']))
                add((getattr(place,'name',None) or '部分地点')+('门票' if getattr(place,'place_type',None)=='scenic' else '费用'))
        for leg in day['legs']:
            journey=leg['journey']
            driving=journey['travel']['mode']=='driving'
            if journey.get('cost_upper_fen') is None:
                if driving and journey.get('vehicle_cost_fen') is not None:taxi.append(journey['vehicle_cost_fen'])
                else:add({'driving':'打车或驾车费用','transit':'公共交通费用'}.get(journey['travel']['mode'],'部分交通费用'))
            if driving and journey.get('tolls_fen',0):tolls.append(journey['tolls_fen'])
            if any(b.get('cost_upper_fen') is None for b in journey.get('boundaries',())):add('部分入园费用')
    if sum(taxi):add(f'打车约{_yuan(sum(taxi))}元/车（高德估计）')
    if tolls:add(f'过路费约{_yuan(sum(tolls))}元')
    return items


def check(values,itinerary,*,days,overnight=False,ctx=None):
    """One sentence comparing the known per-person cost with the per-person budget, or ''.

    ``days`` are ``{'activities','legs','starts_at','ends_at'}`` for each planned day.
    """
    budget=values.get('per_person_budget_fen')
    if budget is None or not itinerary:return ''
    if ctx is None:
        from ...db import connect
        from ..engine import service as engine
        with connect() as db:ctx=engine.context(db)
    def costs_known(place_id,day):
        moments=[datetime.fromisoformat(day[k]) for k in ('starts_at','ends_at') if day.get(k)]
        return len([c for c in ctx.operations.costs if c.place_id==place_id and ctx.evidence_ready(c.evidence,*moments)])==1
    unknown=_unknown_items(days,ctx.facts,costs_known)
    # The engine marked some cost unknown that the per-item scan cannot name.
    if itinerary.get('cost_unknown') and not unknown and not overnight:unknown.append('部分费用')
    if overnight:unknown.append('住宿')
    known=itinerary['known_cost_upper_fen']
    count=len(days)
    limit=budget*count if values.get('per_person_budget_daily') else budget
    basis=f'每人每天{_yuan(budget)}元×{count}天，' if values.get('per_person_budget_daily') and count>1 else ''
    missing=f"（未计入：{'、'.join(unknown)}）" if unknown else ''
    # Meals and stays are never in the sum. A zero sum with unpriced tickets or transport
    # would read as "free", so say the check cannot be made.
    if known==0 and any(u not in ('餐饮','住宿') for u in unknown):
        return f'按每人{_yuan(budget)}元预算核对：已知费用不足以核对预算{missing}。'
    verdict=f'在预算内（{basis}每人{_yuan(limit)}元）' if known<=limit else f'超出约{_yuan(known-limit)}元（{basis}预算每人{_yuan(limit)}元），方案仍可确认，也可以减少收费项目'
    party=values.get('party_size')
    total=f'{party}人合计已知费用约{_yuan(known*party)}元，预算合计{_yuan(limit*party)}元（按每人×{party}人换算）。' if party else ''
    return f'按每人预算核对：已知费用每人约{_yuan(known)}元，{verdict}{missing}。'+total
