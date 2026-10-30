from types import SimpleNamespace

import pytest

from app.city.agent import packing
from test_city_assistant import city, planned
from test_city_agent import run, snapshot as agent_snapshot
from app.city.agent import understand
from app.city.agent.models import Understanding, Conversation
from app.city import weather


def activity(kind, starts, goal='show', place='stage'):
    return {'kind': kind, 'starts_at': starts, 'goal_id': goal, 'place_id': place, 'activity_id': goal + '-activity'}


def single_snapshot(*, walking=4200, returns='2030-05-01T20:30:00+08:00',
                    starts='2030-05-01T09:00:00+08:00', activities=None,
                    mode='walking', cost=12000, unknown=False):
    itinerary = {
        'walking_distance_m': walking,
        'returns_at': returns,
        'known_cost_upper_fen': cost,
        'cost_unknown': unknown,
        'activities': activities if activities is not None else [activity('show', '2030-05-01T19:00:00+08:00')],
        'legs': [{'journey': {'travel': {'mode': mode}}}],
    }
    envelope = {
        'official_plan': {'status': 'current', 'activities': [{'goal_id': 'show', 'name': '晚间演出'}]},
        'requirements': {'starts_at': starts, 'business_date': '2030-05-01'},
        'facts': [],
    }
    return SimpleNamespace(envelope=envelope, route={'itinerary': itinerary}, multi_itinerary=None, planning=None)


def forecast_day(date='2030-05-01', minimum=18, maximum=25, probability=None, amount=0):
    return {'date': date, 'temperature_min_c': minimum, 'temperature_max_c': maximum,
            'periods': [{'period': 'daytime', 'precipitation_probability_percent': probability,
                         'precipitation_mm': amount}]}


def multi_snapshot(dates):
    return SimpleNamespace(multi_itinerary={'days': [
        {'business_date': value, 'original_requirements': {'starts_at': f'{value}T10:00:00+08:00'},
         'itinerary': {'walking_distance_m': 0, 'returns_at': f'{value}T17:00:00+08:00',
                       'known_cost_upper_fen': 0, 'cost_unknown': False, 'activities': [], 'legs': []}}
        for value in dates]}, envelope={}, route={}, planning=None)


def test_single_checklist_uses_confirmed_itinerary_evidence():
    result = packing.checklist(single_snapshot())
    reply = result['reply']
    assert reply.startswith('按你这趟已确认的行程')
    assert '约 4.2 公里' in reply
    assert '晚上 20:30' in reply
    assert '19:00' in reply and '晚间演出' in reply
    assert '约 120 元' in reply
    assert '模型' not in reply


def test_multi_day_checklist_sums_walking_and_uses_latest_date():
    def day(date, distance, end):
        return {'business_date': date, 'original_requirements': {'starts_at': f'{date}T09:00:00+08:00'},
                'itinerary': {'walking_distance_m': distance, 'returns_at': f'{date}T{end}:00+08:00',
                              'known_cost_upper_fen': 0, 'cost_unknown': False, 'activities': [], 'legs': []}}
    snapshot = SimpleNamespace(multi_itinerary={'days': [day('2030-05-01', 2000, '17:00'),
                                                          day('2030-05-02', 1800, '20:30')]},
                               envelope={}, route={}, planning=None)
    reply = packing.checklist(snapshot)['reply']
    assert '全程步行约 3.8 公里' in reply
    assert '5月2日晚上 20:30' in reply


def test_checklist_without_confirmed_plan_is_explicit():
    snapshot = SimpleNamespace(envelope={'official_plan': {'status': 'not_created'}}, route={'itinerary': None},
                               multi_itinerary=None, planning=None)
    assert packing.checklist(snapshot)['reply'].startswith('先排好并确认行程')


def test_checklist_weather_precipitation_temperature_and_close():
    reply = packing.checklist(single_snapshot(), [forecast_day(minimum=8, maximum=33, probability=60)])['reply']
    assert '带把伞：5月1日预报降水概率最高 60%' in reply
    assert '注意保暖：5月1日最低约 8°C' in reply
    assert '带好水和防晒：5月1日最高约 33°C' in reply
    assert reply.endswith('天气依据和风天气预报，会有变化，出发前可以再问我一次。')
    assert reply.index('· 准备好门票和餐饮的钱') < reply.index('· 带把伞')


def test_checklist_weather_formats_fractional_values():
    reply = packing.checklist(single_snapshot(), [forecast_day(minimum=7.72, maximum=30.4,
                                                               probability=None, amount=1.25)])['reply']
    assert '最低约 8°C' in reply and '7.72' not in reply
    assert '最高约 30°C' in reply
    assert '预报降水量约 1.2 毫米' in reply

    mild = packing.checklist(single_snapshot(), [forecast_day(minimum=18, maximum=29.6)])['reply']
    assert '带好水和防晒' not in mild


@pytest.mark.parametrize('text', ['出门要备点啥', '行李该收拾些什么', '需要带上什么', '准备好哪些东西'])
def test_packing_question_matches_preparation_synonyms(text):
    assert packing.QUESTION.search(text)


@pytest.mark.parametrize('text', ['明天准备去清明上河园', '我带了充电宝', '收拾完就出发', '准备出发了'])
def test_packing_question_rejects_non_questions(text):
    assert not packing.QUESTION.search(text)


@pytest.mark.parametrize('probability,amount,expected', [
    (40, 0, '预报降水概率最高 40%'), (39, 0.8, None),
    (20, 1, '预报降水概率最高 20%'), (None, 1.5, '预报降水量约 1.5 毫米'),
])
def test_checklist_precipitation_thresholds_use_each_period(probability, amount, expected):
    forecast = forecast_day(probability=probability, amount=amount)
    forecast['periods'].append({'period': 'nighttime', 'precipitation_probability_percent': None,
                                'precipitation_mm': 0.8})
    reply = packing.checklist(single_snapshot(), [forecast])['reply']
    assert ('· 带把伞' in reply) == (expected is not None)
    if expected:
        assert expected in reply


def test_checklist_uses_nighttime_rain_and_temperature_boundaries():
    forecast = forecast_day(minimum=10, maximum=30, probability=0)
    forecast['periods'].append({'period': 'nighttime', 'precipitation_probability_percent': 70,
                                'precipitation_mm': 0})
    reply = packing.checklist(single_snapshot(), [forecast])['reply']
    assert '预报降水概率最高 70%' in reply
    assert '最低约 10°C' in reply and '最高约 30°C' in reply
    mild = packing.checklist(single_snapshot(), [forecast_day(minimum=10.1, maximum=29.9)])['reply']
    assert '注意保暖' not in mild and '带好水和防晒' not in mild


def test_checklist_weather_clear_still_uses_weather_close():
    reply = packing.checklist(single_snapshot(), [forecast_day()])['reply']
    assert '带把伞' not in reply and '注意保暖' not in reply and '带好水和防晒' not in reply
    assert reply.endswith('天气依据和风天气预报，会有变化，出发前可以再问我一次。')


def test_checklist_weather_controls_light_trip_fallback():
    snapshot = single_snapshot(walking=0, starts='2030-05-01T10:00:00+08:00',
                               returns='2030-05-01T17:00:00+08:00', activities=[], cost=0)
    clear = packing.checklist(snapshot, [forecast_day()])['reply']
    rainy = packing.checklist(snapshot, [forecast_day(probability=60)])['reply']
    assert '这趟行程比较轻松，带好手机就行。' in clear
    assert '· 带把伞：5月1日预报降水概率最高 60%' in rainy
    assert '这趟行程比较轻松，带好手机就行。' not in rainy


def test_checklist_weather_out_of_range_and_amount_only():
    reply = packing.checklist(single_snapshot(), [forecast_day('2030-04-28')])['reply']
    assert reply.endswith('5月1日还没有天气预报，临近出发再问我一次，我按预报补充提醒。')
    amount_reply = packing.checklist(single_snapshot(walking=0, returns='2030-05-01T17:00:00+08:00',
                                                     activities=[]),
                                     [forecast_day(probability=None, amount=2)])['reply']
    assert '带把伞：5月1日预报降水量约 2 毫米' in amount_reply
    assert '这趟行程比较轻松，带好手机就行。' not in amount_reply


def test_checklist_weather_multi_day_uses_relevant_extremes():
    snapshot = multi_snapshot(['2030-05-01', '2030-05-02', '2030-05-03'])
    forecast = [forecast_day('2030-05-01'), forecast_day('2030-05-02', probability=60),
                forecast_day('2030-05-03', minimum=6)]
    reply = packing.checklist(snapshot, forecast)['reply']
    assert '带把伞：5月2日预报降水概率最高 60%' in reply
    assert '注意保暖：5月3日最低约 6°C' in reply


def test_checklist_multi_day_combines_rain_and_uses_only_trip_dates():
    snapshot = multi_snapshot(['2030-05-01', '2030-05-02', '2030-05-03'])
    forecast = [forecast_day('2030-04-30', minimum=-2, maximum=40, probability=100),
                forecast_day('2030-05-03', minimum=8, maximum=31, probability=60),
                forecast_day('2030-05-02', minimum=6, maximum=33, probability=40)]
    reply = packing.checklist(snapshot, forecast)['reply']
    assert '· 带把伞：5月2日、5月3日预报有降水，最高概率 60%。' in reply
    assert reply.count('· 带把伞') == 1
    assert '· 注意保暖：5月2日最低约 6°C。' in reply
    assert '· 带好水和防晒：5月2日最高约 33°C。' in reply
    assert '4月30日' not in reply
    assert reply.endswith('天气依据和风天气预报，会有变化，出发前可以再问我一次。')
    missing = packing.checklist(multi_snapshot(['2030-05-03', '2030-05-01']), forecast[:1])['reply']
    assert missing.endswith('5月1日还没有天气预报，临近出发再问我一次，我按预报补充提醒。')


def test_packing_weather_integration(city, monkeypatch):
    planned(city)
    monkeypatch.setattr(understand, 'parse', lambda *args, **kwargs: (
        Understanding(conversation=Conversation(reply='带防晒霜、雨伞和驱蚊水。')),
        {'model_calls': 0, 'usage': [], 'basis': 'mock_model', 'source': 'mock_model'}))
    monkeypatch.setattr(weather, 'enabled', lambda: True)
    calls = []
    monkeypatch.setattr(weather, 'answer', lambda snapshot, **kwargs: calls.append(kwargs) or {
        'status': 'answered', 'weather': {'daily': {'days': [forecast_day(probability=60)]}},
        'provider_events': [{'endpoint': '/weather/v1/daily', 'status': 'success'}]})
    task = run(city, '我要带什么')
    assert '带把伞：5月1日预报降水概率最高 60%' in task['result']['reply']
    assert calls == [{'include_current': False}]
    assert task['events'][-1]['provider_events'] == [{'endpoint': '/weather/v1/daily', 'status': 'success'}]


@pytest.mark.parametrize('result', [None, {'status': 'unavailable', 'weather': None},
                                  {'status': 'answered', 'weather': {'daily': {'days': []}}},
                                  {'status': 'answered', 'weather': None}])
def test_packing_weather_failed_or_missing_keeps_e3_reply(city, monkeypatch, result):
    planned(city)
    snap, _ = agent_snapshot(city)
    expected = packing.checklist(snap)['reply']
    monkeypatch.setattr(understand, 'parse', lambda *args, **kwargs: (
        Understanding(conversation=Conversation(reply='模型不应接管清单。')),
        {'model_calls': 0, 'usage': [], 'basis': 'mock_model', 'source': 'mock_model'}))
    answer_calls = []
    monkeypatch.setattr(weather, 'enabled', lambda: True)
    def answer(*args, **kwargs):
        answer_calls.append(1)
        if result is None:
            raise RuntimeError('synthetic')
        return result
    monkeypatch.setattr(weather, 'answer', answer)
    task = run(city, '我要带什么')
    assert task['result']['reply'] == expected
    assert answer_calls == [1]


def test_packing_weather_disabled_does_not_query(city, monkeypatch):
    planned(city)
    monkeypatch.setattr(understand, 'parse', lambda *args, **kwargs: (
        Understanding(conversation=Conversation(reply='模型不应接管清单。')),
        {'model_calls': 0, 'usage': [], 'basis': 'mock_model', 'source': 'mock_model'}))
    monkeypatch.setattr(weather, 'enabled', lambda: False)
    monkeypatch.setattr(weather, 'answer', lambda *args, **kwargs: pytest.fail('must not query'))
    task = run(city, '我要带什么')
    assert task['result']['reply'].startswith('按你这趟已确认的行程')
    assert not any(event['tool'] == 'query_weather' for event in task['events'])


def test_packing_question_replaces_model_conversation_for_confirmed_route(city, monkeypatch):
    planned(city)
    monkeypatch.setattr(understand, 'parse', lambda *args, **kwargs: (
        Understanding(conversation=Conversation(reply='带防晒霜、雨伞和驱蚊水。')),
        {'model_calls': 0, 'usage': [], 'basis': 'mock_model', 'source': 'mock_model'}))
    task = run(city, '我要带什么')
    assert task['status'] == 'answered'
    assert task['result']['reply'].startswith('按你这趟已确认的行程')
    assert '带防晒霜、雨伞和驱蚊水' not in task['result']['reply']
