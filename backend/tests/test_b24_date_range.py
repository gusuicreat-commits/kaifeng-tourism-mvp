from datetime import date
import pytest
from app.city.agent.models import PlanningField, StateIntent
from app.city.agent.planning_intent import _field_check
from app.city.agent.date_range import resolve, counts
from app.city.agent.semantic_execution import grounded_state
from test_city_assistant import city


def f(name, value, quote, **extra):
    return PlanningField(field=name, value=value, quote=quote, **extra)


def test_weekend_two_days_uses_server_baseline_and_crosses_to_sunday():
    today=date(2026,10,26)
    assert resolve('周末', today)==date(2026,10,31)
    assert counts('周末玩两天','天')==[2]
    assert _field_check(f('trip_days',2,'周末玩两天'),'周末玩两天',today) is None


def test_range_roles_and_explicit_cross_year_are_independent():
    today=date(2026,10,26)
    assert _field_check(f('start_date','2026-10-31','10月31日到11月1日'),'10月31日到11月1日',today) is None
    assert _field_check(f('end_date','2026-11-01','10月31日到11月1日'),'10月31日到11月1日',today) is None
    assert resolve('2026-12-31',today)<resolve('2027-01-01',today)


def test_nights_and_day_scope_are_grounded_without_making_up_clock():
    assert counts('三天两晚','晚')==[2]
    value=f('start_time','09:00','第二天上午9点',day_ref='第二天')
    assert _field_check(value,'第二天上午9点',date(2026,10,26)) is None
    assert _field_check(f('start_time','14:00','第二天下午去开封府',day_ref='第二天下午'),'第二天下午去开封府',date(2026,10,26))=='clock_not_in_quote'


@pytest.mark.parametrize('text,day_ref,expected_scope,expected_day_ref',[
    ('每天晚上九点回开封府','2026-10-30','every_day',None),
    ('第二天晚上九点回来','第二天',None,'第二天'),
])
def test_day_ref_and_scope_recover_the_field_with_quote_evidence(city,monkeypatch,text,day_ref,expected_scope,expected_day_ref):
    from test_agent_output_tolerance import parse
    raw={'planning':{'quote':text,'fields':[{
        'field':'end_time','value':'21:00','quote':text,'scope':'every_day','day_ref':day_ref}]}}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert intent.planning is not None
    field_value=intent.planning.fields[0]
    assert field_value.scope==expected_scope and field_value.day_ref==expected_day_ref
    assert meta['recovered_fields']==[{'field':'end_time','source':'scope_or_day_from_quote','quote':text}]


def test_day_ref_and_scope_with_both_quotes_drop_only_that_field(city,monkeypatch):
    from test_agent_output_tolerance import dropped,parse
    text='每天第二天晚上九点回开封府'
    raw={'planning':{'quote':text,'fields':[{
        'field':'end_time','value':'21:00','quote':text,'scope':'every_day','day_ref':'第二天'}]}}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert intent.planning is None
    dropped(meta,'planning.fields[0]',reason='ambiguous_day_scope')


def test_future_state_scope_is_evidenced_and_rejected_by_executor_gate():
    value=StateIntent(operation='rest',quote='后面几天也一起改，每天休息20分钟',minutes=20,scope='current_and_future',scope_quote='后面几天')
    grounded_state(value,'后面几天也一起改，每天休息20分钟')


def test_complete_two_day_request_reaches_whole_trip_preview(city, monkeypatch):
    from test_planning_understanding import model, field
    from test_city_agent import run
    from test_city_assistant import path
    text='2030-05-03到2030-05-04，两天，第一天9点到20点，从[合成测试]origin到[合成测试]hotel；第二天9点到20点，从[合成测试]hotel回[合成测试]origin。'
    fields=[
        field('start_date','2030-05-03','2030-05-03到2030-05-04'),
        field('end_date','2030-05-04','2030-05-03到2030-05-04'),
        field('trip_days',2,'两天'),
        {'field':'start_time','value':'09:00','quote':'第一天9点','day_ref':'第一天'},
        {'field':'end_time','value':'20:00','quote':'第一天9点到20点','day_ref':'第一天'},
        {'field':'origin','value':'[合成测试]origin','quote':'从[合成测试]origin','day_ref':'第一天'},
        {'field':'destination','value':'[合成测试]hotel','quote':'到[合成测试]hotel','day_ref':'第一天'},
        {'field':'start_time','value':'09:00','quote':'第二天9点','day_ref':'第二天'},
        {'field':'end_time','value':'20:00','quote':'第二天9点到20点','day_ref':'第二天'},
        {'field':'origin','value':'[合成测试]hotel','quote':'从[合成测试]hotel','day_ref':'第二天'},
        {'field':'destination','value':'[合成测试]origin','quote':'回[合成测试]origin','day_ref':'第二天'},
    ]
    model(monkeypatch,text,fields)
    task=run(city,text)
    assert task['status']=='needs_confirmation',task
    assert '多天行程还在开发中' not in task['result']['reply']
    saved=city['client'].get(path(city,'/exploration/constraints')).json()
    assert saved['constraints']['schema_version']=='city-constraints/2'


def test_day_aliases_conflict_as_one_field_but_other_days_and_fields_survive(city,monkeypatch):
    from test_agent_output_tolerance import parse, dropped
    text='第一天9点或10点或11点开始，第二天8点开始，每天20点结束'
    raw={'planning':{'quote':text,'fields':[
        {'field':'start_time','value':v,'quote':q,'day_ref':ref}
        for v,q,ref in [('09:00','第一天9点','第一天'),('10:00','10点','第一天'),
                         ('11:00','11点','第一天'),('08:00','第二天8点','第二天')]]+[
        {'field':'end_time','value':'20:00','quote':'每天20点结束','scope':'every_day'}]}}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert [(f.field,f.value,f.day_ref) for f in intent.planning.fields]==[
        ('start_time','08:00','第二天'),('end_time','20:00',None)]
    assert meta['planning_conflicts']==[{'field':'start_time','day_ref':'第一天','question':'第一天你说的是“09:00”还是“10:00”还是“11:00”？'}]
    assert len(dropped(meta,reason='conflicting_values'))==3


def test_same_day_aliases_share_a_duplicate_key(city,monkeypatch):
    from test_agent_output_tolerance import parse
    text='第一天9点开始，首日10点开始，第二天8点开始'
    raw={'planning':{'quote':text,'fields':[
        {'field':'start_time','value':v,'quote':q,'day_ref':ref}
        for v,q,ref in [('09:00','第一天9点','第一天'),('10:00','首日10点','首日'),('08:00','第二天8点','第二天')]]}}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert [f.value for f in intent.planning.fields]==['08:00']
    assert len(meta['planning_conflicts'])==1


def test_continue_with_evidenced_correction_keeps_the_last_value_and_other_day(city,monkeypatch):
    from test_agent_output_tolerance import parse
    text='开始安排，第一天9点，不，第一天10点开始，第二天8点开始'
    raw={'planning':{'operation':'continue','quote':text,'fields':[
        {'field':'start_time','value':v,'quote':q,'day_ref':ref}
        for v,q,ref in [('09:00','第一天9点','第一天'),('10:00','第一天10点','第一天'),('08:00','第二天8点','第二天')]]},
        'corrections':[{'before':'第一天9点','after':'第一天10点','quote':'第一天9点，不，第一天10点开始'}]}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert intent.planning.operation=='update'
    assert [(f.value,f.day_ref) for f in intent.planning.fields]==[('10:00','第一天'),('08:00','第二天')]
    assert not meta.get('planning_conflicts')
    assert meta['dropped']==[{'path':'planning.operation','category':'C','reason':'continue_with_conditions'}]


def test_invalid_daily_evidence_only_drops_that_item_and_records_meta(city,monkeypatch):
    from test_agent_output_tolerance import parse, dropped
    text='第一天9点开始，第二天10点开始，想去[合成测试]museum'
    fields=[{'field':'start_time','value':'09:00','quote':'第一天9点','day_ref':'第一天'},
        {'field':'start_time','value':'10:00','quote':'第二天10点','day_ref':'第三天'},
        {'field':'end_time','value':'10:00','quote':'第二天10点','scope':'every_day'},
        {'field':'end_time','value':'20:00','quote':'第一天9点','day_ref':'第一天'}]
    raw={'planning':{'quote':text,'fields':fields,'choices':[
        {'name':'[合成测试]museum','priority':'want','quote':'想去[合成测试]museum','day_ref':'第四天'}]}}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert [(f.field,f.value,f.day_ref) for f in intent.planning.fields]==[
        ('start_time','09:00','第一天'),('start_time','10:00','第二天')]
    assert meta['recovered_fields']==[{'field':'start_time','source':'day_ref_from_quote','quote':'第二天'}]
    assert not intent.planning.choices and not meta.get('planning_conflicts')
    assert {d['path'] for d in dropped(meta)}=={'planning.fields[2]','planning.fields[3]','planning.choices[0]'}
    assert {d['check'] for d in meta['dropped_fields']}=={'day_ref_not_in_quote','scope_not_in_quote','clock_not_in_quote'}


def test_multi_day_conflict_asks_only_that_field_and_never_previews_or_offers_basket(city,monkeypatch):
    from test_agent_output_tolerance import emit, dropped
    from test_city_agent import run
    from test_city_assistant import path, get
    from app.city.agent import orchestration
    text='2030-05-03玩两天，第一天9点或者10点开始，第二天8点开始，想去[合成测试]museum'
    raw={'planning':{'quote':text,'fields':[
        {'field':'start_date','value':'2030-05-03','quote':'2030-05-03'},
        {'field':'trip_days','value':2,'quote':'两天'},
        *[{'field':'start_time','value':v,'quote':q,'day_ref':ref}
          for v,q,ref in [('09:00','第一天9点','第一天'),('10:00','10点','第一天'),('08:00','第二天8点','第二天')]]],
        'choices':[{'name':'[合成测试]museum','quote':'想去[合成测试]museum'}]}}
    emit(monkeypatch,raw)
    def unexpected(*a,**kw):raise AssertionError('conflict must not preview')
    monkeypatch.setattr(orchestration.exploration.engine,'preview',unexpected)
    before=get(city);task=run(city,text)
    assert task['status']=='needs_clarification' and get(city)==before
    assert '第一天你说的是' in task['result']['reply']
    assert '过夜' not in task['result']['reply'] and '结束' not in task['result']['reply']
    assert not task['result'].get('basket_proposal') and not task['result'].get('basket_choices')
    saved=city['client'].get(path(city,'/exploration/constraints')).json()
    assert saved['constraints']['days'][0]['starts_at'] is None
    assert saved['constraints']['days'][1]['starts_at']=='2030-05-04T08:00:00+08:00'
    assert saved['result'] is None
    dropped(task['interpretation'],reason='conflicting_values')


def test_multiday_missing_range_asks_only_start_date_without_attempting_preview(city,monkeypatch):
    from test_planning_understanding import model, field
    from test_city_agent import run
    from test_city_assistant import path, get
    text='去开封玩两天'
    model(monkeypatch,text,[field('trip_days',2,'两天')])
    before=get(city);task=run(city,text)
    assert task['status']=='needs_clarification'
    assert task['result']['reply']=='你打算哪天开始这趟行程？'
    saved=city['client'].get(path(city,'/exploration/constraints')).json()
    assert saved['constraints']['trip_days']==2 and saved['constraints']['start_date'] is None
    assert saved['result'] is None and get(city)==before


def test_multiday_complete_times_only_asks_the_missing_overnight_boundary(city,monkeypatch):
    from test_planning_understanding import model, field
    from test_city_agent import run
    from test_city_assistant import path, get
    text='2030-05-03玩两天，每天9点到20点，第一天从[合成测试]origin出发，第二天回[合成测试]origin'
    fields=[field('start_date','2030-05-03','2030-05-03'),field('trip_days',2,'两天'),
        {'field':'start_time','value':'09:00','quote':'每天9点到20点','scope':'every_day'},
        {'field':'end_time','value':'20:00','quote':'每天9点到20点','scope':'every_day'},
        {'field':'origin','value':'[合成测试]origin','quote':'第一天从[合成测试]origin出发','day_ref':'第一天'},
        {'field':'destination','value':'[合成测试]origin','quote':'第二天回[合成测试]origin','day_ref':'第二天'}]
    model(monkeypatch,text,fields)
    before=get(city);task=run(city,text)
    assert task['status']=='needs_clarification'
    assert task['result']['reply']=='2030-05-03晚上在哪个已知地点过夜，并从那里开始第二天？'
    saved=city['client'].get(path(city,'/exploration/constraints')).json()
    assert all(d['starts_at'].endswith('T09:00:00+08:00') and d['ends_at'].endswith('T20:00:00+08:00') for d in saved['constraints']['days'])
    assert saved['constraints']['days'][0]['origin_place_id']=='origin'
    assert saved['constraints']['days'][1]['destination_place_id']=='origin'
    assert saved['result'] is None and get(city)==before
