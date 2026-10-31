"""Chinese date expressions shared by planning and travel-service intents."""
import re
from calendar import monthrange
from datetime import date, timedelta


_CN_DIGITS = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
              '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_WEEKDAY = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
_RELATIVE = {
    '今天': 0, '今日': 0, '今儿': 0, '今晚': 0, '今早': 0, '今夜': 0,
    '明天': 1, '明日': 1, '明儿': 1, '明晚': 1, '明早': 1,
    '后天': 2, '后儿': 2, '大后天': 3,
}
_RELATIVE_RANGES = {left[0]+right[0]: (start, end)
                    for left, start in _RELATIVE.items() for right, end in _RELATIVE.items()
                    if end-start == 1}
_RELATIVE_RANGE = rf'(?:{"|".join(map(re.escape, _RELATIVE_RANGES))})(?:(?:两|二|2)?(?:天|日))'
_TIME_SUFFIX = re.compile(r'(?:早上|上午|中午|下午|傍晚|晚上|夜里)$')

# Keep the alternatives ordered from the most specific to the least specific so
# a full date is returned instead of a month/day fragment within it.
_CN_NUMBER = r'[一二三四五六七八九十两]{1,3}'
_DATE_NUMBER = rf'(?:\d{{1,2}}|{_CN_NUMBER})'
DATE_EXPRESSION = (
    rf'(?:'
    rf'{_RELATIVE_RANGE}'
    rf'|(?:大后天|今天|今日|今儿|今晚|今早|今夜|明天|明日|明儿|明晚|明早|后天|后儿)'
    # "这星期天气" is this week's weather, not Sunday: 天 there starts 天气.
    rf'|(?:这个|下个|本|这|下)?(?:周|星期|礼拜)(?:[一二三四五六日]|天(?!气))'
    rf'|(?:这个|下个|本|这|下)?周末'
    rf'|(?<!\d)\d{{4}}(?:-|/)\d{{1,2}}(?:-|/)\d{{1,2}}(?!\d)'
    rf'|(?<!\d)\d{{4}}年{_DATE_NUMBER}月{_DATE_NUMBER}(?:日|号)(?!\d)'
    rf'|(?<!\d){_DATE_NUMBER}月{_DATE_NUMBER}(?:日|号)(?!\d)'
    rf'|(?<!\d)\d{{1,2}}[./]\d{{1,2}}(?!\d)'
    # "一日游 / 两日行程" is a duration, not the 1st or 2nd of the month.
    rf'|(?<!\d){_DATE_NUMBER}(?:号|日(?!游|行程|内|以内|左右))(?!\d)'
    rf')'
)


def find_date_expressions(text):
    """Return date expressions in source order, preserving their original text."""
    return re.findall(DATE_EXPRESSION, text)


def relative_date_range(expression, today):
    """Return endpoints for an adjacent relative-day contraction, else None."""
    if not isinstance(expression, str) or not re.fullmatch(_RELATIVE_RANGE, expression.strip()):
        return None
    start, end = _RELATIVE_RANGES[expression.strip()[:2]]
    return today + timedelta(days=start), today + timedelta(days=end)


def _number(value):
    if value.isdigit():
        return int(value)
    if not value or any(char not in _CN_DIGITS and char != '十' for char in value):
        raise ValueError('invalid Chinese number')
    if value.count('十') > 1:
        raise ValueError('invalid Chinese number')
    if '十' not in value:
        if len(value) != 1:
            raise ValueError('invalid Chinese number')
        return _CN_DIGITS[value]
    left, right = value.split('十')
    tens = _CN_DIGITS[left] if left else 1
    ones = _CN_DIGITS[right] if right else 0
    result = tens * 10 + ones
    if not 1 <= result <= 31:
        raise ValueError('invalid Chinese number')
    return result


def _make_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError('invalid date expression') from exc


def _month_day(expression, today):
    match = re.fullmatch(rf'({_DATE_NUMBER})月({_DATE_NUMBER})(?:日|号)', expression)
    if match:
        month, day = (_number(part) for part in match.groups())
        # Pick the nearest future valid occurrence. This also gives 2月29日
        # the next leap year when the current year is not a leap year.
        for year in range(today.year, today.year + 9):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if candidate >= today:
                return candidate
        raise ValueError('invalid date expression')
    match = re.fullmatch(r'(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)', expression)
    if match:
        month, day = (int(part) for part in match.groups())
        for year in range(today.year, today.year + 9):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if candidate >= today:
                return candidate
        raise ValueError('invalid date expression')
    raise ValueError('unsupported date expression')


def _day_of_month(expression, today):
    match = re.fullmatch(rf'({_DATE_NUMBER})(?:号|日)', expression)
    if not match:
        raise ValueError('unsupported date expression')
    day = _number(match[1])
    year, month = today.year, today.month
    if day > monthrange(year, month)[1]:
        # A bare day is explicitly scoped to this month (or, if already
        # passed, the following month); it must not silently roll elsewhere.
        raise ValueError('invalid date expression')
    candidate = date(year, month, day)
    if candidate < today:
        month += 1
        if month == 13:
            year, month = year + 1, 1
        if day > monthrange(year, month)[1]:
            raise ValueError('invalid date expression')
        candidate = date(year, month, day)
    return candidate


def resolve_date(expression, today):
    """Resolve one supported expression relative to ``today``.

    The function deliberately accepts only one expression. Callers that scan a
    sentence should use :func:`find_date_expressions` first and then resolve the
    selected expression explicitly.
    """
    if not isinstance(expression, str):
        raise ValueError('unsupported date expression')
    expression = _TIME_SUFFIX.sub('', expression.strip())
    if expression in _RELATIVE:
        return today + timedelta(days=_RELATIVE[expression])

    match = re.fullmatch(r'(?:这个|下个|本|这|下)?(?:周|星期|礼拜)([一二三四五六日天])', expression)
    if match:
        # Remove the matched middle and weekday to recover the optional prefix.
        suffix = match[1]
        middle = next(token for token in ('星期', '礼拜', '周') if expression.endswith(token + suffix))
        prefix = expression[:-len(middle + suffix)]
        target = _WEEKDAY[suffix]
        monday = today - timedelta(days=today.weekday())
        if prefix in ('下', '下个'):
            return monday + timedelta(days=7 + target)
        candidate = monday + timedelta(days=target)
        return candidate if candidate >= today else candidate + timedelta(days=7)

    match = re.fullmatch(r'(这个|下个|本|这|下)?周末', expression)
    if match:
        prefix = match[1] or ''
        monday = today - timedelta(days=today.weekday())
        if prefix in ('下', '下个'):
            return monday + timedelta(days=12)
        if today.weekday() >= 5:
            return today
        return monday + timedelta(days=5)

    match = re.fullmatch(r'(\d{4})([-/])(\d{1,2})\2(\d{1,2})', expression)
    if match:
        return _make_date(int(match[1]), int(match[3]), int(match[4]))
    match = re.fullmatch(rf'(\d{{4}})年({_DATE_NUMBER})月({_DATE_NUMBER})(?:日|号)', expression)
    if match:
        return _make_date(int(match[1]), _number(match[2]), _number(match[3]))
    if re.fullmatch(rf'{_DATE_NUMBER}(?:月)', expression):
        raise ValueError('unsupported date expression')
    if re.fullmatch(rf'{_DATE_NUMBER}(?:月){_DATE_NUMBER}(?:日|号)', expression):
        return _month_day(expression, today)
    if re.fullmatch(r'\d{1,2}[./]\d{1,2}', expression):
        return _month_day(expression, today)
    if re.fullmatch(rf'{_DATE_NUMBER}(?:号|日)', expression):
        return _day_of_month(expression, today)
    raise ValueError('unsupported date expression')

