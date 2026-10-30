"""Date/count evidence for trip ranges. Single-date and hotel resolution stay unchanged."""
import re
from datetime import date, timedelta
from .dates import DATE_EXPRESSION, resolve_date, _number


RANGE_DATE = re.compile(r'(?<!\d)\d{4}[-/]\d{1,2}[-/]\d{1,2}(?!\d)|(?<!\d)(?:\d{4}年)?[\d一二三四五六七八九十两]+月[\d一二三四五六七八九十两]+(?:日|号)?|(?<![\d-])\d{1,2}[-./]\d{1,2}(?![\d-])|'+DATE_EXPRESSION)
DAY_NUMBER = re.compile(r'第\s*(\d+|[零一二两三四五六七八九十]+)\s*天|第一天|最后一天|首日|末日')
EVERY_DAY = re.compile(r'每\s*(?:一\s*)?天|每日|天天|各天|(?:\d+|[零一二两三四五六七八九十百]+|这几|那几|所有|全部|整个)\s*天\s*(?:全\s*)?都')
SENTENCE = re.compile(r'[^。！？!?；;\n]+')


def expressions(quote):
    return [m.group() for m in RANGE_DATE.finditer(quote)]


def quoted_day_ref(ref,quote,text):
    if ref in text:return ref
    found={m.group() for m in DAY_NUMBER.finditer(quote)}|set(expressions(quote))
    found={value for value in found if not any(value!=other and value in other for other in found)}
    if len(found)==1:
        value=next(iter(found))
        if value in text:return value
    return None


def explicit_year(expression):
    return bool(re.match(r'\d{4}(?:年|[-/])',expression))


def resolve(expression,today):
    value=expression.strip()
    match=re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})',value)
    if match:return date(*map(int,match.groups()))
    match=re.fullmatch(r'(?:(\d{4})年)?([\d一二三四五六七八九十两]+)月([\d一二三四五六七八九十两]+)(?:日|号)?',value)
    if match:return date(int(match[1]) if match[1] else today.year,_number(match[2]),_number(match[3]))
    match=re.fullmatch(r'(\d{1,2})[-./](\d{1,2})',value)
    if match:return date(today.year,*map(int,match.groups()))
    return resolve_date(value,today)


def field_date(field,today):
    found=expressions(field.quote)
    if not found:raise ValueError('date_not_in_quote')
    # Start/end roles are positional within one continuous date-range quote.
    source=found[0] if field.field in ('date','start_date') else found[-1]
    if len(found)>1 and field.field=='end_date' and not all(explicit_year(e) for e in (found[0],found[-1])):
        if resolve(found[-1],today)<resolve(found[0],today):raise ValueError('cross_year_requires_year')
    expected=resolve(source,today)
    if resolve(str(field.value),today)!=expected:raise ValueError('date_not_in_quote')
    return expected


def counts(quote,unit):
    from .planning_intent import _chinese_number
    values=[]
    for m in re.finditer(r'(\d+|[零一二两三四五六七八九十百千万]+)\s*(?:到|至|或)?\s*(\d+|[零一二两三四五六七八九十百千万]+)?\s*'+unit,quote):
        for token in filter(None,m.groups()):
            try:
                values.extend(_chinese_number(c) for c in token) if not token.isdigit() and len(token)>1 and not any(c in token for c in '十百千万') else values.append(_chinese_number(token))
            except ValueError:continue
    return values


def day_reference(ref,text,today):
    from .evidence import request_evidence
    request_evidence(text,ref)
    matches=list(DAY_NUMBER.finditer(ref))
    if matches:
        m=matches[-1];source=m.group()
        value='last' if source in ('最后一天','末日') else 'day:'+str(_number(m[1]) if m[1] else 1)
        normalized=DAY_NUMBER.fullmatch(ref.strip())
        if not normalized:raise ValueError('day_ref_not_in_quote')
        return value
    found=expressions(ref)
    if len(found)!=1:raise ValueError('day_ref_not_in_quote')
    return resolve(found[0],today).isoformat()


def daily_evidence(quote,text,field=None):
    # Repeated short quotes must agree on scope; a match in another sentence is not evidence.
    anchor=quote.strip('。！？!?；;\n')
    parts=[m.group() for m in SENTENCE.finditer(anchor)]
    if len(parts)>1:
        if field is None:return False
        from .understand import clock_grounded
        relevant=[p for p in parts if clock_grounded(field.value,p,text)] if field.field in ('start_time','end_time') else [
            p for p in parts if field.value is not None and str(field.value) in p]
        return bool(relevant) and all(daily_evidence(p,text) for p in relevant)
    sentences=[m.group() for m in SENTENCE.finditer(text) if anchor and anchor in m.group()]
    return bool(sentences) and all(EVERY_DAY.search(s) for s in sentences)


def multiple_days(base,fields,today):
    values={k:base.get(k) for k in ('start_date','end_date','trip_days')}
    for f in fields:
        key='start_date' if f.field=='date' else f.field
        if key not in values:continue
        value=resolve(str(f.value),today).isoformat() if f.value and key.endswith('_date') else f.value
        values[key]=value
    if any(f.field=='trip_days' for f in fields) and not any(f.field=='end_date' for f in fields):
        return bool(values['trip_days'] and values['trip_days']>1)
    if not values['start_date'] or not values['end_date']:return bool(values['trip_days'] and values['trip_days']>1)
    return bool(values['start_date'] and values['end_date'] and values['end_date']>values['start_date'])


def scope_check(field,text):
    from .evidence import request_evidence
    if field.day_ref and field.scope:raise ValueError('ambiguous_day_scope')
    if field.scope:
        request_evidence(text,field.quote)
        if not daily_evidence(field.quote,text,field):raise ValueError('scope_not_in_quote')


def day_key(field,today):
    if field.day_ref:
        return ('day',day_reference(field.day_ref,field.day_ref,today))
    return ('scope',field.scope)


def date_for_ref(ref,start,end):
    if ref=='last':return end
    if ref.startswith('day:'):return start+timedelta(days=int(ref[4:])-1)
    return date.fromisoformat(ref)
