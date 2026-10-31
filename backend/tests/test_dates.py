from datetime import date, timedelta
import re

import pytest

from app.city.agent.dates import DATE_EXPRESSION, find_date_expressions, resolve_date, relative_date_range, _RELATIVE


PREFIXES = ('', '这', '本', '这个', '下', '下个')
RELATIVE_HEADS = {word[0]: offset for word, offset in _RELATIVE.items()}
RELATIVE_PAIRS = [(left+right, start, end) for left, start in RELATIVE_HEADS.items()
                  for right, end in RELATIVE_HEADS.items() if end-start == 1]


@pytest.mark.parametrize('expression,start,end', RELATIVE_PAIRS)
@pytest.mark.parametrize('suffix', ('天', '日', '两天', '两日', '二天', '二日', '2天', '2日'))
@pytest.mark.parametrize('today', (date(2026,10,26), date(2026,10,31), date(2026,12,31), date(2028,2,28)))
def test_adjacent_relative_contractions_are_ranges(today, suffix, expression, start, end):
    expression+=suffix
    assert relative_date_range(expression,today)==(today+timedelta(days=start),today+timedelta(days=end))
    assert find_date_expressions('计划'+expression+'去开封')==[expression]
    assert re.findall(DATE_EXPRESSION,'计划'+expression+'去开封')==[expression]
    with pytest.raises(ValueError):resolve_date(expression,today)


@pytest.mark.parametrize('expression', [pair[0] for pair in RELATIVE_PAIRS])
def test_bare_adjacent_contractions_are_not_ranges(expression):
    assert relative_date_range(expression,date(2026,10,26)) is None
    assert find_date_expressions(expression)==[]
    assert find_date_expressions('计划'+expression+'去开封')==[]


@pytest.mark.parametrize('text,expected', [
    ('逛完之后大概六点回', []),
    ('明天之后大后天也行', ['明天', '大后天']),
])
def test_contraction_head_needs_a_day_suffix(text, expected):
    assert find_date_expressions(text)==expected
    assert re.findall(DATE_EXPRESSION,text)==expected


RELATIVE_CONTEXTS = ('{w}', '我{w}去开封', '计划{w}', '我们{w}下午出发', '定在{w}', '之后{w}', '就{w}吧')


@pytest.mark.parametrize('word', list(_RELATIVE))
@pytest.mark.parametrize('template', RELATIVE_CONTEXTS)
def test_single_relative_words_match_main_in_sentence_positions(word, template):
    text=template.format(w=word)
    assert find_date_expressions(text)==[word]
    assert re.findall(DATE_EXPRESSION,text)==[word]


@pytest.mark.parametrize('expression', [left+right for left, start in RELATIVE_HEADS.items()
    for right, end in RELATIVE_HEADS.items() if end-start != 1]+['明昨','昨今','前后'])
@pytest.mark.parametrize('suffix', ('', '两天'))
def test_nonadjacent_or_unknown_contractions_are_not_ranges(expression,suffix):
    expression+=suffix
    assert relative_date_range(expression,date(2026,10,26)) is None
    assert find_date_expressions(expression)==[]


def _weekday_expected(today, target, prefix):
    monday = today - timedelta(days=today.weekday())
    target_date = monday + timedelta(days=target)
    if prefix in ('下', '下个'):
        return monday + timedelta(days=7 + target)
    return target_date if target_date >= today else target_date + timedelta(days=7)


@pytest.mark.parametrize('weekday,target', list(enumerate(('一', '二', '三', '四', '五', '六', '日'))))
@pytest.mark.parametrize('prefix', PREFIXES)
@pytest.mark.parametrize('middle', ('周', '星期', '礼拜'))
@pytest.mark.parametrize('today', [date(2026, 10, 26) + timedelta(days=i) for i in range(7)])
def test_every_weekday_prefix_is_relative_to_the_current_week(today, middle, prefix, weekday, target):
    expression = f'{prefix}{middle}{target}'
    assert resolve_date(expression, today) == _weekday_expected(today, weekday, prefix)
    assert find_date_expressions(f'我计划{expression}下午去') == [expression]


@pytest.mark.parametrize('today', [date(2026, 10, 26) + timedelta(days=i) for i in range(7)])
@pytest.mark.parametrize('prefix', PREFIXES)
def test_every_weekend_prefix_resolves_to_the_expected_saturday(today, prefix):
    expression = f'{prefix}周末'
    if prefix in ('下', '下个'):
        expected = today - timedelta(days=today.weekday()) + timedelta(days=12)
    elif today.weekday() >= 5:
        expected = today
    else:
        expected = today - timedelta(days=today.weekday()) + timedelta(days=5)
    assert resolve_date(expression, today) == expected


@pytest.mark.parametrize('expression,expected', [
    ('今天', 0), ('今日', 0), ('今儿', 0), ('今晚', 0), ('今早', 0), ('今夜', 0),
    ('明天', 1), ('明日', 1), ('明儿', 1), ('明晚', 1), ('明早', 1),
    ('后天', 2), ('后儿', 2), ('大后天', 3),
])
def test_relative_days(expression, expected):
    today = date(2026, 10, 26)
    assert resolve_date(expression, today) == today + timedelta(days=expected)


@pytest.mark.parametrize('expression,expected', [
    ('2026-10-31', date(2026, 10, 31)),
    ('2026/10/31', date(2026, 10, 31)),
    ('2026年10月31日', date(2026, 10, 31)),
    ('2026年十月三十一号', date(2026, 10, 31)),
    ('十月三十一日', date(2026, 10, 31)),
    ('10月31号', date(2026, 10, 31)),
    ('10.31', date(2026, 10, 31)),
    ('10/31', date(2026, 10, 31)),
    ('三十一号', date(2026, 10, 31)),
])
def test_calendar_forms(expression, expected):
    assert resolve_date(expression, date(2026, 10, 26)) == expected


def test_yearless_month_day_uses_the_next_year_after_passing():
    assert resolve_date('10月1日', date(2026, 10, 2)) == date(2027, 10, 1)
    assert resolve_date('12月31日', date(2026, 12, 31)) == date(2026, 12, 31)


def test_day_only_rolls_to_next_month_and_rejects_missing_day():
    assert resolve_date('31号', date(2026, 3, 31)) == date(2026, 3, 31)
    assert resolve_date('30号', date(2026, 3, 31)) == date(2026, 4, 30)
    with pytest.raises(ValueError):
        resolve_date('31号', date(2026, 4, 10))


def test_february_29_finds_the_nearest_valid_future_leap_year():
    assert resolve_date('2月29日', date(2024, 2, 1)) == date(2024, 2, 29)
    assert resolve_date('2月29日', date(2025, 1, 1)) == date(2028, 2, 29)


@pytest.mark.parametrize('text,expected', [
    ('我打算礼拜六下午去', ['礼拜六']),
    ('安排下个周五和10月31号', ['下个周五', '10月31号']),
    ('今天气温怎么样', ['今天']),
    ('今天天气不错', ['今天']),
    ('明天天黑前回来', ['明天']),
    ('周六天黑前到', ['周六']),
    ('星期天天气好就去', ['星期天']),
    ('这星期天气预报', []),
])
def test_find_date_expression_in_sentence(text, expected):
    assert find_date_expressions(text) == expected
    assert re.findall(DATE_EXPRESSION, text) == expected


@pytest.mark.parametrize('expression', ['礼拜七', '星期七', '2026-02-30', '13月1日', '31号'])
def test_unsupported_or_invalid_dates_raise(expression):
    with pytest.raises(ValueError):
        resolve_date(expression, date(2026, 4, 10))



@pytest.mark.parametrize('text,found', [
    ('帮我排一个开封一日游',[]),('两日游从开封府出发',[]),('三日行程',[]),('五日内回来',[]),
    ('11月1日去',['11月1日']),('1日去清明上河园',['1日']),('一号去',['一号']),
])
def test_day_count_words_are_not_calendar_dates(text,found):
    assert re.findall(DATE_EXPRESSION,text)==found
