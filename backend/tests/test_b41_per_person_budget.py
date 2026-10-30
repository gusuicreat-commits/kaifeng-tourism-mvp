"""B4.1: a per-person budget is a recorded requirement checked against known costs after preview."""
from types import SimpleNamespace

import pytest

from app.city.agent.budget_check import check, daily, party_size
from test_city_assistant import city, path, get
from test_city_agent import run
from test_planning_understanding import field, model
from test_agent_backend_orchestration import prepare
from test_b26_multi_day_conditions import daily_fields, daily_text, range_fields, saved


@pytest.mark.parametrize('text,expected', [
    ('我们三个人', 3), ('我们俩每人500', 2), ('一家三口', 3), ('4位', 4), ('五个人，每位两百', 5),
    ('两个大人一个小孩', 3), ('一家四口，两个大人两个孩子', 4), ('我们三个一起去', 3),
])
def test_party_size_needs_a_stated_count(text, expected):
    assert party_size(text) == expected


@pytest.mark.parametrize('text', [
    '和朋友', '带家人', '一人800', '一个人不超过200块', '订两人间', '三个景点', '我们三个景点都想去',
    '玩两天每人800', '一家三口，两个大人', '我们三个人，四位游客',
])
def test_no_count_or_conflicting_counts_leave_party_unknown(text):
    assert party_size(text) is None


@pytest.mark.parametrize('quote,text,expected', [
    ('预算每人每天300元', '预算每人每天300元', True),
    ('300元', '三天行程，每人每天300元', True),
    ('300元', '每日每人300元，住开封府附近', True),
    ('每人800', '两天都去万岁山，每人800', False),
    ('800元', '每天八点出发。预算每人800元', False),
    ('每人800', '每人800', False),
])
def test_daily_budget_needs_words_in_the_same_clause(quote, text, expected):
    assert daily(quote, text) is expected


def ctx(costs=(), names=None):
    return SimpleNamespace(operations=SimpleNamespace(costs=[SimpleNamespace(place_id=p, evidence=None) for p in costs]),
                           facts={('place', k): SimpleNamespace(name=v, place_type='scenic' if '馆' in v else 'night_visit')
                                  for k, v in (names or {}).items()},
                           evidence_ready=lambda evidence, *moments: True)


def leg(mode='walking', cost=0, boundaries=()):
    return {'journey': {'travel': {'mode': mode}, 'cost_upper_fen': cost,
                        'boundaries': [{'cost_upper_fen': b} for b in boundaries]}}


def day(activities=(), legs=()):
    return {'activities': [{'kind': k, 'place_id': p} for k, p in activities], 'legs': list(legs),
            'starts_at': '2030-05-01T09:00:00+08:00', 'ends_at': '2030-05-01T20:00:00+08:00'}


def test_known_cost_within_budget_names_what_is_left_out():
    sentence = check({'per_person_budget_fen': 80000}, {'known_cost_upper_fen': 23000, 'cost_unknown': True},
        days=[day([('visit', 'park'), ('dining', 'shop')], [leg(), leg('walking', 0, [3000])])], ctx=ctx(['park']))
    assert sentence == '按每人预算核对：已知费用每人约230元，在预算内（每人800元）（未计入：餐饮）。'


def test_over_budget_says_by_how_much_and_keeps_the_plan_confirmable():
    sentence = check({'per_person_budget_fen': 30000, 'party_size': 2}, {'known_cost_upper_fen': 42000, 'cost_unknown': True},
        days=[day([('visit', 'park'), ('visit', 'museum'), ('visit', 'market')], [leg('driving', None), leg()])],
        ctx=ctx(['park'], {'museum': '某博物馆', 'market': '某夜市'}))
    assert '已知费用每人约420元，超出约120元（预算每人300元），方案仍可确认' in sentence
    assert '未计入：某博物馆门票、某夜市费用、打车或驾车费用' in sentence
    assert '2人合计已知费用约840元，预算合计600元（按每人×2人换算）' in sentence


def test_all_costs_unknown_cannot_be_checked():
    sentence = check({'per_person_budget_fen': 80000}, {'known_cost_upper_fen': 0, 'cost_unknown': True},
        days=[day([('visit', 'museum')], [leg('driving', None), leg('walking', 0, [None])])], ctx=ctx())
    assert sentence.startswith('按每人800元预算核对：已知费用不足以核对预算')
    assert '部分地点费用' in sentence and '打车或驾车费用' in sentence and '部分入园费用' in sentence
    assert '在预算内' not in sentence and '超出' not in sentence


def test_daily_budget_scales_by_days_and_stays_list_lodging():
    sentence = check({'per_person_budget_fen': 30000, 'per_person_budget_daily': True},
        {'known_cost_upper_fen': 50000, 'cost_unknown': True},
        days=[day([('visit', 'park')], [leg()]), day([('visit', 'park')], [leg()])], overnight=True, ctx=ctx(['park']))
    assert '在预算内（每人每天300元×2天，每人600元）' in sentence and '未计入：住宿' in sentence


def test_without_per_person_budget_nothing_is_said():
    assert check({'budget_fen': 80000}, {'known_cost_upper_fen': 0, 'cost_unknown': False}, days=[day()], ctx=ctx()) == ''


def per_person_fields(quote, value):
    return [field('date', '2030-05-01', '2030-05-01'), field('start_time', '09:00', '上午9点'), field('end_time', '20:00', '晚上8点'),
            field('origin', '[合成测试]origin', '[合成测试]origin'), field('destination', '[合成测试]hotel', '[合成测试]hotel'),
            field('budget_fen', value, quote) | {'budget_basis': 'per_person'}]


@pytest.mark.parametrize('people,party', [('我们三个人，', 3), ('和朋友一起，', None)])
def test_single_day_preview_checks_the_per_person_budget(city, monkeypatch, people, party):
    message = prepare(city, monkeypatch) + '，' + people + '预算每人100元'
    model(monkeypatch, message, per_person_fields('预算每人100元', 10000))
    task = run(city, message)
    reply = task['result']['reply']
    assert task['status'] == 'answered' and task['result']['planning_workflow']['result']['status'] == 'feasible'
    values = saved(city)
    assert values['per_person_budget_fen'] == 10000 and values['budget_fen'] is None and values['party_size'] == party
    assert '按每人预算核对：已知费用每人约0元，在预算内（每人100元）' in reply
    # Once the plan exists the check replaces the "recorded" note; no old notices remain.
    assert '方案出来后' not in reply and '按人预算暂时不能用于规划' not in reply and '格式无法核对' not in reply
    if party:assert '3人合计已知费用约0元，预算合计300元（按每人×3人换算）' in reply
    else:assert '人合计' not in reply


def test_multi_day_preview_checks_a_daily_per_person_budget(city, monkeypatch):
    text = daily_text() + '，预算每人每天300元'
    budget = field('budget_fen', 30000, '预算每人每天300元') | {'budget_basis': 'per_person'}
    model(monkeypatch, text, range_fields() + daily_fields('every_day') + [budget])
    before = get(city)
    task = run(city, text)
    assert task['status'] == 'needs_confirmation', task
    assert len(task['result']['planning_workflow']['result']['result']['itineraries'][0]['days']) == 3
    values = saved(city)
    assert values['per_person_budget_fen'] == 30000 and values['per_person_budget_daily'] is True
    reply = task['result']['reply']
    # Without a stated party the per-person check stays; whole-trip pricing is not switched on.
    assert values.get('pricing') is None
    assert '按每人预算核对' in reply and '每人每天300元×3天，每人900元' in reply and '住宿' in reply
    assert '全程已计入费用' not in reply and '明确旅行人数' not in reply
    assert '多天全程预算尚未接通' not in reply and get(city) == before


def test_multi_day_per_person_budget_with_a_stated_party_uses_the_trip_summary(city, monkeypatch):
    text = daily_text() + '，我们三个人，预算每人每天300元'
    budget = field('budget_fen', 30000, '预算每人每天300元') | {'budget_basis': 'per_person'}
    model(monkeypatch, text, range_fields() + daily_fields('every_day') + [budget])
    task = run(city, text)
    assert task['status'] == 'needs_confirmation', task
    values = saved(city)
    assert values['party_size'] == 3 and values['pricing']['party_size'] == 3
    plan = task['result']['planning_workflow']['result']['result']['itineraries'][0]
    assert plan['budget']['limit_fen'] == 30000 * 3 * 3
    assert '全程已计入费用' in task['result']['reply'] and '按每人预算核对' not in task['result']['reply']
    # Unknown items read like the per-person check: grouped, in visitor words.
    assert '（未计入：餐饮、住宿）' in task['result']['reply'], task['result']['reply']


def test_multi_day_total_budget_reaches_the_shared_budget_summary(city, monkeypatch):
    text = daily_text() + '，总预算900元'
    budget = field('budget_fen', 90000, '总预算900元') | {'budget_basis': 'total'}
    model(monkeypatch, text, range_fields() + daily_fields('every_day') + [budget])
    task=run(city,text);reply=task['result']['reply']
    plan=task['result']['planning_workflow']['result']['result']['itineraries'][0]
    assert saved(city)['pricing']['total_budget_fen']==90000
    assert plan['budget']['limit_fen']==90000
    assert '全程预算900元，全程已计入费用约' in reply and '多天全程预算尚未接通' not in reply
    assert '未计入：' in reply and '：未设置' not in reply and '待核对：' not in reply


@pytest.mark.parametrize('amounts,tolls,expected', [
    ([1500],[0],'打车约15元/车（高德估计）'),
    ([1500,2300],[200,300],'打车约38元/车（高德估计）'),
    ([None],[None],'打车或驾车费用'),
    ([1500,None],[0,None],'打车约15元/车（高德估计）'),
    ([0],[0],None),  # zero-distance hops: known free, nothing to list
])
def test_vehicle_references_are_aggregated_across_days_but_not_per_person(amounts,tolls,expected):
    days=[]
    for amount,toll in zip(amounts,tolls):
        transport=leg('driving',None)
        transport['journey'].update(vehicle_cost_fen=amount,tolls_fen=toll)
        days.append(day(legs=[transport]))
    sentence=check({'per_person_budget_fen':10000,'party_size':3},
                   {'known_cost_upper_fen':5000,'cost_unknown':True},days=days,ctx=ctx())
    if expected:assert expected in sentence
    else:assert '打车' not in sentence
    assert '已知费用每人约50元，在预算内' in sentence
    assert '3人合计已知费用约150元' in sentence
    if any(tolls):assert '过路费约5元' in sentence
    else:assert '过路费' not in sentence
    if None in amounts:assert '打车或驾车费用' in sentence
