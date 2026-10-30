from types import SimpleNamespace

from app.city.agent import packing
from test_city_assistant import city, planned
from test_city_agent import run
from app.city.agent import understand
from app.city.agent.models import Understanding, Conversation


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
        'activities': activities or [activity('show', '2030-05-01T19:00:00+08:00')],
        'legs': [{'journey': {'travel': {'mode': mode}}}],
    }
    envelope = {
        'official_plan': {'status': 'current', 'activities': [{'goal_id': 'show', 'name': '晚间演出'}]},
        'requirements': {'starts_at': starts, 'business_date': '2030-05-01'},
        'facts': [],
    }
    return SimpleNamespace(envelope=envelope, route={'itinerary': itinerary}, multi_itinerary=None, planning=None)


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


def test_packing_question_replaces_model_conversation_for_confirmed_route(city, monkeypatch):
    planned(city)
    monkeypatch.setattr(understand, 'parse', lambda *args, **kwargs: (
        Understanding(conversation=Conversation(reply='带防晒霜、雨伞和驱蚊水。')),
        {'model_calls': 0, 'usage': [], 'basis': 'mock_model', 'source': 'mock_model'}))
    task = run(city, '我要带什么')
    assert task['status'] == 'answered'
    assert task['result']['reply'].startswith('按你这趟已确认的行程')
    assert '带防晒霜、雨伞和驱蚊水' not in task['result']['reply']
