"""LLM -> validated fields -> existing workflow; all providers and data are isolated."""
import copy
import json
import pytest
from fastapi import HTTPException
from app import language
from app.city.agent import provider, understand, service
from test_city_assistant import city, path, get, rid
from test_city_agent import run, snapshot, empty_tasks


def model(monkeypatch,text,fields=(),**extra):
    calls=[]
    output={'actions':[{'kind':'modify','quote':text}],
            'planning':{'quote':text,'fields':list(fields)},**extra}
    monkeypatch.setattr(language,'available',lambda:True)
    def generate(system,payload,**kw):
        calls.append(payload)
        assert len(system)+len(json.dumps(payload,ensure_ascii=False))<=12000
        return copy.deepcopy(output),{'source':'mock_model','http_attempts':0}
    monkeypatch.setattr(provider,'generate',generate)
    return calls


def field(name,value,quote):return {'field':name,'value':value,'quote':quote}


@pytest.mark.parametrize('online',[False,True])
def test_round_trip_semantic_online_and_deterministic_offline_never_commits(city,monkeypatch,online):
    monkeypatch.setattr(language,'available',lambda:online)
    calls=[]
    def semantic(*a,**k):
        calls.append(1)
        return {'actions':[{'kind':'modify','quote':'从开封站出发和返回'}],
                'planning':{'quote':'从开封站出发和返回','fields':[field(role,'开封站','开封站') for role in ('origin','destination')]}},{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',semantic)
    before=get(city)
    task=run(city,'从开封站出发和返回')
    assert task['status']=='needs_clarification' and task['interpretation']['model_calls']==int(online)
    assert len(calls)==int(online)
    action=task['result']['planning_workflow']['result'];c=action['constraints']
    assert c['origin_text']==c['destination_text']=='开封站'
    assert c['origin_place_id'] is None and c['destination_place_id'] is None
    assert 'planning_action' not in task['result']
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['origin_text']==saved['destination_text']=='开封站'
    assert get(city)==before


@pytest.mark.parametrize('text',[
    '不要从开封站出发和返回','从开封站出发和返回是什么意思？',
    '从开封站出发和返回，下午五点前回来','从开封站还是宋城路站出发和返回',
    '从开封站出发和返回，再去清明上河园','从不是开封站出发和返回',
])
def test_round_trip_control_never_salvages_partial_or_ambiguous_request(text):
    from app.city.agent.planning_intent import explicit_round_trip
    assert explicit_round_trip(text) is None


def test_rejected_model_evidence_records_category_without_raw_output(city,monkeypatch):
    text='换个出发地点'
    model(monkeypatch,text,[field('origin','编造的酒店','当前原文没有这句话')])
    task=run(city,text)
    assert task['status']=='needs_clarification'
    # An invented field is dropped and asked again; its value never reaches state or output.
    assert task['interpretation']['dropped_fields']==[{'field':'origin','check':'quote_not_in_request'}]
    assert '编造的酒店' not in json.dumps(task,ensure_ascii=False)


@pytest.mark.parametrize('text',[
    '我想明天去开封玩','我明天想去开封玩','明天我想去开封玩','我们打算明天去开封旅游',
    '这趟开封之旅放到明天吧','日期换到明天，其他不变',
])
def test_free_text_reaches_llm_before_planning_workflow(city,monkeypatch,text):
    before=get(city);calls=model(monkeypatch,text,[field('date','明天','明天')])
    t=run(city,text)
    assert len(calls)==1 and calls[0]['text']==text
    assert t['interpretation']['source']=='mock_model' and t['mode']=='planning'
    assert t['status']=='needs_clarification',t
    action=t['result']['planning_workflow']['result']
    assert t['result']['planning_workflow']['trip_id']==city['trip']['id']
    assert action['constraints']['business_date']=='2030-05-02'
    assert action['constraints']['starts_at'] is None
    assert city['client'].get(path(city,'/exploration/constraints')).json()['version']==1
    assert 'planning_action' not in t['result']  # no frontend relay is needed
    assert city['client'].get(path(city,'/exploration/constraints')).json()['constraints']['business_date']=='2030-05-02'
    assert get(city)==before


def test_date_correction_preserves_saved_hours_and_only_current_versions_can_apply(city,monkeypatch):
    saved={'business_date':'2030-05-01','starts_at':'2030-05-01T09:00:00+08:00','ends_at':'2030-05-01T18:00:00+08:00'}
    def preview(c,version):return city['client'].post(path(city,'/exploration/preview'),json={'request_id':rid(),'expected_version':1,'basket_version':0,'planning_version':version,'constraints':c})
    assert preview(saved,0).status_code==200
    model(monkeypatch,'我明天想去开封玩',[field('date','明天','明天')])
    t=run(city,'我明天想去开封玩');action=t['result']['planning_workflow']['result']
    assert action['constraints']['starts_at']=='2030-05-02T09:00:00+08:00'
    assert action['constraints']['ends_at']=='2030-05-02T18:00:00+08:00'
    assert preview(saved|{'pace':'relaxed'},1).status_code==409
    assert preview(saved|{'pace':'relaxed'},2).status_code==200
    assert preview(action['constraints'],action['planning_version']).status_code==409
    assert city['client'].get(path(city,'/exploration/constraints')).json()['constraints']['business_date']=='2030-05-01'


@pytest.mark.parametrize('text',['我明天不想去开封玩','明天开封有什么演出？'])
def test_model_query_or_negative_does_not_modify_date(city,monkeypatch,text):
    calls=model(monkeypatch,text,planning=None,actions=[{'kind':'query','quote':text}])
    t=run(city,text)
    assert t.get('result',{}).get('planning_action') is None
    assert city['client'].get(path(city,'/exploration/constraints')).json()['version']==0


@pytest.mark.parametrize('fields',[
    [field('date','后天','明天')],
    [field('date','2030-02-30','2030-02-30')],
    [field('start_time','15:00','下午')],
    [field('start_time','09:00','下午6点')],
    [field('origin','编造的酒店','开封')],
    [field('date','明天','明天'),field('date','后天','后天')],
])
def test_invented_invalid_or_conflicting_fields_never_reach_workflow(city,monkeypatch,fields):
    text='明天或后天，2030-02-30，下午，下午6点，到开封'
    model(monkeypatch,text,fields)
    t=run(city,text)
    assert t['status']=='needs_clarification',t
    assert t['result'].get('planning_action') is None
    assert city['client'].get(path(city,'/exploration/constraints')).json()['version']==0


def test_time_range_quote_accepts_both_endpoints(city,monkeypatch):
    text='2026-10-25，17:30到22:00，从开封府出发并返回，全程步行'
    model(monkeypatch,text,[
        field('date','2026-10-25','2026-10-25'),
        field('start_time','17:30','17:30到22:00'),
        field('end_time','22:00','17:30到22:00'),
        field('origin','开封府','从开封府出发并返回'),
        field('destination','开封府','从开封府出发并返回'),
        field('mode','walking','全程步行'),
    ])
    t=run(city,text)
    # Time-range quote was accepted (no "invented time" rejection); the only
    # downstream stop is the synthetic fixture missing a real 开封府 place.
    assert t['mode']=='planning' and t['status']=='needs_data',t


def test_unresolved_extra_requirements_block_partial_date_write(city,monkeypatch):
    text='我明天想去开封玩，下午那场演出必须保留'
    model(monkeypatch,text,[field('date','明天','明天')],unresolved=['需要明确是哪场演出'])
    t=run(city,text)
    assert t['status']=='needs_clarification' and '哪场演出' in t['result']['reply']
    assert city['client'].get(path(city,'/exploration/constraints')).json()['version']==0


def test_model_failure_does_not_disguise_itself_as_missing_user_intent(city,monkeypatch):
    text='我明天想去开封玩'
    model(monkeypatch,text)
    def fail(*a,**kw):raise HTTPException(503,detail='isolated provider failure')
    monkeypatch.setattr(provider,'generate',fail)
    t=run(city,text)
    assert t['status']=='needs_clarification' and '没有变动' in t['result']['reply']
    assert city['client'].get(path(city,'/exploration/constraints')).json()['version']==0


def test_missing_model_does_not_claim_free_text_was_understood(city):
    t=run(city,'我明天想去开封玩')
    assert t['status']=='unavailable' and t['result']['state']=='model_unavailable'
    assert t['result']['code']=='MODEL_NOT_CONFIGURED' and t['metrics']['model_calls']==0
    assert t['metrics']['model_calls']==0


def test_continue_is_not_route_confirmation(city,monkeypatch):
    before=get(city);text='就这样'
    model(monkeypatch,text,planning={'operation':'continue','quote':text})
    t=run(city,text)
    assert t['result']['planning_workflow']['operation']=='continue'
    assert 'confirmation' not in t['result'] and get(city)==before


def test_model_cannot_downgrade_a_material_using_a_must_quote(city,monkeypatch):
    name='[合成测试]museum';text=name+'必去'
    c=city['client']
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,'item':{'kind':'place','object_id':'museum','priority':'must'}}).status_code==200
    model(monkeypatch,text,planning={'quote':text,'choices':[{'name':name,'priority':'want','quote':text}]})
    t=run(city,text)
    assert t['status']=='needs_clarification' and t['result'].get('planning_action') is None
    assert c.get(path(city,'/basket')).json()['items'][0]['priority']=='must'


@pytest.mark.parametrize('place_id,covered',[('museum',False),('restaurant',True)])
def test_named_material_does_not_erase_an_unmet_food_requirement(city,monkeypatch,place_id,covered):
    from app.city import facts
    from app.db import connect
    with connect() as db:
        food = facts.records(db)['food','food'].model_copy(update={'name':'灌汤包','version':2})
        facts.import_bundle(facts.FactBundle(records=(food,)),db,apply=True)
    name = '[合成测试]' + place_id
    text = name + '想去，还必须吃灌汤包'
    response = city['client'].patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':place_id,'priority':'want'}})
    assert response.status_code == 200
    model(monkeypatch,text,planning={'quote':text,'choices':[{'name':name,'priority':'want','quote':text}]})
    snap,_ = snapshot(city)
    intent,meta = understand.parse(text,snap)
    assert any(c.field == 'food' for c in intent.constraints) is not covered, (intent,meta,understand.rules(text,snap))


def test_cancel_after_model_understanding_never_publishes_workflow_input(city,monkeypatch):
    from test_city_agent import start,step
    text='我明天想去开封玩';model(monkeypatch,text,[field('date','明天','明天')])
    t=step(city,start(city,text).json()).json()
    assert t['mode']=='planning' and t['status']=='running'
    cancelled=city['client'].post(path(city,'/agent/tasks/'+t['task_id']+'/cancel')).json()
    assert cancelled['status']=='cancelled'
    assert step(city,cancelled).json()['result'].get('planning_action') is None
    assert city['client'].get(path(city,'/exploration/constraints')).json()['version']==0


@pytest.mark.parametrize('location_field',['city','destination','origin'])
def test_travel_city_is_not_a_specific_origin_or_return_point(city,monkeypatch,location_field):
    text='我明天想去开封玩'
    model(monkeypatch,text,[field('date','明天','明天'),field(location_field,'开封','开封')])
    task=run(city,text)
    action=task['result']['planning_workflow']['result']
    assert action['constraints']['business_date']=='2030-05-02'
    assert action['constraints']['origin_place_id'] is None
    assert action['constraints']['destination_place_id'] is None


def test_explicit_return_to_city_is_intent_not_a_navigable_place(city,monkeypatch):
    text='我明天回开封'
    model(monkeypatch,text,[field('date','明天','明天'),field('destination','开封','开封')])
    task=run(city,text)
    assert task['status']=='needs_clarification'
    c=task['result']['planning_workflow']['result']['constraints']
    assert c['destination_text']=='开封' and c['destination_place_id'] is None


def test_other_city_does_not_silently_become_kaifeng(city,monkeypatch):
    text='我明天想去郑州玩'
    model(monkeypatch,text,[field('date','明天','明天'),field('city','郑州','郑州')])
    task=run(city,text)
    assert task['status']=='needs_data' and task['result'].get('planning_action') is None


def test_reported_endpoints_replace_old_ids_without_becoming_facts(city,monkeypatch):
    text='从我住的酒店出发，最后回开封站'
    model(monkeypatch,text,[field('origin','我住的酒店','我住的酒店'),field('destination','开封站','开封站')])
    before=get(city)
    task=run(city,text)
    c=task['result']['planning_workflow']['result']['constraints']
    assert c['origin_text']=='我住的酒店' and c['destination_text']=='开封站'
    assert c['origin_place_id'] is None and c['destination_place_id'] is None
    assert get(city)==before


@pytest.mark.parametrize('text,expected',[
    ('今晚五点半从开封府出发，十点前回来',['17:30','22:00']),
    ('晚上七点到九点半',['19:00','21:30']),
    ('明早八点出发，十一点半回来',['08:00','11:30']),
    ('中午一点吃饭',['13:00']),
    ('五点半出发',['05:30']),
])
def test_clock_inherits_day_period_stated_earlier(text,expected):
    assert understand.clock_values(text)==expected


def test_clock_grounding_accepts_pm_reading_of_bare_hour_only_without_period():
    text='今晚五点半出发，十点前回来'
    assert understand.clock_grounded('22:00','十点前回来',text)
    assert not understand.clock_grounded('10:00','十点前回来',text)
    assert understand.clock_grounded('22:00','十点前回来','十点前回来')
    assert not understand.clock_grounded('22:00','早上十点前回来','早上十点前回来')


def test_evening_trip_sentence_keeps_times_return_point_and_food(city,monkeypatch):
    text='今晚五点半从开封府出发，想吃灌汤包，十点前回来，走路就行。'
    model(monkeypatch,text,[field('date','今晚','今晚'),field('start_time','17:30','今晚五点半'),
        field('origin','开封府','从开封府出发'),field('end_time','22:00','十点前回来'),field('mode','walking','走路就行')],
        constraints=[{'field':'food','value':'灌汤包','strength':'soft','quote':'想吃灌汤包'}])
    t=run(city,text)
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['starts_at'].endswith('T17:30:00+08:00') and saved['ends_at'].endswith('T22:00:00+08:00'),saved
    assert saved['origin_text']==saved['destination_text']=='开封府' or saved['origin_place_id']==saved['destination_place_id']
    assert '灌汤包' in t['result']['reply'] and '没听准' not in t['result']['reply']


def test_unsupported_field_is_dropped_and_asked_while_the_rest_is_kept(city,monkeypatch):
    text='明天从开封府出发，九点前结束'
    # The quote has no evening word, so 21:00 is acceptable but 23:00 is invented.
    model(monkeypatch,text,[field('date','明天','明天'),field('origin','开封府','从开封府出发'),field('end_time','23:00','九点前结束')])
    t=run(city,text)
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['business_date'] and saved['ends_at'] is None,saved
    assert t['result']['reply'].count('最晚几点结束？')==1 and '结束时间没听准' not in t['result']['reply']


def test_stitched_quote_is_mapped_back_to_the_request_span():
    text='今晚五点半从开封府出发，想吃灌汤包，十点前回来，走路就行。'
    assert understand.repair_quote('今晚五点半从开封府出发，十点前回来，走路就行。',text)==text[:-1]
    assert understand.repair_quote('从开封府出发...回来',text)=='从开封府出发，想吃灌汤包，十点前回来'
    assert understand.repair_quote('从开封站出发',text)=='从开封站出发'
    assert understand.repair_quote('回来，从开封府出发',text)=='回来，从开封府出发'


def test_live_shaped_output_with_stitched_quotes_still_plans(city,monkeypatch):
    # Shape recorded from a real model reply: stitched planning quote and a bare "回来" destination quote.
    text='今晚五点半从开封府出发，想吃灌汤包，十点前回来，走路就行。'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda s,p,**k:({'actions':[{'kind':'query','quote':'想吃灌汤包','purpose':'dining'}],
        'planning':{'quote':'今晚五点半从开封府出发，十点前回来，走路就行。','fields':[field('date','今晚','今晚'),field('start_time','17:30','五点半'),
        field('end_time','22:00','十点前回来'),field('origin','开封府','从开封府出发'),field('destination','开封府','回来'),field('mode','walking','走路就行')]}},{'source':'mock_model'}))
    t=run(city,text)
    assert t['interpretation']['basis']=='kimi_validated',t['interpretation']
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['starts_at'].endswith('T17:30:00+08:00') and saved['ends_at'].endswith('T22:00:00+08:00')
    assert (saved['destination_place_id'] or saved['destination_text'])==(saved['origin_place_id'] or saved['origin_text'])
    assert '没听准' not in t['result']['reply']


def test_failures_name_the_check_and_log_one_line_without_visitor_text(city,monkeypatch,caplog):
    import logging
    from app.city.agent import service as agent_service
    monkeypatch.setattr(agent_service.LOG,'propagate',True)
    text='明天从开封府出发'
    model(monkeypatch,text,[field('date','明天','明天'),field('origin','编造的酒店','从开封府出发')])
    with caplog.at_level(logging.INFO,logger='tourism.agent'):
        t=run(city,text)
    assert t['interpretation']['dropped_fields']==[{'field':'origin','check':'place_not_in_quote'}]
    assert t['events'][0]['basis']=='kimi_validated' and t['events'][0]['mode']
    line=json.loads(caplog.records[-1].getMessage())
    assert line['dropped']==[{'field':'origin','check':'place_not_in_quote'}] and line['steps'][0][0]=='understand'
    assert '开封府' not in caplog.text and '编造' not in caplog.text
    # A whole-turn rejection names the failed check and where it ran.
    monkeypatch.setattr(provider,'generate',lambda s,p,**k:({'actions':[{'kind':'modify','quote':'不在原话里'}]},{'source':'mock_model'}))
    t=run(city,'换个出发地点')
    assert t['interpretation']['validation_failure']=={'kind':'evidence','at':'understand.grounded','check':'ungrounded evidence quote'}


def dining(monkeypatch,text,**action):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda s,p,**k:({'actions':[{'kind':'search_dining','quote':text,**action}]},{'source':'mock_model'}))


def test_finding_shops_for_a_named_dish_needs_no_position_or_route(city,monkeypatch):
    text='帮我找个能吃[合成测试]菜品的店'
    dining(monkeypatch,text,target_name='[合成测试]菜品')
    before=get(city)
    t=run(city,text)
    assert t['mode']=='find' and t['status']=='answered',t['result']
    assert t['result']['items'][0]['place_id']=='restaurant' and '[合成测试]菜品可以去' in t['result']['reply']
    assert t['events'][-1]['tool']=='find_food' and get(city)==before


def test_nearby_dining_is_in_trip_replanning_and_declares_position(city,monkeypatch):
    # The model left near unset; the visitor's own "附近" still decides.
    text='附近哪里有[合成测试]菜品'
    dining(monkeypatch,text)
    t=run(city,text)
    assert t['mode']=='react' and t['result']['code']=='FRESH_POSITION_REQUIRED'
    assert t['understanding']['actions'][0]['near'] is True


@pytest.mark.parametrize('value',['明天','明晚','2030-05-02'])
def test_normalized_date_must_name_the_same_day_as_the_quote(city,monkeypatch,value):
    text='明晚五点半从开封府出发'
    model(monkeypatch,text,[field('date',value,'明晚'),field('start_time','17:30','明晚五点半')])
    run(city,text)
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['business_date']=='2030-05-02' and saved['starts_at'].endswith('T17:30:00+08:00'),saved


def test_pace_synonym_from_the_model_maps_to_the_enum(city,monkeypatch):
    text='明天下午两点出发，节奏放慢一点'
    model(monkeypatch,text,[field('date','明天','明天'),field('start_time','14:00','下午两点'),field('pace','slow','节奏放慢一点')])
    run(city,text)
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['pace']=='relaxed',saved


def test_plan_ready_reply_names_wanted_stops_the_default_plan_leaves_out():
    from app.city.agent.orchestration import _ready_reply
    result={'goal_keys':{'g1':'scenic:a','g2':'vendor:b'},'activity_names':{'g1':'开封府','g2':'第一楼'},
            'planning':{'result':{'itineraries':[{'activities':[{'goal_id':'g2'}]},{'activities':[{'goal_id':'g1'}]}]}}}
    assert _ready_reply(result)=='方案已准备好。时间不够，这版没排进：开封府。请点“确认方案”保存。'
    result['planning']['result']['itineraries'][0]['activities'].append({'goal_id':'g1'})
    assert _ready_reply(result)=='方案已准备好。请点“确认方案”保存。'


@pytest.mark.parametrize('today,weekend,next_weekend',[
    ('2026-10-30','2026-10-31','2026-11-07'),
    ('2026-10-31','2026-10-31','2026-11-07'),
    ('2026-11-01','2026-11-01','2026-11-07'),
])
def test_weekend_dates_share_planning_and_hotel_resolution(today,weekend,next_weekend):
    import re
    from datetime import date
    from app.city.agent.models import PlanningField,ServiceIntent
    from app.city.agent.planning_intent import DATE_EXPRESSION,resolve_date,_field_check
    from app.city.travel_services import semantic_requirements
    for expression in ('周末','这周末','本周末','下周末'):
        expected=next_weekend if expression=='下周末' else weekend
        assert re.findall(DATE_EXPRESSION,'计划'+expression+'出发')==[expression]
        assert resolve_date(expression,date.fromisoformat(today)).isoformat()==expected
        assert _field_check(PlanningField(**field('date',expected,expression)),expression,date.fromisoformat(today)) is None
        text=expression+'入住住一晚酒店'
        req=semantic_requirements(ServiceIntent(quote=text,fields=[field('check_in',expression,expression)]),text,{},date.fromisoformat(today))
        assert req['check_in']==expected


@pytest.mark.parametrize('duration',['玩两天','三天两晚','2天','游玩 3 天'])
def test_multiday_request_only_saves_first_day_and_explains_limit(city,monkeypatch,duration):
    text='2030-05-03去开封'+duration
    model(monkeypatch,text,[field('date','2030-05-03','2030-05-03')])
    before=get(city);task=run(city,text)
    assert '目前只能先排第一天（2030-05-03），多天行程还在开发中' in task['result']['reply']
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['business_date']=='2030-05-03' and not {'days','nights','end_date'} & saved.keys()
    assert get(city)==before


def test_single_day_request_does_not_show_multiday_notice(city,monkeypatch):
    text='2030-05-03去开封玩一天'
    model(monkeypatch,text,[field('date','2030-05-03','2030-05-03')])
    assert '多天行程' not in run(city,text)['result']['reply']


@pytest.mark.parametrize('scope',['每人','人均','一人','每个人'])
@pytest.mark.parametrize('home',['fields','constraints'])
def test_per_person_budget_is_dropped_after_normalization(city,monkeypatch,scope,home):
    text='明天去开封，预算'+scope+'800元'
    fields=[field('date','明天','明天')];budget=field('budget_fen',80000,'预算'+scope+'800元')
    extra={}
    if home=='constraints':extra['constraints']=[budget|{'strength':'hard'}]
    else:fields.append(budget)
    model(monkeypatch,text,fields,**extra);task=run(city,text)
    assert {'field':'budget_fen','check':'per_person_budget'} in task['interpretation']['dropped_fields']
    assert '按人预算暂时不能用于规划，先不按预算筛选' in task['result']['reply']
    assert '预算没听准' not in task['result']['reply']
    assert city['client'].get(path(city,'/exploration/constraints')).json()['constraints'].get('budget_fen') is None


@pytest.mark.parametrize('quote',['预算每人800元','800元'])
def test_per_person_budget_gate_checks_original_budget_clause(quote):
    from datetime import date
    from app.city.agent.models import PlanningField
    from app.city.agent.planning_intent import _field_check
    assert _field_check(PlanningField(**field('budget_fen',80000,quote)),'预算每人800元',date(2026,10,26))=='per_person_budget'
    assert _field_check(PlanningField(**field('budget_fen',80000,'800元')),'总预算800元',date(2026,10,26)) is None


@pytest.mark.parametrize('slot,value,quote,question',[
    ('date','错误日期','明天','你打算哪天去？'),
    ('start_time','13:00','上午9点','当天几点开始，最晚几点结束？'),
    ('end_time','21:00','晚上8点','当天几点开始，最晚几点结束？'),
    ('origin','不存在的起点','开封站','从哪里出发，最后返回哪里？'),
    ('destination','不存在的终点','开封站','从哪里出发，最后返回哪里？'),
])
def test_missing_field_question_does_not_repeat_dropped_field(city,monkeypatch,slot,value,quote,question):
    fields=[field('city','开封','开封')]
    if slot!='date':fields.append(field('date','明天','明天'))
    if slot in ('origin','destination'):
        fields.extend([field('start_time','09:00','上午9点'),field('end_time','20:00','晚上8点')])
    text='明天去开封，上午9点到晚上8点，从开封站出发和返回'
    fields.append(field(slot,value,quote));model(monkeypatch,text,fields)
    task=run(city,text);reply=task['result']['reply']
    assert question in reply and '没听准' not in reply
    assert len(task['interpretation']['dropped_fields'])==1


@pytest.mark.parametrize('text,target_name',[('想吃开封特色美食',None),('有什么好吃的',None),('想吃开封特色美食','开封特色美食')])
@pytest.mark.parametrize('kind',['query','search_dining'])
def test_general_dining_lists_only_dishes_with_supply_records(city,monkeypatch,text,target_name,kind):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':kind,'purpose':'dining','quote':text,'target_name':target_name}]},{'source':'mock_model'}))
    before=get(city);task=run(city,text);result=task['result']
    assert task['status']=='answered' and '[合成测试]菜品可以去：[合成测试]restaurant' in result['reply']
    assert result['items'][0]['food_id']=='food' and result['items'][0]['vendor_id']=='vendor'
    assert '想去哪家可以加入素材篮' in result['reply'] and '你想问哪一场演出' not in result['reply']
    assert get(city)==before


@pytest.mark.parametrize('kind',['query','search_dining'])
def test_general_nearby_dining_still_requires_position(city,monkeypatch,kind):
    text='附近有什么好吃的'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':kind,'purpose':'dining','quote':text}]},{'source':'mock_model'}))
    task=run(city,text)
    assert task['result']['code']=='FRESH_POSITION_REQUIRED' and '你现在在哪' in task['result']['reply']
    assert 'items' not in task['result']


@pytest.mark.parametrize('mutation',['missing','cancelled','unverified'])
def test_general_dining_does_not_infer_supply_from_restaurant_category(city,monkeypatch,mutation):
    from app.city.agent import toolbelt
    from app.city.agent.models import Understanding
    snap,_=snapshot(city)
    supply=next(r for r in snap.planning.facts.values() if r.kind=='supply')
    key=('supply',supply.fact_id)
    if mutation=='missing':snap.planning.facts.pop(key)
    else:snap.planning.facts[key]=supply.model_copy(update={'business_status':'cancelled'} if mutation=='cancelled' else {'validation_status':'unverified'})
    result=toolbelt.find_food(snap,Understanding(),general=True)
    assert result['status']=='needs_data' and not result['items'] and '[合成测试]restaurant' not in result['reply']


def test_general_dining_limits_dishes_and_distinct_shops(city):
    from collections import Counter
    from app.city.agent import toolbelt
    from app.city.agent.models import Understanding
    snap,_=snapshot(city);catalog=snap.planning.facts
    food=catalog['food','food'];vendor=catalog['vendor','vendor'];place=catalog['place','restaurant'];supply=catalog['supply','supply']
    for shop in range(3):
        catalog['place',f'shop:{shop}']=place.model_copy(update={'fact_id':f'shop:{shop}','name':f'[合成测试]店{shop}'})
        catalog['vendor',f'vendor:{shop}']=vendor.model_copy(update={'fact_id':f'vendor:{shop}','place_id':f'shop:{shop}'})
    for dish in range(6):
        catalog['food',f'dish:{dish}']=food.model_copy(update={'fact_id':f'dish:{dish}','name':f'[合成测试]菜{dish}'})
        for shop in range(3):
            identity=f'supply:{dish}:{shop}'
            catalog['supply',identity]=supply.model_copy(update={'fact_id':identity,'food_id':f'dish:{dish}','vendor_id':f'vendor:{shop}'})
    result=toolbelt.find_food(snap,Understanding(),general=True)
    counts=Counter(i['food_id'] for i in result['items'])
    assert len(counts)==5 and set(counts.values())=={2}
    for dish in counts:
        assert len({i['place_id'] for i in result['items'] if i['food_id']==dish})==2
