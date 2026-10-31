"""AMap business-hour parsing uses only local synthetic POI responses."""
from datetime import date
import importlib.util
from pathlib import Path

import pytest

from app.city import facts


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'amap_business_hours.py'


@pytest.fixture
def hours_module():
    spec = importlib.util.spec_from_file_location('amap_business_hours_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOURS = {
    '万岁山武侠城': '1月1日至4月30日 08:00-23:00(21:00停止入园)；5月1日至5月5日 08:00-23:00(21:30停止入园)；5月6日至12月31日 08:00-23:00(21:00停止入园)、大型节假日及周六至周日根据景区现场调整时间为主',
    '龙亭景区': '08:00-22:30（22:00停止检票）)',
    '大相国寺': '冬季 1/1-12/31(冬季) 08:00–17:30开放 最晚进入17:00；春秋季 1/1-12/31(春秋季) 08:00–18:00开放 最晚进入17:30；夏季 1/1-12/31(夏季) 08:00–18:30开放 最晚进入18:00',
    '中国翰园碑林': '08:00-21:30 最晚进入21:30',
    '包公祠': '冬令时 07:30-17:30开放（17:30停止入园）；夏令时 07:00-18:30开放（18:00停止入园）,一般情况下夏季六月至十月晚间有夜游,延长营业时间至22:00；具体开放时间以官方通知为准',
    '山陕甘会馆': '夏令时 1/1-12/31(夏令时) 08:00–18:00开放 最晚进入18:00；冬令时 1/1-12/31(冬令时) 08:00–17:30开放 最晚进入17:30',
}


@pytest.mark.parametrize('name,expected_nov,expected_jul', [
    ('万岁山武侠城', (((8, 0), (23, 0)), (21, 0)), (((8, 0), (23, 0)), (21, 0))),
    ('龙亭景区', (((8, 0), (22, 30)), (22, 0)), (((8, 0), (22, 30)), (22, 0))),
    ('大相国寺', (((8, 0), (18, 0)), (17, 30)), (((8, 0), (18, 30)), (18, 0))),
    ('中国翰园碑林', (((8, 0), (21, 30)), (21, 30)), (((8, 0), (21, 30)), (21, 30))),
    ('包公祠', (((7, 30), (17, 30)), (17, 30)), (((7, 0), (18, 30)), (18, 0))),
    ('山陕甘会馆', (((8, 0), (17, 30)), (17, 30)), (((8, 0), (18, 0)), (18, 0))),
])
def test_six_failed_poi_texts_are_selected_by_date(hours_module, name, expected_nov, expected_jul):
    assert hours_module.parse_day(HOURS[name], date(2026, 11, 1)) == ([expected_nov[0]], expected_nov[1])
    assert hours_module.parse_day(HOURS[name], date(2026, 7, 15)) == ([expected_jul[0]], expected_jul[1])


def test_month_day_segment_selects_the_matching_segment(hours_module):
    assert hours_module.parse_day(HOURS['万岁山武侠城'], date(2026, 5, 3)) == (
        [((8, 0), (23, 0))], (21, 30))
    assert hours_module.parse_day(HOURS['万岁山武侠城'], date(2026, 2, 1)) == (
        [((8, 0), (23, 0))], (21, 0))


def test_weekday_holiday_override_and_24_hour_regressions(hours_module):
    weekly = '周一至周日 09:00-22:00 最晚进入21:00'
    assert hours_module.parse_day(weekly, date(2026, 11, 1)) == ([((9, 0), (22, 0))], (21, 0))
    holiday = '周一至周日 09:00-22:00；10-01至10-07 周一至周日 10:00-20:00'
    assert hours_module.parse_day(holiday, date(2026, 11, 1)) == ([((9, 0), (22, 0))], None)
    assert hours_module.parse_day('24小时', date(2026, 11, 1)) == ([((0, 0), (23, 59))], None)


def test_build_marks_season_assumption_and_note(hours_module):
    place = facts.Place(kind='place', fact_id='place-test', name='大相国寺', place_type='scenic',
                        longitude=114.3, latitude=34.8, coordinate_system='GCJ02')

    class FakeAmap:
        def detail(self, poi_id):
            return {'id': poi_id, 'name': '大相国寺', 'location': '114.3,34.8',
                    'business': {'opentime_week': HOURS['大相国寺']}}

    records, report, bindings = hours_module.build(
        {('place', place.fact_id): place}, {}, {place.fact_id: 'poi-test'},
        [date(2026, 11, 1)], FakeAmap())
    rule = next(record for record in records if isinstance(record, facts.OpenRule))
    row = next(item for item in report if item['place_id'] == place.fact_id)
    assert rule.opens_at.hour == 8 and rule.closes_at.hour == 18 and rule.last_entry_at.minute == 30
    assert '季节按通用划分推算，以景区公告为准。' in rule.planning_note
    assert row['season_assumed'] == '春秋季' and row['rules'] == 1 and bindings == {'poi-test': place.fact_id}


def test_plain_interval_with_en_dash_is_daily(hours_module):
    assert hours_module.parse_day('08:00–22:30（22:00停止检票）)', date(2026, 11, 1)) == (
        [((8, 0), (22, 30))], (22, 0))


def test_no_matching_segment_and_free_text_do_not_change_hours(hours_module):
    assert hours_module.parse_day(
        '冬季 1/1-12/31(冬季) 08:00–17:30开放 最晚进入17:00', date(2026, 7, 15)
    ) == (None, 'seasonal_no_match')
    assert hours_module.parse_day(
        '夏令时 07:00-18:30开放（18:00停止入园）,夜游延长营业时间至22:00', date(2026, 7, 15)
    ) == ([((7, 0), (18, 30))], (18, 0))
