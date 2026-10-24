"""LLM -> validated fields -> existing workflow; all providers and data are isolated."""
import copy
import json
import pytest
from fastapi import HTTPException
from app import language
from app.city.agent import provider, understand, service
from test_city_assistant import city, path, get, rid
from test_city_agent import run, snapshot


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
    assert task['events'][0]['validation_failure']=={'kind':'evidence'}
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
    assert t['status']=='needs_clarification' and '尚未修改' in t['result']['reply']
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
