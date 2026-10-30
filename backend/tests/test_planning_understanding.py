"""LLM -> validated fields -> existing workflow; all providers and data are isolated."""
import copy
import json
from typing import get_args
import pytest
from fastapi import HTTPException
from app import language
from app.city.agent import provider, understand, service, orchestration
from app.city.agent.models import PlanningField, ServiceField, Constraint, Understanding
from app.city.agent.prompt_schema import prompt_schema
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


def choices(monkeypatch,text,*items,fields=(),**extra):
    return model(monkeypatch,text,planning={'quote':text,'fields':list(fields),'choices':[
        {'name':name,'priority':priority,'status':status,'quote':quote,
         'priority_stated':item[4] if len(item)>4 else True}
        for item in items for name,priority,status,quote in [item[:4]]]},**extra)


def named_basket(city,*items):
    from app.city import facts
    from app.db import connect
    with connect() as db:
        original=facts.records(db)['place','museum']
        facts.import_bundle(facts.FactBundle(records=tuple(original.model_copy(update={
            'fact_id':identity,'name':name,'aliases':aliases,'version':1}) for identity,name,aliases in items)),db,apply=True)
    c=city['client'];version=c.get(path(city,'/basket')).json()['version']
    for identity,_,_ in items:
        response=c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':version,
            'item':{'kind':'place','object_id':identity,'priority':'must'}})
        assert response.status_code==200
        version=response.json()['version']
    return c.get(path(city,'/basket')).json()


@pytest.mark.parametrize('name,aliases,model_name,text,priority,status,stated',[
    ('包公祠',(),'包公祠','包公那边就不去了','want','standby',False),
    ('龙亭公园',(),'龙亭公园','龙亭算了','want','standby',False),
    ('汴梁民俗博物馆',(),'汴梁民俗博物馆','民俗博物这站先搁着','want','standby',False),
    ('甲地文化园',('湖畔展馆',),'甲地文化园','湖畔那站暂列候补','want','standby',False),
    ('甲地文化园',('湖畔展馆',),'湖畔展馆','湖畔那站暂列候补','want','standby',False),
    ('龙亭公园',(),'龙亭公园','龙亭有空再安排','optional','pending',True),
    ('汴梁民俗博物馆',(),'汴梁民俗博物馆','民俗博物改成一般想去','want','pending',True),
])
def test_basket_short_name_proposes_full_name_and_waits_for_confirmation(city,monkeypatch,name,aliases,model_name,text,priority,status,stated):
    before=named_basket(city,('short:one',name,aliases));trip=get(city)
    choices(monkeypatch,text,(model_name,priority,status,text,stated))
    task=run(city,text)
    assert task['status']=='needs_confirmation',task
    proposal=task['result']['basket_proposal'];item=proposal['items'][0]
    assert len(proposal['items'])==1 and proposal['basket_version']==before['version']
    assert item=={'kind':'place','object_id':'short:one','name':name,'quote':text,
        'operation':'standby' if status=='standby' else 'put','priority':priority if stated else 'must'}
    assert task['interpretation']['short_name_matches']==[{'name':model_name,'method':'common_substring','material_keys':['place:short:one']}]
    assert not task['interpretation'].get('dropped_fields')
    assert city['client'].get(path(city,'/basket')).json()==before and get(city)==trip


@pytest.mark.parametrize('count',[2,4])
@pytest.mark.parametrize('aliases',[False,True])
@pytest.mark.parametrize('status',['standby','pending'])
def test_basket_short_name_lists_every_match_without_trusting_model_target(city,monkeypatch,count,aliases,status):
    names=[f'合成地点{i}' if aliases else f'双桥分馆{i}' for i in range(count)]
    before=named_basket(city,*[(f'short:{i}',name,(f'双桥别馆{i}',) if aliases else ()) for i,name in enumerate(names)])
    text='双桥这次先不安排' if status=='standby' else '双桥就有时间再去吧'
    choices(monkeypatch,text,(names[0],'optional',status,text,status=='pending'))
    trip=get(city);task=run(city,text)
    assert task['status']=='needs_clarification' and '是哪一个' in task['result']['reply'],task
    assert 'basket_proposal' not in task['result']
    prompt=task['result']['basket_choices'][0]
    assert prompt['operation']==('standby' if status=='standby' else 'put')
    assert prompt['priority_stated']==(status=='pending') and prompt['quote']==text
    assert {(i['object_id'],i['name']) for i in prompt['candidates']}=={(f'short:{i}',name) for i,name in enumerate(names)}
    assert set(task['interpretation']['short_name_matches'][0]['material_keys'])=={f'place:short:{i}' for i in range(count)}
    assert city['client'].get(path(city,'/basket')).json()==before and get(city)==trip


def test_short_name_candidates_come_from_the_chosen_name_not_other_places_in_the_sentence(city,monkeypatch):
    # Claude review: "夜市不去了，上河园留着" must not make 上河园 a candidate for 夜市.
    before=named_basket(city,('short:market','双桥夜市',()),('short:park','清风上河园',()),('short:hall','双桥会馆府',()))
    for text in ('夜市不去了，上河园留着','双桥的夜市这次不去了'):
        choices(monkeypatch,text,('双桥夜市','want','standby',text,False))
        task=run(city,text)
        assert 'basket_proposal' in task['result'],(text,task['result'].get('reply'),task['interpretation'].get('dropped_fields'))
        items=task['result']['basket_proposal']['items']
        assert [(i['object_id'],i['operation']) for i in items]==[('short:market','standby')],(text,task['result'])
        assert city['client'].get(path(city,'/basket')).json()==before


@pytest.mark.parametrize('shape',['choices','cancel_actions'])
def test_short_name_read_as_all_candidates_proposes_them_together(city,monkeypatch,shape):
    # Claude review: live model output for "夜市不去了" with two night markets named both,
    # as two choices or two cancel actions. Both go into one confirmation card.
    before=named_basket(city,('short:a','双桥夜市',()),('short:b','西岸夜市',()))
    text='夜市不去了'
    monkeypatch.setattr(language,'available',lambda:True)
    if shape=='choices':
        choices(monkeypatch,text,('双桥夜市','want','standby',text,False),('西岸夜市','want','standby',text,False))
    else:
        monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'cancel','quote':text,'target_name':n}
            for n in ('双桥夜市','西岸夜市')]},{'source':'mock_model'}))
    trip=get(city);task=run(city,text)
    items=task['result']['basket_proposal']['items']
    assert sorted((i['object_id'],i['operation']) for i in items)==[('short:a','standby'),('short:b','standby')],task['result']
    assert city['client'].get(path(city,'/basket')).json()==before and get(city)==trip


def test_short_name_with_a_partial_model_choice_asks_once(city,monkeypatch):
    before=named_basket(city,('short:a','双桥夜市',()),('short:b','西岸夜市',()),('short:c','北门夜市',()))
    text='夜市不去了'
    choices(monkeypatch,text,('双桥夜市','want','standby',text,False),('西岸夜市','want','standby',text,False))
    task=run(city,text)
    assert 'basket_proposal' not in task['result']
    assert task['result']['reply'].count('哪一个')==1 and len(task['result']['basket_choices'])==1,task['result']
    assert city['client'].get(path(city,'/basket')).json()==before


@pytest.mark.parametrize('model_target',['双桥夜市','西岸夜市'])
def test_state_skip_with_short_name_asks_which_basket_item(city,monkeypatch,model_target):
    # Claude review: a model-chosen full name must not pick one of several basket items
    # that the visitor's short form fits ("夜市" with two night markets).
    before=named_basket(city,('short:a','双桥夜市',()),('short:b','西岸夜市',()))
    text='算了，夜市就不逛了'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'progress','quote':text}],
        'state_request':{'operation':'skip','quote':text,'target_name':model_target}},{'source':'mock_model'}))
    trip=get(city);task=run(city,text)
    assert 'basket_proposal' not in task['result'],task['result']
    assert '双桥夜市' in task['result']['reply'] and '西岸夜市' in task['result']['reply'],task['result']['reply']
    assert city['client'].get(path(city,'/basket')).json()==before and get(city)==trip


@pytest.mark.parametrize('in_basket,model_name,text,status,stated',[
    (False,'包公祠','包公那边就不去了','standby',False),
    (False,'包公祠','想去包公那边','pending',True),
    (True,'包公祠','另外那站先搁置','standby',False),
    (True,'包公祠','包不去了','standby',False),
    (True,'包公祠','包然后公那站算了','standby',False),
    (True,'包公祠','包公挺有意思','pending',False),
    (True,'包公祠','包公好玩吗','standby',False),
    (True,'包','包不去了','standby',False),
])
def test_short_name_does_not_authorize_additions_questions_or_single_characters(city,monkeypatch,in_basket,model_name,text,status,stated):
    named_basket(city,('short:one','包公祠',()))
    c=city['client']
    if not in_basket:
        before=c.get(path(city,'/basket')).json()
        assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':before['version'],
            'operation':'remove','item':{'kind':'place','object_id':'short:one'}}).status_code==200
    before=c.get(path(city,'/basket')).json();trip=get(city)
    choices(monkeypatch,text,(model_name,'optional',status,text,stated),actions=[])
    task=run(city,text)
    assert 'basket_proposal' not in task['result'] and 'basket_choices' not in task['result'],task
    assert not task['interpretation'].get('short_name_matches')
    if model_name!='包' and not text.endswith('吗'):
        assert {'field':'choice','check':'choice_not_in_quote'} in task['interpretation']['dropped_fields']
    assert c.get(path(city,'/basket')).json()==before and get(city)==trip


@pytest.fixture
def reference_city(city):
    import model_replay
    model_replay.import_reference()
    return city


def test_empty_basket_proposes_places_saves_conditions_and_never_writes_materials(reference_city,monkeypatch):
    from app.db import connect
    e=reference_city;text='明晚五点半从开封府出发，想去清明上河园和鼓楼夜市，十点前回来'
    before=get(e);basket=e['client'].get(path(e,'/basket')).json()
    choices(monkeypatch,text,('清明上河园','want','pending','想去清明上河园和鼓楼夜市'),('鼓楼夜市','want','pending','想去清明上河园和鼓楼夜市'),fields=[
        field('date','明天','明晚'),field('start_time','17:30','五点半'),field('origin','开封府','开封府'),field('end_time','22:00','十点前回来')])
    task=run(e,text);proposal=task['result']['basket_proposal']
    assert task['status']=='needs_confirmation' and proposal['basket_version']==0
    assert [(i['operation'],i['kind'],i['object_id'],i['priority']) for i in proposal['items']]==[
        ('put','place','pl_wb_000001','want'),('put','place','pl_wb_000013','want')]
    saved=e['client'].get(path(e,'/exploration/constraints')).json()
    assert saved['constraints']['business_date']=='2030-05-02'
    assert saved['constraints']['starts_at']=='2030-05-02T17:30:00+08:00' and saved['constraints']['ends_at']=='2030-05-02T22:00:00+08:00'
    assert saved['constraints']['origin_place_id']==saved['constraints']['destination_place_id']=='pl_wb_000003'
    assert saved['result'] is None and '确认后我接着排' in task['result']['reply']
    assert e['client'].get(path(e,'/basket')).json()==basket and get(e)==before
    with connect() as db:assert db.execute('SELECT COUNT(*) FROM city_engine_candidates').fetchone()[0]==0


@pytest.mark.parametrize('text,quote,model_priority,expected',[
    ('清明上河园必去','清明上河园','must','want'),
    ('一定要去清明上河园','一定要去清明上河园','must','must'),
    ('清明上河园一定得去','清明上河园一定得去','must','must'),
    ('想去清明上河园','想去清明上河园','want','want'),
    ('清明上河园顺路看看','清明上河园顺路看看','optional','optional'),
    ('鼓楼夜市必去，想去清明上河园','想去清明上河园','want','want'),
])
def test_new_material_priority_requires_attitude_evidence(reference_city,monkeypatch,text,quote,model_priority,expected):
    choices(monkeypatch,text,('清明上河园',model_priority,'pending',quote))
    task=run(reference_city,text)
    assert task['result']['basket_proposal']['items'][0]['priority']==expected
    assert reference_city['client'].get(path(reference_city,'/basket')).json()['items']==[]


def test_material_priority_prompt_defines_default_and_all_three_levels(city,monkeypatch):
    name='[合成测试]museum';text='去趟'+name
    choices(monkeypatch,text,(name,'want','pending',text));generate=provider.generate
    def check_prompt(system,payload,**kwargs):
        schema=json.dumps(prompt_schema(Understanding.model_json_schema()),ensure_ascii=False)
        prompt_body=system[:system.find('{')]
        for definition in ('must=游客明确表示非去不可','want=一般想去（默认）','optional=有空/顺路再去','没有明确表态时用want'):
            assert definition in schema
            assert definition not in prompt_body
        return generate(system,payload,**kwargs)
    monkeypatch.setattr(provider,'generate',check_prompt)
    task=run(city,text)
    assert task['status']=='needs_confirmation' and task['result']['basket_proposal']['items'][0]['priority']=='want'


@pytest.mark.parametrize('name,object_id',[('万岁山','pl_wb_000002'),('清明上河园','pl_wb_000001')])
def test_named_scenic_can_be_proposed_without_a_detail_page(reference_city,monkeypatch,name,object_id):
    text='想去'+name;choices(monkeypatch,text,(name,'want','pending',text))
    task=run(reference_city,text)
    assert task['result']['basket_proposal']['items'][0]['object_id']==object_id


def test_food_name_or_alias_proposes_food_without_choosing_a_vendor(reference_city,monkeypatch):
    from app.city import facts
    from app.db import connect
    e=reference_city
    with connect() as db:
        food=facts.records(db)['food','food_wb_000001']
        facts.import_bundle(facts.FactBundle(records=(food.model_copy(update={'aliases':('汤包',),'version':food.version+1}),)),db,apply=True)
    for name in ('灌汤包','汤包'):
        text='想吃'+name;choices(monkeypatch,text,(name,'want','pending',text))
        task=run(e,text);items=task['result']['basket_proposal']['items']
        assert len(items)==1 and items[0]['kind']=='food' and items[0]['object_id']=='food_wb_000001' and items[0]['priority']=='want'
        assert '把有这道菜的店加进素材篮' not in task['result']['reply']
    assert e['client'].get(path(e,'/basket')).json()['items']==[]


def test_unknown_material_has_explanation_and_no_proposal(city,monkeypatch):
    text='想去不存在的景点';choices(monkeypatch,text,('不存在的景点','want','pending',text))
    task=run(city,text)
    assert 'basket_proposal' not in task['result'] and '资料里没有找到“不存在的景点”' in task['result']['reply']


@pytest.mark.parametrize('compound',[False,True])
def test_ambiguous_material_lists_at_most_three_candidates_without_picking(city,monkeypatch,compound):
    from app.city import facts
    from app.db import connect
    with connect() as db:
        original=facts.records(db)['place','museum']
        records=tuple(original.model_copy(update={'fact_id':f'branch{i}','name':f'合成分店{i}','aliases':('合成店',)}) for i in range(4))
        facts.import_bundle(facts.FactBundle(records=records),db,apply=True)
    text='想去合成店';choices(monkeypatch,text,('合成店','want','pending',text),
        **({'conversation':{'reply':'可以一起安排。'}} if compound else {}))
    task=run(city,text)
    assert 'basket_proposal' not in task['result']
    candidates=task['result']['basket_choices'][0]['candidates']
    assert task['result']['basket_choices'][0]['priority']=='want' and task['result']['basket_choices'][0]['quote']==text
    assert len(candidates)==3 and all(i['object_id'] in {'branch0','branch1','branch2','branch3'} for i in candidates)
    assert all(i['name'] in task['result']['reply'] for i in candidates)
    assert city['client'].get(path(city,'/basket')).json()['items']==[]


@pytest.mark.parametrize('text',['不去鼓楼夜市了','鼓楼夜市不去了'])
def test_not_going_reply_patch_preserves_priority_and_rejects_unclear_choices(text):
    from app.city.exploration import reply_patch
    item={'key':'place:night','name':'鼓楼夜市','priority':'must','status':'pending'}
    with pytest.raises(HTTPException):reply_patch(text,[item],{})
    with pytest.raises(HTTPException):reply_patch('鼓楼夜市可能不去',[item],{})
    with pytest.raises(HTTPException):reply_patch(text,[item|{'status':'arranged'}],{})


@pytest.mark.parametrize('text',['不去鼓楼夜市了','鼓楼夜市不去了'])
@pytest.mark.parametrize('arranged',[False,True])
def test_not_going_only_proposes_standby_for_pending_material(reference_city,monkeypatch,text,arranged):
    from test_city_assistant import planned
    e=reference_city;c=e['client']
    if arranged:
        e['data']['requirements']['goals'][0]['target_id']='pl_wb_000013'
        # Reuse the fixture's routable place so formal-arrangement detection is exercised without new data.
        from app.city import facts
        from app.db import connect
        with connect() as db:
            night=facts.records(db)['place','museum'].model_copy(update={'name':'鼓楼夜市','version':2})
            facts.import_bundle(facts.FactBundle(records=(night,)),db,apply=True)
        e['data']['requirements']['goals'][0]['target_id']='museum'
        planned(e)
    identity='museum' if arranged else 'pl_wb_000013'
    assert c.patch(path(e,'/basket'),json={'request_id':rid(),'expected_version':0,'item':{'kind':'place','object_id':identity,'priority':'must'}}).status_code==200
    before=c.get(path(e,'/basket')).json()
    choices(monkeypatch,text,('鼓楼夜市','must','standby',text))
    task=run(e,text)
    if arranged:
        assert 'basket_proposal' not in task['result'] and "鼓楼夜市已在正式行程里，途中想跳过可以说'跳过鼓楼夜市'" in task['result']['reply']
    else:
        item=task['result']['basket_proposal']['items'][0]
        assert item['operation']=='standby' and item['object_id']==identity and item['priority']=='must'
    assert c.get(path(e,'/basket')).json()==before


def test_existing_same_priority_choice_remains_a_harmless_echo(city,monkeypatch):
    name='[合成测试]museum';text='想去'+name
    c=city['client'];assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,'item':{'kind':'place','object_id':'museum'}}).status_code==200
    before=c.get(path(city,'/basket')).json()
    choices(monkeypatch,text,(name,'want','pending',name))
    task=run(city,text)
    assert 'basket_proposal' not in task['result'] and c.get(path(city,'/basket')).json()==before


def test_existing_priority_change_also_waits_for_confirmation(city,monkeypatch):
    name='[合成测试]museum';text=name+'改为想去';c=city['client']
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,'item':{'kind':'place','object_id':'museum','priority':'must'}}).status_code==200
    choices(monkeypatch,text,(name,'want','pending',text))
    task=run(city,text)
    assert task['result']['basket_proposal']['items'][0]['priority']=='want'
    assert c.get(path(city,'/basket')).json()['items'][0]['priority']=='must'


def test_material_confirmation_uses_versioned_basket_api_then_saved_conditions(city,monkeypatch):
    from test_city_assistant import modify
    e=city;c=e['client']
    modify(e,{'operation':'save_draft','draft':{'business_date':'2030-05-01','starts_at':'2030-05-01T10:00:00+08:00',
        'ends_at':'2030-05-01T20:00:00+08:00','origin_place_id':'origin','destination_place_id':'hotel'}})
    name='[合成测试]museum';text='想去'+name;choices(monkeypatch,text,(name,'want','pending',text))
    task=run(e,text);proposal=task['result']['basket_proposal'];item=proposal['items'][0]
    mutation={'request_id':rid(),'expected_version':proposal['basket_version'],'item':{
        'kind':item['kind'],'object_id':item['object_id'],'priority':item['priority'],'source':'assistant'}}
    saved=c.patch(path(e,'/basket'),json=mutation);assert saved.status_code==200,saved.text
    assert c.patch(path(e,'/basket'),json=mutation|{'request_id':rid()}).status_code==409
    workspace=c.get(path(e,'/exploration/constraints')).json()
    preview=c.post(path(e,'/exploration/preview'),json={'request_id':rid(),'expected_version':get(e)['version'],
        'basket_version':saved.json()['version'],'planning_version':workspace['version'],'constraints':workspace['constraints']})
    assert preview.status_code==200 and preview.json()['status']=='feasible',preview.text
    assert saved.json()['items'][0]['priority']=='want' and get(e)['route_version']==0


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


def test_attitude_quote_uses_model_priority_and_still_requires_confirmation(city,monkeypatch):
    name='[合成测试]museum';text=name+'保留为一般想去就行'
    c=city['client']
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,'item':{'kind':'place','object_id':'museum','priority':'must'}}).status_code==200
    model(monkeypatch,text,planning={'quote':text,'choices':[{'name':name,'priority':'want','quote':text,'priority_stated':True}]})
    t=run(city,text)
    assert t['status']=='needs_confirmation' and t['result']['basket_proposal']['items'][0]['priority']=='want'
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


@pytest.mark.parametrize('time_fields,question,absent',[
    ([field('start_time','09:00','早上九点')],'最晚几点结束？','几点开始'),
    ([field('end_time','18:00','下午六点')],'当天几点开始？','最晚'),
    ([], '当天几点开始，最晚几点结束？',None),
])
def test_planning_asks_only_for_missing_times(city,monkeypatch,time_fields,question,absent):
    text='明天从[合成测试]origin出发'+('，早上九点' if time_fields and time_fields[0]['field']=='start_time' else
        '，下午六点结束' if time_fields else '')
    model(monkeypatch,text,[field('date','明天','明天'),field('origin','[合成测试]origin','[合成测试]origin'),*time_fields])
    task=run(city,text);reply=task['result']['reply']
    assert task['status']=='needs_clarification' and task['interpretation']['basis']=='kimi_validated'
    assert reply=='日期已记为 2030-05-02。'+question
    if absent:assert absent not in reply
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert bool(saved['starts_at'])==any(f['field']=='start_time' for f in time_fields)
    assert bool(saved['ends_at'])==any(f['field']=='end_time' for f in time_fields)


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
    # An unsupported item is now audited locally; no valid content still clarifies.
    monkeypatch.setattr(provider,'generate',lambda s,p,**k:({'actions':[{'kind':'modify','quote':'不在原话里'}]},{'source':'mock_model'}))
    t=run(city,'换个出发地点')
    assert {'path':'actions[0]','category':'A','reason':'ungrounded evidence quote','quote':'不在原话里'} in t['interpretation']['dropped']
    assert t['status']=='needs_clarification' and '没完全理解' in t['result']['reply']
    assert not t['interpretation'].get('validation_failure')


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


@pytest.mark.parametrize('text',['开封有啥特色小吃','想尝尝本地菜','推荐点开封小吃'])
def test_model_near_without_position_evidence_is_dropped_to_general_dining(city,monkeypatch,text):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{
        'kind':'search_dining','purpose':'dining','quote':text,'target_name':None,'near':True}]},
        {'source':'mock_model'}))
    task=run(city,text)
    assert task['understanding']['actions'][0]['near'] is False
    assert {'field':'near','check':'near_without_position'} in task['interpretation']['dropped_fields']
    assert task['status']=='answered' and '你现在在哪' not in task['result']['reply']


@pytest.mark.parametrize('text',['这附近有什么吃的','离我近的灌汤包店','就在这里找一家饭店'])
def test_model_near_with_position_evidence_still_requires_fresh_position(city,monkeypatch,text):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{
        'kind':'search_dining','purpose':'dining','quote':text,'target_name':None,'near':False}]},
        {'source':'mock_model'}))
    task=run(city,text)
    assert task['understanding']['actions'][0]['near'] is True
    assert task['result']['code']=='FRESH_POSITION_REQUIRED'


def test_near_drop_survives_dropped_service_fields(city,monkeypatch):
    text='想尝本地菜，再找个酒店'
    model(monkeypatch,text,planning=None,actions=[{'kind':'search_dining','quote':'想尝本地菜','near':True}],
        service_query={'quote':'找个酒店','fields':[{'field':'nights','value':2,'quote':'两晚'}]})
    intent,meta=understand.parse(text,snapshot(city)[0])
    assert not intent.actions[0].near
    assert {'field':'near','check':'near_without_position'} in meta['dropped_fields']
    assert {'field':'service.nights','check':'quote_not_in_request'} in meta['dropped_fields']


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


@pytest.mark.parametrize('text',['别把一天排得太满','行程不要安排得那么紧','不用把路线排得过于密集'])
def test_degree_negation_keeps_modelled_relaxed_pace(city,monkeypatch,text):
    model(monkeypatch,text,[field('pace','relaxed',text)])
    task=run(city,text)
    assert not task['interpretation'].get('validation_failure')
    assert city['client'].get(path(city,'/exploration/constraints')).json()['constraints']['pace']=='relaxed'
    assert '放松' in task['result']['reply']


@pytest.mark.parametrize('suffix,priority',[
    ('顺路去趟','must'),('路过看看','must'),('想去','optional'),
])
def test_existing_material_priority_change_requires_priority_stated(city,monkeypatch,suffix,priority):
    name='[合成测试]museum';text=name+suffix;c=city['client']
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum','priority':'want'}}).status_code==200
    before=c.get(path(city,'/basket')).json()
    choices(monkeypatch,text,(name,priority,'pending',text,False))
    task=run(city,text)
    assert 'basket_proposal' not in task['result']
    assert c.get(path(city,'/basket')).json()==before


@pytest.mark.parametrize('name,phrase',[('鼓楼夜市','必须去'),('民俗馆','这次一定要看'),('城楼','非去不可')])
def test_existing_material_explicit_priority_stated_creates_proposal(city,monkeypatch,name,phrase):
    from app.city import facts
    from app.db import connect
    with connect() as db:
        original=facts.records(db)['place','museum']
        facts.import_bundle(facts.FactBundle(records=(original.model_copy(update={'name':name,'version':original.version+1}),)),db,apply=True)
    text=name+phrase;c=city['client']
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum','priority':'want'}}).status_code==200
    choices(monkeypatch,text,(name,'must','pending',text,True))
    task=run(city,text)
    assert task['status']=='needs_confirmation' and task['result']['basket_proposal']['items'][0]['priority']=='must'


def test_endpoint_suffix_resolution_is_bounded_to_entrance_terms(city):
    from app.city.agent.planning_intent import _resolve_endpoint
    snap,_=snapshot(city);catalog=snap.planning.facts
    catalog['place','museum']=catalog['place','museum'].model_copy(update={'name':'开封府','aliases':()})
    for suffix in ('门口','门前','大门口','大门','正门','入口'):
        resolved,normalized=_resolve_endpoint(catalog,'开封府'+suffix)
        assert resolved.fact_id=='museum' and normalized
    for suffix in ('对面','旁边','附近','东门','西门'):
        resolved,normalized=_resolve_endpoint(catalog,'开封府'+suffix)
        assert resolved is None and not normalized


@pytest.mark.parametrize('slot,name,suffix,phrase',[
    ('origin','鼓楼','门口','在鼓楼门口碰头'),
    ('origin','清明上河园','大门','从清明上河园大门出发'),
    ('destination','开封府','门前','最后回开封府门前'),
])
def test_endpoint_suffix_reply_names_normalized_place(city,monkeypatch,slot,name,suffix,phrase):
    from app.db import connect
    from app.city import facts
    with connect() as db:
        original=facts.records(db)['place','museum']
        facts.import_bundle(facts.FactBundle(records=(original.model_copy(update={'name':name,'aliases':(),'version':original.version+1}),)),db,apply=True)
    text='明天，'+phrase
    model(monkeypatch,text,[field('date','明天','明天'),field(slot,name+suffix,phrase)])
    task=run(city,text)
    constraints=task['result']['planning_workflow']['result']['constraints']
    assert constraints[slot+'_place_id']=='museum'
    assert ('起点' if slot=='origin' else '终点')+'按'+name+'处理' in task['result']['reply']


def test_endpoint_suffix_prefers_exact_name_and_never_guesses_ambiguous_base(city):
    from app.city.agent.planning_intent import _resolve_endpoint
    snap,_=snapshot(city);catalog=snap.planning.facts;original=catalog['place','museum']
    catalog['place','museum']=original.model_copy(update={'name':'开封府','aliases':('府衙',)})
    assert _resolve_endpoint(catalog,'府衙大门口')[0].fact_id=='museum'
    gate=original.model_copy(update={'fact_id':'gate','name':'开封府入口'})
    catalog['place','gate']=gate
    assert _resolve_endpoint(catalog,'开封府入口')==(gate,False)
    # The stripped name resolves exactly like a typed name (place_by_name prefers the located record).
    catalog['place','duplicate']=original.model_copy(update={'fact_id':'duplicate','name':'开封府',
        'coordinate_system':'unknown','longitude':None,'latitude':None})
    assert _resolve_endpoint(catalog,'开封府门口')==(catalog['place','museum'],True)
    catalog['place','duplicate2']=catalog['place','museum'].model_copy(update={'fact_id':'duplicate2'})
    assert _resolve_endpoint(catalog,'开封府门口')==(None,False)
    assert _resolve_endpoint(catalog,'开封府对面')==(None,False)


@pytest.mark.parametrize('quote,value',[
    ('人均300',60000),('一人800',800),('五个人，每位两百',100000),
])
def test_misconverted_per_person_budget_is_only_explained(city,monkeypatch,quote,value):
    text='明天去开封，'+quote
    model(monkeypatch,text,[field('date','明天','明天'),field('budget_fen',value,quote)|{'budget_basis':'per_person'}])
    task=run(city,text)
    # B4.1: a per-person amount the model multiplied is not in the quote; only that item is dropped.
    assert task['interpretation']['dropped_fields']==[{'field':'budget_fen','check':'budget_not_in_quote'}]
    reply=task['result']['reply']
    assert '预算没听准' in reply and '按人预算暂时不能用于规划' not in reply and '格式无法核对' not in reply
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['budget_fen'] is None and saved['per_person_budget_fen'] is None


@pytest.mark.parametrize('quote,value',[('总共300元',60000),('两人合计800',800),('整趟五百块',100000)])
def test_total_budget_amount_mismatch_still_drops_the_field(city,monkeypatch,quote,value):
    text='明天，'+quote
    model(monkeypatch,text,[field('date','明天','明天'),field('budget_fen',value,quote)|{'budget_basis':'total'}])
    task=run(city,text)
    assert task['interpretation']['dropped_fields']==[{'field':'budget_fen','check':'budget_not_in_quote'}]
    assert '预算没听准' in task['result']['reply']
    assert city['client'].get(path(city,'/exploration/constraints')).json()['constraints']['budget_fen'] is None


@pytest.mark.parametrize('text',['脚疼，少安排点路','走不动了，后面少走些','腿脚不便，按体力排'])
@pytest.mark.parametrize('home',['fields','constraints'])
def test_less_walking_is_a_model_preference_with_grounded_quote(city,monkeypatch,text,home):
    entry=field('objective','less_walking',text)
    model(monkeypatch,text,[entry] if home=='fields' else [],
        **({'constraints':[entry|{'strength':'soft'}]} if home=='constraints' else {}))
    task=run(city,text)
    assert city['client'].get(path(city,'/exploration/constraints')).json()['constraints']['objective']=='less_walking'
    assert '少走路' in task['result']['reply']


@pytest.mark.parametrize('text',['走路就行','全程步行','不坐车，走路过去'])
def test_null_walking_bound_does_not_invent_less_walking(city,monkeypatch,text):
    model(monkeypatch,text,[field('mode','walking',text.split('，')[-1])],
        constraints=[field('walking_seconds',None,text)|{'strength':'soft'}])
    task=run(city,text)
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['objective']=='balanced' and saved['modes']==['walking']
    assert not task['understanding']['constraints'] and '少走路' not in task['result']['reply']


@pytest.mark.parametrize('phrase',['这次先不去了','就算了，留到下次','先放候补吧'])
def test_predeparture_material_choices_propose_standby_without_modifying_basket(city,monkeypatch,phrase):
    c=city['client'];name='[合成测试]museum';text=name+phrase
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum','priority':'must'}}).status_code==200
    before=c.get(path(city,'/basket')).json()
    choices(monkeypatch,text,(name,'want','standby',text,False))
    task=run(city,text);item=task['result']['basket_proposal']['items'][0]
    assert task['status']=='needs_confirmation' and item['operation']=='standby' and item['priority']=='must'
    assert c.get(path(city,'/basket')).json()==before


@pytest.mark.parametrize('phrase',['这次先不去了','就算了，留到下次','先放候补吧'])
def test_material_choices_do_not_move_an_official_stop_to_standby(city,monkeypatch,phrase):
    from test_city_assistant import planned
    planned(city);c=city['client'];name='[合成测试]museum';text=name+phrase
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum','priority':'must'}}).status_code==200
    before=c.get(path(city,'/basket')).json();trip_before=get(city)
    choices(monkeypatch,text,(name,'want','standby',text,False))
    task=run(city,text)
    assert 'basket_proposal' not in task['result'] and '已在正式行程里' in task['result']['reply']
    assert c.get(path(city,'/basket')).json()==before and get(city)==trip_before


@pytest.mark.parametrize('version',['3.1','3.2','3.3','3.4','3.5'])
@pytest.mark.parametrize('stated',[None,True])
def test_legacy_material_choices_default_priority_stated_to_false(version,stated):
    item={'name':'鼓楼','priority':'must','quote':'鼓楼必须去'}
    if stated is not None:item['priority_stated']=stated
    intent=Understanding.model_validate({'schema_version':'travel-intent/'+version,
        'planning':{'quote':'鼓楼必须去','choices':[item]}})
    assert intent.schema_version=='travel-intent/3.6' and intent.planning.choices[0].priority_stated is False


@pytest.mark.parametrize('priority',['must','want','optional'])
def test_new_material_still_uses_priority_without_priority_stated(city,monkeypatch,priority):
    name='[合成测试]museum';text='想去'+name
    choices(monkeypatch,text,(name,priority,'pending',text,False))
    task=run(city,text)
    assert task['result']['basket_proposal']['items'][0]['priority']==priority


def test_a8_model_field_descriptions_define_semantic_values():
    from app.city.agent.models import Understanding
    schema=Understanding.model_json_schema()
    planning=schema['$defs']['PlanningField']['properties']['value']['description']
    choice=schema['$defs']['MaterialChoice']['properties']['priority_stated']['description']
    assert 'objective=balanced均衡/less_walking少走路' in planning and '腿脚不便' in planning
    assert '明确优先级才true' in choice and '仅true改已有项' in choice


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
def test_multiday_request_asks_for_daily_time_and_overnight_before_preview(city,monkeypatch,duration):
    text='2030-05-03去开封'+duration
    days=3 if '三天' in duration or '3' in duration else 2
    quote='三天' if '三天' in duration else '3 天' if '3 天' in duration else '2天' if '2天' in duration else '两天'
    model(monkeypatch,text,[field('date','2030-05-03','2030-05-03'),field('trip_days',days,quote)])
    before=get(city);task=run(city,text)
    assert task['status']=='needs_clarification'
    assert '多天行程还在开发中' not in task['result']['reply']
    assert any(word in task['result']['reply'] for word in ('开始','结束','过夜','返回'))
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved is None or saved.get('schema_version')=='city-constraints/2'
    assert get(city)==before


def test_single_day_request_does_not_show_multiday_notice(city,monkeypatch):
    text='2030-05-03去开封玩一天'
    model(monkeypatch,text,[field('date','2030-05-03','2030-05-03')])
    assert '多天行程' not in run(city,text)['result']['reply']


@pytest.mark.parametrize('scope',['每人','人均','一人','每个人'])
@pytest.mark.parametrize('home',['fields','constraints'])
def test_per_person_budget_is_dropped_after_normalization(city,monkeypatch,scope,home):
    text='明天去开封，预算'+scope+'800元'
    fields=[field('date','明天','明天')];budget=field('budget_fen',80000,'预算'+scope+'800元')|{'budget_basis':'per_person'}
    extra={}
    if home=='constraints':extra['constraints']=[budget|{'strength':'hard'}]
    else:fields.append(budget)
    model(monkeypatch,text,fields,**extra);task=run(city,text)
    # B4.1: the per-person budget is kept as its own requirement, never as the solver's total.
    assert not any(d['field']=='budget_fen' for d in task['interpretation'].get('dropped_fields',()))
    reply=task['result']['reply']
    assert '预算按每人800元记下' in reply and '按人预算暂时不能用于规划' not in reply and '格式无法核对' not in reply
    assert '预算没听准' not in reply
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved.get('budget_fen') is None and saved['per_person_budget_fen']==80000 and saved['party_size'] is None


@pytest.mark.parametrize('quote',['预算每人800元','800元'])
def test_budget_basis_gate_is_explicit(quote):
    from datetime import date
    from app.city.agent.models import PlanningField
    from app.city.agent.planning_intent import _field_check
    assert _field_check(PlanningField(**(field('budget_fen',80000,quote)|{'budget_basis':'per_person'})),'预算每人800元',date(2026,10,26)) is None
    assert _field_check(PlanningField(**(field('budget_fen',160000,quote)|{'budget_basis':'per_person'})),'预算每人800元',date(2026,10,26))=='budget_not_in_quote'
    assert _field_check(PlanningField(**(field('budget_fen',80000,'800元')|{'budget_basis':'total'})),'总预算800元',date(2026,10,26)) is None


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


@pytest.mark.parametrize('text,target_name',[
    ('想吃开封特色美食',None),('有什么好吃的',None),('来点当地的招牌',None),
    ('给我推荐下吃的',None),('想找一家口碑不错的店',None),
    ('想吃开封特色美食','开封特色美食'),
])
@pytest.mark.parametrize('kind',['query','search_dining'])
def test_general_dining_lists_only_dishes_with_supply_records(city,monkeypatch,text,target_name,kind):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':kind,'purpose':'dining','quote':text,'target_name':target_name}]},{'source':'mock_model'}))
    before=get(city);task=run(city,text);result=task['result']
    assert task['status']=='answered' and '[合成测试]菜品可以去：[合成测试]restaurant' in result['reply']
    assert result['items'][0]['food_id']=='food' and result['items'][0]['vendor_id']=='vendor'
    assert '想去哪家可以加入素材篮' in result['reply'] and '你想问哪一场演出' not in result['reply']
    # A target without a record gets a note true for a missing dish and a generic phrase alike.
    assert '资料里暂时没有' not in result['reply']
    assert ('没有列出的菜暂时没有资料' in result['reply'])==bool(target_name)
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


def test_echoed_basket_choice_is_dropped_but_date_field_survives(city,monkeypatch):
    text='后天上午出发，帮我排一下路线'
    model(monkeypatch,text,planning={'quote':text,'fields':[field('date','后天','后天')],
        'choices':[{'name':'[合成测试]museum','priority':'must','status':'pending','quote':'[合成测试]museum'}]})
    snap,_=snapshot(city);intent,meta=understand.parse(text,snap)
    assert not intent.planning.choices
    assert {'field':'choice','check':'choice_not_in_quote'} in meta['dropped_fields']
    task=run(city,text)
    assert 'basket_proposal' not in task['result'] and task['result']['planning_workflow']['result']['constraints']['business_date']=='2030-05-03'
    assert 'choice' not in task['result']['reply'] and '没听准' not in task['result']['reply']


@pytest.mark.parametrize('kind',['cancel','modify'])
@pytest.mark.parametrize('in_basket',[True,False])
def test_place_labelled_cancel_or_modify_before_departure_becomes_standby_proposal(city,monkeypatch,kind,in_basket):
    # Claude review: real model output for "XX就算了" was {kind:cancel,target_name:<full name>}.
    c=city['client']
    if in_basket:
        assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
            'item':{'kind':'place','object_id':'museum','priority':'want'}}).status_code==200
    text='[合成测试]museum就算了，不去了'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':kind,'quote':text,'target_name':'[合成测试]museum'}]},{'source':'mock_model'}))
    before=c.get(path(city,'/basket')).json();trip=get(city);task=run(city,text)
    if in_basket:
        item=task['result']['basket_proposal']['items'][0]
        assert item['operation']=='standby' and item['object_id']=='museum'
    else:
        assert 'basket_proposal' not in task['result']
    assert c.get(path(city,'/basket')).json()==before and get(city)==trip


@pytest.mark.parametrize('operation',['skip','cancel'])
def test_pending_state_skip_becomes_standby_proposal_without_route_write(city,monkeypatch,operation):
    c=city['client'];assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum','priority':'must'}}).status_code==200
    text='先把[合成测试]museum这站放到候补吧'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'progress','quote':text}],
        'state_request':{'operation':operation,'quote':text,'target_name':'[合成测试]museum'}},{'source':'mock_model'}))
    before=get(city);task=run(city,text)
    proposal=task['result']['basket_proposal']['items'][0]
    assert proposal['operation']=='standby' and proposal['priority']=='must'
    assert c.get(path(city,'/basket')).json()['items'][0]['status']=='pending' and get(city)==before


def test_dish_wish_never_changes_a_basket_shop_whose_name_contains_the_dish(city,monkeypatch):
    # Real case: 第一楼 has the alias "第一楼灌汤包总店"; "想吃灌汤包" labelled must by the
    # model became "把第一楼改为必去" and blocked the demo plan. A dish served by a basket
    # shop is covered whatever priority the model gives it.
    from app.city import facts
    from app.db import connect
    with connect() as db:
        place=facts.records(db)['place','restaurant']
        facts.import_bundle(facts.FactBundle(records=(place.model_copy(update={'version':place.version+1,'aliases':(*place.aliases,'[合成测试]菜品总店')}),)),db,apply=True)
    c=city['client'];assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'vendor','object_id':'vendor','priority':'want'}}).status_code==200
    text='后天想吃[合成测试]菜品'
    for priority in ('must','want','optional'):
        choices(monkeypatch,text,('[合成测试]菜品',priority,'pending','想吃[合成测试]菜品'),fields=[field('date','后天','后天')])
        task=run(city,text)
        assert 'basket_proposal' not in task['result'] and '优先级' not in task['result']['reply'],(priority,task['result']['reply'])
        assert c.get(path(city,'/basket')).json()['items'][0]['priority']=='want'


def test_misplaced_service_and_budget_basis_fields_are_relocated_not_fatal(city,monkeypatch):
    # Live output for the pitch sentence (A6-A8b): hotel category/fields nested in the
    # search_services action and budget_basis on trip_days rejected the whole turn.
    text='周六去开封玩两天，想去[合成测试]museum。酒店要干净、有窗户'
    model(monkeypatch,text,[field('date','周六','周六'),{'field':'trip_days','value':2,'quote':'玩两天','budget_basis':'per_person'}],
        actions=[{'kind':'search_services','quote':'酒店要干净、有窗户','category':'hotels',
                  'fields':[{'field':'clean','value':True,'quote':'干净','strength':'hard'},
                            {'field':'window','value':True,'quote':'有窗户','strength':'hard'}]}])
    snap,_=snapshot(city);intent,meta=understand.parse(text,snap)
    assert intent.service_query and intent.service_query.category=='hotels'
    assert {f.field for f in intent.service_query.fields}=={'clean','window'}
    assert any(f.field=='trip_days' and f.budget_basis is None for f in intent.planning.fields)
    assert all(set(a.model_dump())<=set(understand.Action.model_fields) for a in intent.actions)


@pytest.mark.parametrize('text,quote,expect',[
    ('我准备后天和朋友去开封玩，想去[合成测试]museum，行程不要太赶','我准备后天和朋友去开封玩，想去[合成测试]museum','2030-05-03'),
    ('打算明天去开封转转，想去[合成测试]museum','打算明天去开封转转，想去[合成测试]museum','2030-05-02'),
])
def test_missing_date_is_recovered_from_the_planning_quote_by_the_date_grammar(city,monkeypatch,text,quote,expect):
    model(monkeypatch,text,planning={'quote':quote,'fields':[],'choices':[]})
    snap,_=snapshot(city);intent,meta=understand.parse(text,snap)
    assert [f.value for f in intent.planning.fields if f.field=='date']
    assert meta['recovered_fields']==[{'field':'date','source':'date_grammar'}]
    task=run(city,text)
    assert task['result']['planning_workflow']['result']['constraints']['business_date']==expect


@pytest.mark.parametrize('text,quote',[
    ('明天或者后天去开封，想去[合成测试]museum','明天或者后天去开封，想去[合成测试]museum'),   # two dates: never pick one
    ('想去[合成测试]museum','想去[合成测试]museum'),                                         # no date: still asked
])
def test_date_grammar_recovery_never_guesses(city,monkeypatch,text,quote):
    model(monkeypatch,text,planning={'quote':quote,'fields':[],'choices':[]})
    snap,_=snapshot(city);intent,meta=understand.parse(text,snap)
    assert not any(f.field=='date' for f in intent.planning.fields) and not meta.get('recovered_fields')


def departure_clock(monkeypatch,time):
    from test_live_model_replay import freeze
    freeze(monkeypatch,{'recorded_at':'2030-05-01T'+time+'+08:00'})


@pytest.mark.parametrize('text,end_time,end_quote,clause',[
    ('现在从开封府出发，下午五点回到开封府','17:00','下午五点','现在从开封府出发'),
    ('我现在就出发，从开封府走，晚上八点前回开封府','20:00','晚上八点','我现在就出发'),
    ('马上从开封府出发，六点前回来','18:00','六点前回来','马上从开封府出发'),
])
def test_now_departure_saves_today_rounded_start_and_recovery_evidence(reference_city,monkeypatch,text,end_time,end_quote,clause):
    departure_clock(monkeypatch,'16:18:44')
    model(monkeypatch,text,[field('origin','开封府','开封府'),field('destination','开封府','开封府'),
        field('end_time',end_time,end_quote)])
    before=get(reference_city);task=run(reference_city,text)
    saved=reference_city['client'].get(path(reference_city,'/exploration/constraints')).json()['constraints']
    assert saved['business_date']=='2030-05-01' and saved['starts_at']=='2030-05-01T16:20:00+08:00'
    assert saved['ends_at']==f'2030-05-01T{end_time}:00+08:00'
    assert saved['origin_place_id']==saved['destination_place_id']=='pl_wb_000003'
    assert '按现在出发：今天 16:20 开始。' in task['result']['reply']
    assert task['interpretation']['recovered_fields']==[
        {'field':f,'source':'now_departure','quote':clause} for f in ('date','start_time')]
    assert get(reference_city)==before


@pytest.mark.parametrize('time,expected',[
    ('16:18:44','16:20'),('16:19:30','16:25'),('16:19:00','16:20'),
    ('16:19:00.000001','16:25'),('16:20:00','16:25'),('23:58:00',None),
])
def test_now_departure_rounding_leaves_at_least_one_minute_and_stays_today(city,monkeypatch,time,expected):
    departure_clock(monkeypatch,time);text='现在出发，从开封府出发和返回'
    model(monkeypatch,text,[field('origin','开封府','开封府'),field('destination','开封府','开封府')])
    task=run(city,text);saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    if expected:
        assert saved['business_date']=='2030-05-01' and saved['starts_at']==f'2030-05-01T{expected}:00+08:00'
        assert f'按现在出发：今天 {expected} 开始。' in task['result']['reply']
    else:
        assert not saved or not saved['business_date'] and not saved['starts_at']
        assert '哪天' in task['result']['reply'] and '按现在出发' not in task['result']['reply']
        assert not task['interpretation'].get('recovered_fields')


@pytest.mark.parametrize('clause',[
    '现在就出发','马上从开封府走','立刻动身','立即出门','这就过去','这会儿启程','即刻从开封府出发',
    '现在'+'先'*20+'出发',
])
def test_now_departure_accepts_the_bounded_time_and_departure_class(city,monkeypatch,clause):
    departure_clock(monkeypatch,'16:18:44');model(monkeypatch,clause)
    task=run(city,clause)
    assert '按现在出发：今天 16:20 开始。' in task['result']['reply']
    assert city['client'].get(path(city,'/exploration/constraints')).json()['constraints']['starts_at']=='2030-05-01T16:20:00+08:00'


@pytest.mark.parametrize('clause',[
    '现在几点','现在人多吗','马上到了','现在'+'先'*21+'出发','从开封府出发，现在再看看',
    *('现在先看看'+separator+'从开封府出发' for separator in '，,。；;！!？?\n'),
])
def test_now_departure_does_not_infer_a_start_without_a_departure_in_the_same_clause(city,monkeypatch,clause):
    departure_clock(monkeypatch,'16:18:44');text=clause+'，从开封府出发和返回，晚上八点结束'
    model(monkeypatch,text,[field('origin','开封府','开封府'),field('destination','开封府','开封府'),field('end_time','20:00','晚上八点')])
    task=run(city,text)
    assert task['status']=='needs_clarification' and '你打算哪天去' in task['result']['reply']
    assert city['client'].get(path(city,'/exploration/constraints')).json()['version']==0
    assert not task['interpretation'].get('recovered_fields')


@pytest.mark.parametrize('explicit',[
    [field('date','明天','明天')],
    [field('start_time','17:00','下午五点')],
    [field('date','今天','今天'),field('start_time','17:00','下午五点')],
])
def test_now_departure_preserves_explicit_date_and_start_time(city,monkeypatch,explicit):
    departure_clock(monkeypatch,'16:18:44')
    text=('明天现在这个点出发' if len(explicit)==1 and explicit[0]['field']=='date' else
          ('今天' if len(explicit)==2 else '')+'现在先准备，下午五点马上出发')+'，从开封府出发和返回，晚上八点结束'
    model(monkeypatch,text,[*explicit,field('origin','开封府','开封府'),field('destination','开封府','开封府'),field('end_time','20:00','晚上八点')])
    task=run(city,text);saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert '按现在出发' not in task['result']['reply']
    assert not task['interpretation'].get('recovered_fields')
    if explicit[0]['field']=='start_time':
        assert '你打算哪天去' in task['result']['reply'] and saved is None
    else:
        assert saved['business_date']==('2030-05-02' if len(explicit)==1 else '2030-05-01')
        assert saved['starts_at']==(None if len(explicit)==1 else '2030-05-01T17:00:00+08:00')


@pytest.mark.parametrize('saved_date',['今天','明天'])
def test_now_departure_only_updates_an_empty_or_today_saved_date(city,monkeypatch,saved_date):
    departure_clock(monkeypatch,'16:18:44')
    text=saved_date+'上午九点到晚上八点'
    model(monkeypatch,text,[field('date',saved_date,saved_date),field('start_time','09:00','上午九点'),field('end_time','20:00','晚上八点')])
    run(city,text);before=city['client'].get(path(city,'/exploration/constraints')).json()
    text='我现在就出发';model(monkeypatch,text);task=run(city,text)
    saved=city['client'].get(path(city,'/exploration/constraints')).json()
    if saved_date=='明天':
        assert task['status']=='needs_clarification' and '已保存的日期是5月2日，现在出发是指今天吗？' in task['result']['reply']
        assert saved==before and not task['interpretation'].get('recovered_fields')
    else:
        assert saved['constraints']['starts_at']=='2030-05-01T16:20:00+08:00'
        assert '按现在出发' in task['result']['reply']


def test_now_departure_does_not_fill_multiday_times(city,monkeypatch):
    departure_clock(monkeypatch,'16:18:44');text='现在从开封府出发，玩两天'
    model(monkeypatch,text,[field('origin','开封府','开封府'),field('trip_days',2,'玩两天')])
    task=run(city,text);saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert '按现在出发' not in task['result']['reply'] and not task['interpretation'].get('recovered_fields')
    assert not saved or all(not day['starts_at'] for day in saved['days'])


@pytest.mark.parametrize('value',['now','现在'])
def test_now_departure_recovers_a_non_clock_model_start_time_without_losing_other_fields(reference_city,monkeypatch,value):
    departure_clock(monkeypatch,'16:19:30');text='现在从开封府出发，晚上八点前回开封府'
    model(monkeypatch,text,[field('origin','开封府','开封府'),field('destination','开封府','开封府'),
        field('end_time','20:00','晚上八点'),field('start_time',value,'现在')])
    task=run(reference_city,text);saved=reference_city['client'].get(path(reference_city,'/exploration/constraints')).json()['constraints']
    assert not task['interpretation'].get('validation_failure'),task
    assert saved['business_date']=='2030-05-01' and saved['starts_at']=='2030-05-01T16:25:00+08:00'
    assert saved['ends_at']=='2030-05-01T20:00:00+08:00'
    assert saved['origin_place_id']==saved['destination_place_id']=='pl_wb_000003'
    assert '按现在出发：今天 16:25 开始。' in task['result']['reply']
    # The dropped non-clock start is covered by the recovery, so no omission notice.
    assert '没采用' not in task['result']['reply']
    assert {f['field'] for f in task['interpretation']['recovered_fields']}=={'date','start_time'}


def test_now_departure_keeps_the_end_after_start_check(city,monkeypatch):
    departure_clock(monkeypatch,'16:19:30');text='现在从开封府出发，下午四点结束'
    model(monkeypatch,text,[field('origin','开封府','开封府'),field('end_time','16:00','下午四点')])
    task=run(city,text)
    assert task['status']=='needs_clarification' and '结束时间要晚于出发时间' in task['result']['reply']
    assert city['client'].get(path(city,'/exploration/constraints')).json()['version']==0


def test_named_dish_lookup_offers_food_basket_candidate(city,monkeypatch):
    text='哪家店能做[合成测试]菜品？'
    dining(monkeypatch,text,target_name='[合成测试]菜品')
    task=run(city,text);proposal=task['result']['basket_proposal']['items'][0]
    assert proposal['kind']=='food' and proposal['object_id']=='food' and proposal['priority']=='want'
    assert proposal['quote']==text


def test_duplicate_candidate_names_without_addresses_explain_data_ambiguity(city,monkeypatch):
    from app.city import facts
    from app.db import connect
    with connect() as db:
        original=facts.records(db)['place','museum']
        records=tuple(original.model_copy(update={'fact_id':f'same{i}','name':'同名园子','address':None,'aliases':()}) for i in range(2))
        facts.import_bundle(facts.FactBundle(records=records),db,apply=True)
    text='我想把同名园子也排进去';choices(monkeypatch,text,('同名园子','want','pending','同名园子'))
    task=run(city,text)
    assert '资料里有重复记录' in task['result']['reply']
    assert len(task['result']['basket_choices'][0]['candidates'])>=2


@pytest.mark.parametrize('mention,quote,slot',[
    ('从{name}出发','从{name}出发','origin'),
    ('最后回到{name}','最后回到{name}','destination'),
    ('路过{name}之后再出发','路过{name}','origin'),
    ('从{name}怎么走到住处','从{name}怎么走','origin'),
    ('起点在{name}附近','{name}','origin'),
    ('结束地点设在{name}门口','结束地点设在{name}','destination'),
])
@pytest.mark.parametrize('model_status',['pending','standby'])
def test_incidental_basket_mentions_never_change_priority_or_block_planning(city,monkeypatch,mention,quote,slot,model_status):
    from test_city_assistant import modify
    e=city;c=e['client'];name='[合成测试]museum'
    modify(e,{'operation':'save_draft','draft':{'business_date':'2030-05-01','starts_at':'2030-05-01T10:00:00+08:00',
        'ends_at':'2030-05-01T20:00:00+08:00','origin_place_id':'origin','destination_place_id':'hotel'}})
    assert c.patch(path(e,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum','priority':'want'}}).status_code==200
    before=c.get(path(e,'/basket')).json();mention=mention.format(name=name);quote=quote.format(name=name)
    text=mention+'，帮我安排今天的路线'
    choices(monkeypatch,text,(name,'must',model_status,quote),fields=[field(slot,name,mention),field('date','今天','今天')])
    task=run(e,text)
    assert task['mode']=='planning' and 'planning_workflow' in task['result'],task
    assert 'basket_proposal' not in task['result'] and '先确认素材的取舍' not in task['result']['reply']
    assert c.get(path(e,'/basket')).json()==before


@pytest.mark.parametrize('suffix,priority,status',[
    ('这次非它不可','must','pending'),('留着有空再逛','optional','pending'),('这回就不安排它了','want','standby'),
])
def test_existing_basket_attitude_creates_confirmable_change(city,monkeypatch,suffix,priority,status):
    name='[合成测试]museum';text=name+suffix;c=city['client']
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum','priority':'want'}}).status_code==200
    before=c.get(path(city,'/basket')).json();choices(monkeypatch,text,(name,priority,status,text))
    task=run(city,text);item=task['result']['basket_proposal']['items'][0]
    assert task['status']=='needs_confirmation' and item['priority']==priority
    assert item['operation']==('standby' if status=='standby' else 'put')
    assert c.get(path(city,'/basket')).json()==before


def test_new_material_inside_endpoint_quote_defaults_to_want(city,monkeypatch):
    name='[合成测试]museum';text='从'+name+'出发，帮我安排今天'
    choices(monkeypatch,text,(name,'must','standby','从'+name+'出发'),
        fields=[field('origin',name,'从'+name+'出发'),field('date','今天','今天')])
    task=run(city,text);item=task['result']['basket_proposal']['items'][0]
    assert item['operation']=='put' and item['priority']=='want'


def test_alias_only_echo_uses_fact_and_basket_names(city,monkeypatch):
    from app.city.agent import planning_intent
    from app.city.agent.models import MaterialChoice, PlanningIntent
    snap,_=snapshot(city);fact=snap.planning.facts['place','museum']
    fact=fact.model_copy(update={'aliases':('博物馆',)})
    snap.planning.facts['place','museum']=fact
    item={'name':'游客篮中名称','fact_kind':'place','fact_id':'museum'}
    for name,quote in ((fact.name,'博物馆'),('博物馆','游客篮中名称')):
        choice=MaterialChoice(name=name,quote=quote,priority='must')
        assert not planning_intent._choice_attitude(choice,PlanningIntent(quote=quote),[fact],[item],snap)


def test_vendor_names_use_place_and_tolerate_missing_place(city):
    from app.city.agent import planning_intent
    from app.city.agent.models import MaterialChoice, PlanningIntent
    snap,_=snapshot(city);vendor=snap.planning.facts['vendor','vendor'];place=snap.planning.facts['place',vendor.place_id]
    snap.planning.facts['place',place.fact_id]=place.model_copy(update={'aliases':('食府别名',)})
    item={'name':'游客篮中名称','fact_kind':'vendor','fact_id':vendor.fact_id}
    assert planning_intent._material_names(item,snap)=={'游客篮中名称',place.name,'食府别名'}
    choice=MaterialChoice(name=place.name,quote='食府别名',priority='must')
    assert not planning_intent._choice_attitude(choice,PlanningIntent(quote=choice.quote),[vendor],[item],snap)
    snap.planning.facts.pop(('place',place.fact_id))
    assert planning_intent._material_names(item,snap)=={'游客篮中名称'}


def test_duplicate_places_prefer_the_unique_canonical_record(city,monkeypatch):
    from app.city import facts
    from app.db import connect
    with connect() as db:
        original=facts.records(db)['place','museum']
        canonical=original.model_copy(update={'name':'规范园子','aliases':('园子别名',),'version':2,
            'coordinate_system':'WGS84','longitude':114.3,'latitude':34.8})
        duplicate=original.model_copy(update={'fact_id':'legacy_museum','name':'规范园子','aliases':('园子别名',),
            'coordinate_system':'unknown','longitude':None,'latitude':None})
        facts.import_bundle(facts.FactBundle(records=(canonical,duplicate)),db,apply=True)
    for name in ('规范园子','园子别名'):
        text='我想去'+name;choices(monkeypatch,text,(name,'want','pending',text))
        task=run(city,text)
        assert 'basket_choices' not in task['result']
        assert task['result']['basket_proposal']['items'][0]['object_id']=='museum'


@pytest.mark.parametrize('spoken,target',[
    ('鼓楼','鼓楼夜市'),('鼓楼夜市','鼓楼夜市'),('夜间小吃街','鼓楼夜市'),('鼓楼','鼓楼'),('夜间小吃街','夜间小吃街'),
])
@pytest.mark.parametrize('formal_route',[False,True])
def test_pending_skip_accepts_unique_partial_full_or_alias_before_state_grounding(city,monkeypatch,spoken,target,formal_route):
    from app.city import facts
    from app.db import connect
    from test_city_assistant import planned
    e=city;c=e['client']
    with connect() as db:
        original=facts.records(db)['place','museum']
        records=[original.model_copy(update={'name':'鼓楼夜市','aliases':('夜间小吃街',),'version':2})]
        if formal_route:
            records.append(original.model_copy(update={'fact_id':'outside_route','name':'鼓楼夜市','aliases':('夜间小吃街',)}))
        facts.import_bundle(facts.FactBundle(records=tuple(records)),db,apply=True)
    if formal_route:planned(e)
    identity='outside_route' if formal_route else 'museum'
    assert c.patch(path(e,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':identity,'priority':'must'}}).status_code==200
    text=spoken+'这次不去了';monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'progress','quote':text}],
        'state_request':{'operation':'skip','quote':text,'target_name':target}},{'source':'mock_model'}))
    before=get(e);basket_before=c.get(path(e,'/basket')).json();task=run(e,text)
    assert not task['interpretation'].get('validation_failure'),task
    item=task['result']['basket_proposal']['items'][0]
    assert task['status']=='needs_confirmation' and item['operation']=='standby' and item['object_id']==identity
    assert item['priority']=='must' and item['quote']==text
    assert get(e)==before and c.get(path(e,'/basket')).json()==basket_before


def test_pending_skip_ambiguous_basket_target_asks_instead_of_guessing(city,monkeypatch):
    from app.city import facts
    from app.db import connect
    with connect() as db:
        original=facts.records(db)['place','museum']
        facts.import_bundle(facts.FactBundle(records=tuple(original.model_copy(update={'fact_id':f'night{i}',
            'name':f'鼓楼夜市{i}店','aliases':('小吃街',)}) for i in range(2))),db,apply=True)
    for i in range(2):
        assert city['client'].patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':i,
            'item':{'kind':'place','object_id':f'night{i}'}}).status_code==200
    text='小吃街先不去了';monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'state_request':{
        'operation':'skip','quote':text,'target_name':'小吃街'}},{'source':'mock_model'}))
    before=get(city);task=run(city,text)
    assert task['status']=='needs_clarification' and all(f'鼓楼夜市{i}店' in task['result']['reply'] for i in range(2))
    assert not task['interpretation'].get('validation_failure') and 'basket_proposal' not in task['result']
    assert get(city)==before


@pytest.mark.parametrize('text',['这站先不去了','鼓这次不去了','博物馆这次不去了'])
def test_pending_skip_without_two_character_name_evidence_keeps_strict_gate(city,monkeypatch,text):
    c=city['client'];assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum'}}).status_code==200
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'state_request':{
        'operation':'skip','quote':text,'target_name':'[合成测试]museum'}},{'source':'mock_model'}))
    task=run(city,text)
    assert any(d['path']=='state_request' and d['category']=='A' and d['reason']=='state target absent' for d in task['interpretation']['dropped'])
    assert task['status']=='needs_clarification' and '这部分我没采用' in task['result']['reply']
    assert 'basket_proposal' not in task['result']


@pytest.mark.parametrize('spoken',['鼓楼','鼓楼夜市','夜间小吃街'])
def test_official_stop_skip_keeps_strict_target_evidence_and_state_confirmation(city,monkeypatch,spoken):
    from app.city import facts
    from app.db import connect
    from test_city_assistant import planned, modify
    with connect() as db:
        original=facts.records(db)['place','museum']
        facts.import_bundle(facts.FactBundle(records=(original.model_copy(update={
            'name':'鼓楼夜市','aliases':('夜间小吃街',),'version':2}),)),db,apply=True)
    planned(city);modify(city,{'operation':'transition','status':'ACTIVE'});c=city['client']
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum'}}).status_code==200
    text=spoken+'这次不去了';monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'state_request':{
        'operation':'skip','quote':text,'target_name':'鼓楼夜市'}},{'source':'mock_model'}))
    before=get(city);task=run(city,text)
    assert 'basket_proposal' not in task['result'] and get(city)==before
    # An alias or a short name that identifies one of today's remaining stops is evidence
    # (D4, 2026-10-27); nothing changes before the visitor confirms the full-name card.
    assert task['status']=='needs_confirmation' and '鼓楼夜市' in json.dumps(task['result'],ensure_ascii=False),task


@pytest.mark.parametrize('kind,identity,place_id,name,alias',[
    ('vendor','vendor','restaurant','宋味食府总店','宋味老店'),
    ('food','food',None,'传统花生酥糖','花生甜点'),
    ('place','museum',None,'汴梁民俗博物馆','民俗展馆'),
])
@pytest.mark.parametrize('mention',['partial','alias','full'])
@pytest.mark.parametrize('output',['state','planning'])
def test_material_skip_names_are_safe_for_vendor_food_and_place(city,monkeypatch,kind,identity,place_id,name,alias,mention,output):
    from app.city import facts
    from app.db import connect
    with connect() as db:
        original=facts.records(db)['place' if place_id else kind,place_id or identity]
        facts.import_bundle(facts.FactBundle(records=(original.model_copy(update={
            'name':name,'aliases':(alias,),'version':original.version+1}),)),db,apply=True)
    c=city['client']
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':kind,'object_id':identity,'priority':'must'}}).status_code==200
    spoken={'partial':name[:3],'alias':alias,'full':name}[mention];text=spoken+'这次不去了'
    if output=='planning':choices(monkeypatch,text,(spoken if mention=='partial' else name,'must','standby',text))
    else:
        monkeypatch.setattr(language,'available',lambda:True)
        monkeypatch.setattr(provider,'generate',lambda *a,**k:({'state_request':{
            'operation':'skip','quote':text,'target_name':name}},{'source':'mock_model'}))
    before=get(city);basket_before=c.get(path(city,'/basket')).json();task=run(city,text)
    assert not task['interpretation'].get('validation_failure'),task
    item=task['result']['basket_proposal']['items'][0]
    assert task['status']=='needs_confirmation' and item['operation']=='standby'
    assert (item['kind'],item['object_id'],item['priority'])==(kind,identity,'must')
    assert c.get(path(city,'/basket')).json()==basket_before and get(city)==before


@pytest.mark.parametrize('with_fields,confirmed_priority',[(True,'optional'),(False,'optional'),(True,'must')])
def test_existing_priority_confirmation_keeps_conditions_and_resumes_planning(city,monkeypatch,with_fields,confirmed_priority):
    from test_city_assistant import modify
    c=city['client'];name='[合成测试]museum'
    modify(city,{'operation':'save_draft','draft':{'business_date':'2030-04-30' if with_fields else '2030-05-01',
        'starts_at':('2030-04-30' if with_fields else '2030-05-01')+'T10:00:00+08:00',
        'ends_at':('2030-04-30' if with_fields else '2030-05-01')+'T20:00:00+08:00',
        'origin_place_id':'origin','destination_place_id':'hotel'}})
    assert c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':0,
        'item':{'kind':'place','object_id':'museum','priority':'must'}}).status_code==200
    quote=name+'有空再去就好';text=quote+('，今天上午9点到下午6点' if with_fields else '')
    fields=[field('date','今天','今天'),field('start_time','09:00','上午9点'),field('end_time','18:00','下午6点')] if with_fields else []
    choices(monkeypatch,text,(name,'optional','pending',quote),fields=fields)
    before=get(city);basket_before=c.get(path(city,'/basket')).json();task=run(city,text)
    assert task['status']=='needs_confirmation' and '调整'+name+'的优先级' in task['result']['reply']
    assert '先确认素材的取舍' not in task['result']['reply']
    proposal=task['result']['basket_proposal'];item=proposal['items'][0]
    assert item['operation']=='put' and item['priority']=='optional'
    workspace=c.get(path(city,'/exploration/constraints')).json();constraints=workspace['constraints']
    assert workspace['result'] is None and constraints==task['result']['planning_workflow']['result']['constraints']
    assert constraints['business_date']=='2030-05-01'
    assert constraints['starts_at']=='2030-05-01T'+('09:00' if with_fields else '10:00')+':00+08:00'
    assert constraints['ends_at']=='2030-05-01T'+('18:00' if with_fields else '20:00')+':00+08:00'
    assert c.get(path(city,'/basket')).json()==basket_before and get(city)==before
    saved=c.patch(path(city,'/basket'),json={'request_id':rid(),'expected_version':proposal['basket_version'],
        'item':{'kind':item['kind'],'object_id':item['object_id'],'priority':confirmed_priority,'source':'assistant'}})
    assert saved.status_code==200,saved.text
    assert saved.json()['items'][0]['priority']==confirmed_priority
    preview=c.post(path(city,'/exploration/preview'),json={'request_id':rid(),'expected_version':before['version'],
        'basket_version':saved.json()['version'],'planning_version':workspace['version'],'constraints':constraints})
    assert preview.status_code==200 and preview.json()['status']=='feasible',preview.text
    assert preview.json()['constraints']==constraints and get(city)==before


@pytest.mark.parametrize('home',['fields','constraints'])
@pytest.mark.parametrize('quote,value,basis',[
    ('一个人不超过200块',20000,'per_person'),
    ('我们俩每人500',50000,'per_person'),
    ('两个人一共600',60000,'total'),
    ('3个人合计735.50元',73550,'total'),
    ('四位游客总共九百块',90000,'total'),
    ('五人每位三百元',30000,'per_person'),
])
def test_budget_basis_survives_normalization_validation_storage_and_reply(city,monkeypatch,home,quote,value,basis):
    text='明天去开封，'+quote
    budget=field('budget_fen',value,quote)|{'budget_basis':basis}
    fields=[field('date','明天','明天')]
    extra={}
    if home=='constraints':extra['constraints']=[budget|{'strength':'hard'}]
    else:fields.append(budget)
    model(monkeypatch,text,fields,**extra);task=run(city,text)
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert task['interpretation']['basis']=='kimi_validated'
    assert '预算没听准' not in task['result']['reply']
    if basis=='total':
        assert saved['budget_fen']==value
        assert f'预算按总额{value/100:g}元理解' in task['result']['reply']
        assert not task['interpretation'].get('dropped_fields')
    else:
        assert saved['budget_fen'] is None and saved['per_person_budget_fen']==value
        assert f'预算按每人{value/100:g}元记下' in task['result']['reply']
        assert '按人预算暂时不能用于规划' not in task['result']['reply']
        assert not task['interpretation'].get('dropped_fields')
        # A count the visitor said converts; "一个人" only states the per-person basis.
        party={'一个人不超过200块':None,'我们俩每人500':2,'五人每位三百元':5}[quote]
        assert saved['party_size']==party
        if party:assert f'{party}人合计{value*party/100:g}元，按每人×{party}人换算' in task['result']['reply']
    assert get(city)['route_version']==0


@pytest.mark.parametrize('home',['fields','constraints'])
@pytest.mark.parametrize('basis',[{}, {'budget_basis':None}])
def test_missing_budget_basis_is_not_silently_saved_as_total(city,monkeypatch,home,basis):
    text='明天去开封，一个人不超过200块'
    budget=field('budget_fen',20000,'一个人不超过200块')|basis
    fields=[field('date','明天','明天')]
    extra={'schema_version':'travel-intent/3.4'}
    if home=='constraints':extra['constraints']=[budget|{'strength':'hard'}]
    else:fields.append(budget)
    model(monkeypatch,text,fields,**extra);task=run(city,text)
    assert task['interpretation']['dropped_fields']==[{'field':'budget_fen','check':'budget_basis_missing'}]
    assert city['client'].get(path(city,'/exploration/constraints')).json()['constraints']['budget_fen'] is None
    assert '这笔预算是所有人合计，还是每人？' in task['result']['reply']
    assert '预算没听准' not in task['result']['reply']


def test_per_person_request_does_not_claim_to_remove_a_saved_total_budget(city,monkeypatch):
    model(monkeypatch,'明天总预算900元',[field('date','明天','明天'),field('budget_fen',90000,'总预算900元')|{'budget_basis':'total'}])
    run(city,'明天总预算900元')
    text='我们俩每人500'
    model(monkeypatch,text,[field('budget_fen',50000,text)|{'budget_basis':'per_person'}])
    task=run(city,text)
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['budget_fen']==90000 and saved['per_person_budget_fen']==50000
    assert '预算按每人500元记下' in task['result']['reply'] and '先不按预算筛选' not in task['result']['reply']


def test_budget_scope_is_echoed_before_basket_confirmation(city,monkeypatch):
    text='明天去[合成测试]museum，两个人一共600'
    choices(monkeypatch,text,('[合成测试]museum','want','pending','去[合成测试]museum'),fields=[
        field('date','明天','明天'),field('budget_fen',60000,'两个人一共600')|{'budget_basis':'total'}])
    task=run(city,text)
    assert task['status']=='needs_confirmation' and '预算按总额600元理解' in task['result']['reply']
    assert city['client'].get(path(city,'/basket')).json()['items']==[]


@pytest.mark.parametrize('dropped_field',[
    *get_args(PlanningField.model_fields['field'].annotation),
    *get_args(Constraint.model_fields['field'].annotation),
    *('service.'+f for f in get_args(ServiceField.model_fields['field'].annotation)),
    'choice','future_unknown_field',
])
def test_dropped_fields_never_leak_program_names_in_questions(city,monkeypatch,dropped_field):
    text='明天去开封'
    model(monkeypatch,text,[field('date','明天','明天')])
    parse=understand.parse
    def dropped(*args,**kwargs):
        intent,meta=parse(*args,**kwargs)
        meta['dropped_fields']=[{'field':dropped_field,'check':'choice_not_in_quote' if dropped_field=='choice' else 'invalid_evidence'}]
        return intent,meta
    monkeypatch.setattr(understand,'parse',dropped)
    reply=run(city,text)['result']['reply']
    assert dropped_field not in reply
    if dropped_field=='choice' or dropped_field not in orchestration.LABELS:
        assert '没听准' not in reply


@pytest.mark.parametrize('markets,expect',[(('双桥夜市',),'proposal'),(('双桥夜市','西岸夜市'),'ask')])
def test_bare_cancel_takes_its_target_from_the_basket_item_the_quote_names(city,monkeypatch,markets,expect):
    # Live model output for "夜市不去了": a bare cancel with no target and no branch.
    before=named_basket(city,*[(f'short:{i}',n,()) for i,n in enumerate(markets)],('short:park','清风上河园',()))
    text='夜市不去了'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'cancel','quote':text}]},{'source':'mock_model'}))
    trip=get(city);task=run(city,text)
    if expect=='proposal':
        assert [(i['object_id'],i['operation']) for i in task['result']['basket_proposal']['items']]==[('short:0','standby')],task['result']
    else:
        assert 'basket_proposal' not in task['result'] and '夜市' in task['result']['reply'],task['result']
        assert '请明确状态操作及目标' not in task['result']['reply']
    assert city['client'].get(path(city,'/basket')).json()==before and get(city)==trip


def test_bare_cancel_naming_no_basket_item_stays_strict(city,monkeypatch):
    named_basket(city,('short:park','清风上河园',()))
    text='今天不去了'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'cancel','quote':text}]},{'source':'mock_model'}))
    task=run(city,text)
    assert 'basket_proposal' not in task['result'] and task['status']=='needs_clarification'


@pytest.mark.parametrize('text,quote', [('帮我排一个开封一日游','一日游'),('2030-05-03来开封一日游','一日游')])
def test_one_day_tour_is_a_duration_not_the_first_of_the_month(city,monkeypatch,text,quote):
    fields=[field('city','开封','开封'),{'field':'trip_days','value':1,'quote':quote}]
    if text.startswith('2030'):fields.insert(0,field('date','2030-05-03','2030-05-03'))
    model(monkeypatch,text,fields)
    reply=run(city,text)['result']['reply']
    assert '没采用' not in reply and '游玩天数没听准' not in reply, reply
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints'] or {}
    # No date was said in the first sentence; never invent the 1st of a month.
    assert saved.get('business_date') == ('2030-05-03' if text.startswith('2030') else None), saved
