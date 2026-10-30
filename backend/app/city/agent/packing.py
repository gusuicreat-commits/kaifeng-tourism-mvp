"""Grounded pre-trip packing advice derived from a confirmed itinerary."""
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo


QUESTION = re.compile(
    r'(?:要|需要|该|得|应该)?(?:带|准备|备|收拾)(?:上|好)?(?:些|点)?(?:什么|哪些|啥)'
    r'|行前准备|出行准备|准备清单|出行清单|行李清单'
)
TZ = ZoneInfo('Asia/Shanghai')
_EMPTY = '先排好并确认行程，我再按你的行程列准备清单。现在可以先告诉我日期和出发地。'
_OPEN = '按你这趟已确认的行程，出发前可以准备这些：'
_CLOSE = '出发前也可以问我“明天天气怎么样”，我按预报再提醒你。'
_WEATHER_CLOSE = '天气依据和风天气预报，会有变化，出发前可以再问我一次。'


def _dump(value):
    if value is None:
        return None
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    return value


def _get(value, key, default=None):
    value = _dump(value)
    if isinstance(value, dict):
        return value.get(key, default)
    if value is not None:
        return getattr(value, key, default)
    return default


def _dt(value):
    if isinstance(value, datetime):
        return value.astimezone(TZ)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).astimezone(TZ)
        except ValueError:
            return None
    return None


def _days(snapshot):
    """Return ``(itinerary, activities, starts_at, business_date)`` per confirmed day."""
    multi = _get(snapshot, 'multi_itinerary')
    if multi is not None:
        days = []
        for day in _get(multi, 'days', ()) or ():
            itinerary = _dump(_get(day, 'itinerary'))
            if not itinerary:
                continue
            original = _get(day, 'original_requirements') or _get(day, 'requirements') or {}
            starts = _get(original, 'starts_at')
            business_date = _get(day, 'business_date') or _get(original, 'business_date')
            days.append((itinerary, _get(itinerary, 'activities', ()) or (), starts, business_date))
        return days

    envelope = _get(snapshot, 'envelope') or {}
    official = _get(envelope, 'official_plan') or {}
    route = _get(snapshot, 'route') or {}
    itinerary = _dump(_get(route, 'itinerary'))
    if _get(official, 'status') == 'not_created' or not itinerary:
        return []
    requirements = _get(envelope, 'requirements') or {}
    return [(itinerary, _get(itinerary, 'activities', ()) or (),
             _get(requirements, 'starts_at'), _get(requirements, 'business_date'))]


def _facts(snapshot):
    values = {}
    envelope = _get(snapshot, 'envelope') or {}
    for fact in _get(envelope, 'facts', ()) or ():
        fact = _dump(fact)
        if not isinstance(fact, dict):
            continue
        name = fact.get('name')
        if name:
            for key in (fact.get('id'), fact.get('fact_id'), fact.get('place_id')):
                if key:
                    values[str(key)] = name
    planning = _get(snapshot, 'planning')
    facts = _get(planning, 'facts') if planning is not None else None
    if hasattr(facts, 'items'):
        for key, fact in facts.items():
            name = _get(fact, 'name')
            if name:
                values[str(key[1] if isinstance(key, tuple) and len(key) > 1 else key)] = name
                values[str(_get(fact, 'fact_id'))] = name
    return values


def _activity_names(snapshot):
    names = _facts(snapshot)
    official = _get(_get(snapshot, 'envelope') or {}, 'official_plan') or {}
    for activity in _get(official, 'activities', ()) or ():
        activity = _dump(activity)
        if not isinstance(activity, dict):
            continue
        name = activity.get('name')
        if name and '资料待核对' not in name:
            for key in (activity.get('goal_id'), activity.get('activity_id'), activity.get('place_id')):
                if key:
                    names[str(key)] = name
    for key, name in (_get(official, 'activity_names', {}) or {}).items():
        if name and '资料待核对' not in name:
            names[str(key)] = name
    return names


def _hours(value):
    number = round(value, 1)
    return f'{number:g}'


def _date_key(value):
    if isinstance(value, datetime):
        return value.astimezone(TZ).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def _date_text(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    return f'{parsed.month}月{parsed.day}日'


def _integer(value):
    return str(int(round(float(value))))


def _decimal(value):
    text = f'{float(value):.1f}'
    return text.rstrip('0').rstrip('.')


def _weather_items(days, forecast):
    if forecast is None:
        return [], set()
    by_date = {}
    for item in forecast or ():
        key = _date_key(_get(item, 'date'))
        if key:
            by_date[key] = item

    matched = []
    for _, _, _, business_date in days:
        key = _date_key(business_date)
        if key and key in by_date:
            matched.append((key, by_date[key]))
    if not matched:
        return [], set()

    rainy = []
    cold = []
    hot = []
    for key, item in matched:
        periods = _get(item, 'periods', ()) or ()
        probabilities = []
        amounts = []
        for period in periods:
            probability = _get(period, 'precipitation_probability_percent')
            if isinstance(probability, (int, float)) and not isinstance(probability, bool):
                probabilities.append(probability)
            amount = _get(period, 'precipitation_mm')
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                amounts.append(amount)
        max_probability = max(probabilities) if probabilities else None
        max_amount = max(amounts) if amounts else 0
        if max_probability is not None and max_probability >= 40 or max_amount >= 1:
            rainy.append((key, max_probability, max_amount))

        minimum = _get(item, 'temperature_min_c')
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and minimum <= 10:
            cold.append((key, minimum))
        maximum = _get(item, 'temperature_max_c')
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and maximum >= 30:
            hot.append((key, maximum))

    weather_items = []
    if rainy:
        rainy.sort(key=lambda value: value[0])
        date_text = '、'.join(_date_text(key) for key, _, _ in rainy)
        probabilities = [value for _, value, _ in rainy if value is not None]
        if probabilities:
            probability = max(probabilities)
            if len(rainy) == 1:
                weather_items.append(f'· 带把伞：{date_text}预报降水概率最高 {_integer(probability)}%。')
            else:
                weather_items.append(f'· 带把伞：{date_text}预报有降水，最高概率 {_integer(probability)}%。')
        else:
            amount = max(value for _, _, value in rainy)
            if len(rainy) == 1:
                weather_items.append(f'· 带把伞：{date_text}预报降水量约 {_decimal(amount)} 毫米。')
            else:
                weather_items.append(f'· 带把伞：{date_text}预报有降水，最高时段降水量约 {_decimal(amount)} 毫米。')
    if cold:
        key, minimum = min(cold, key=lambda value: value[1])
        weather_items.append(f'· 注意保暖：{_date_text(key)}最低约 {_integer(minimum)}°C。')
    if hot:
        key, maximum = max(hot, key=lambda value: value[1])
        weather_items.append(f'· 带好水和防晒：{_date_text(key)}最高约 {_integer(maximum)}°C。')
    return weather_items, {key for key, _ in matched}


def checklist(snapshot, forecast=None) -> dict:
    days = _days(snapshot)
    if not days:
        return {'status': 'answered', 'reply': _EMPTY}

    names = _activity_names(snapshot)
    walking = sum(float(_get(itinerary, 'walking_distance_m', 0) or 0) for itinerary, *_ in days)
    longest = 0.0
    latest = None
    shows = []
    modes = set()
    known_cost = 0
    cost_unknown = False
    for itinerary, activities, starts_at, business_date in days:
        returns = _dt(_get(itinerary, 'returns_at'))
        starts = _dt(starts_at)
        if starts is None:
            legs = _get(itinerary, 'legs', ()) or ()
            starts = _dt(_get(legs[0], 'departs_at')) if legs else None
        if starts and returns:
            longest = max(longest, (returns - starts).total_seconds() / 3600)
        if returns and returns.hour >= 18 and (latest is None or returns > latest):
            latest = returns
        known_cost += int(_get(itinerary, 'known_cost_upper_fen', 0) or 0)
        cost_unknown = cost_unknown or bool(_get(itinerary, 'cost_unknown', False))
        for leg in _get(itinerary, 'legs', ()) or ():
            mode = _get(_get(leg, 'journey') or {}, 'travel', {})
            mode = _get(mode, 'mode')
            if mode:
                modes.add(mode)
        for activity in activities:
            if _get(activity, 'kind') == 'show':
                starts_at = _dt(_get(activity, 'starts_at'))
                if starts_at:
                    shows.append((starts_at, names.get(str(_get(activity, 'goal_id'))) or
                                  names.get(str(_get(activity, 'activity_id'))) or
                                  names.get(str(_get(activity, 'place_id')))))

    items = []
    if walking >= 3000:
        scope = '全程' if len(days) > 1 else '这趟行程'
        items.append(f'· 穿双舒服的鞋：{scope}步行约 {walking / 1000:.1f} 公里。')
    if longest >= 8:
        items.append(f'· 带上充电宝：最长的一天在外约 {_hours(longest)} 小时，导航和拍照都耗电。')
    if latest:
        time_text = latest.strftime('%H:%M')
        if len(days) > 1:
            time_text = f'{latest.month}月{latest.day}日晚上 {time_text}'
        else:
            time_text = f'晚上 {time_text}'
        items.append(f'· 带件外套：行程会持续到{time_text}。')
    if shows:
        shows.sort(key=lambda item: item[0])
        first_time, first_name = shows[0]
        suffix = f'（{first_name}）' if first_name else ''
        items.append(f'· 提前到场：行程里有 {len(shows)} 场演出，最早一场 {first_time:%H:%M} 开始{suffix}。')
    if 'transit' in modes:
        items.append('· 准备好乘车码：行程里安排了公交地铁。')
    if 'driving' in modes:
        items.append('· 预留打车费：行程里安排了打车，打车费以高德估计为准。')
    if known_cost > 0:
        suffix = '，另有部分费用暂无价格' if cost_unknown else ''
        yuan = f'{known_cost / 100:.2f}'.rstrip('0').rstrip('.')
        items.append(f'· 准备好门票和餐饮的钱：已知费用最多约 {yuan} 元{suffix}。')
    weather_items, forecast_dates = _weather_items(days, forecast)
    items.extend(weather_items)
    if not items:
        items.append('这趟行程比较轻松，带好手机就行。')
    if forecast is None or forecast_dates:
        close = _CLOSE if forecast is None else _WEATHER_CLOSE
    else:
        dates = [_date_key(business_date) for _, _, _, business_date in days]
        dates = [value for value in dates if value]
        earliest = min(dates) if dates else None
        close = (f'{_date_text(earliest)}还没有天气预报，临近出发再问我一次，我按预报补充提醒。'
                 if earliest else _CLOSE)
    return {'status': 'answered', 'reply': _OPEN + '\n' + '\n'.join(items) + '\n' + close}
