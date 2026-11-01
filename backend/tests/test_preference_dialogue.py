"""Preference dialogue uses synthetic facts and model outputs, never live providers."""
import pytest
from app.city import exploration_state
from app.city.agent import understand
from app.db import connect
from test_city_assistant import city,path,get
from test_city_agent import snapshot,empty_tasks
from test_city_engine_api import engine
from test_exploration import collect,constraints
from test_multi_day_api import multi_engine
from test_multi_day_basket_goals import basket_trip
from test_planning_understanding import model,field
from test_quick_mode import _run
from test_trip_preferences import change_fact


QUESTION='开始排之前问一下：能吃辣吗？更想多安排演出、美食、夜景还是景区古迹，有没有不想安排的？直接说“都行”也可以。'


def saved(e):return e['client'].get(path(e,'/exploration/constraints')).json()['constraints']


def workspace(e,values):
    with connect() as db:exploration_state.save(db,e['trip']['id'],values,None,0)


def prepared(e,values=None,items=(('place','museum'),('place','garden'),('food','food'))):
    for kind,identity in items:
        response=collect(e['client'],e['trip'],kind,identity)
        assert response.status_code==200,response.text
    workspace(e,values or constraints(e['data']))


def run(e,monkeypatch,text='开始安排',fields=(),mode='deep',**extra):
    if not fields and not extra:extra={'planning':{'operation':'continue','quote':text,'fields':[]}}
    model(monkeypatch,text,fields,**extra)
    return _run(e,text,mode)


def workflow(task):return task['result']['planning_workflow']['result']


def test_deep_asks_both_once_before_any_preview(city,monkeypatch):
    prepared(city);before=get(city)
    task=run(city,monkeypatch)
    assert task['status']=='needs_clarification' and task['result']['reply']==QUESTION,task
    assert saved(city)['preferences_asked'] is True
    assert saved(city)['preferences_pending'] is True
    with connect() as db:assert db.execute('SELECT COUNT(*) FROM city_engine_candidates').fetchone()[0]==0
    assert get(city)==before


def test_spice_already_stated_only_asks_interests(city,monkeypatch):
    prepared(city)
    task=run(city,monkeypatch,'不吃辣',[field('spice','none','不吃辣')])
    assert task['result']['reply']==QUESTION.replace('能吃辣吗？',''),task
    assert saved(city)['preferences']['spice']=='none'


@pytest.mark.parametrize('kind,identity',[('food','food'),('vendor','vendor'),('place','restaurant')])
def test_known_categories_only_asks_spice_for_dining(city,monkeypatch,kind,identity):
    prepared(city,items=(('place','museum'),(kind,identity)))
    task=run(city,monkeypatch,'更喜欢古迹',[field('interest','scenic','更喜欢古迹')])
    assert task['result']['reply']=='开始排之前问一下：能吃辣吗？直接说“都行”也可以。',task


def test_answer_applies_preferences_then_previews_and_explains_exclusion(city,monkeypatch):
    prepared(city,items=(('place','museum'),('place','garden'),('food','food'),('show','s1')))
    run(city,monkeypatch,fields=[field('pace','standard','安排')])
    task=run(city,monkeypatch,'不想看演出，能吃辣',[
        field('avoid','shows','不想看演出'),field('spice','any','能吃辣')])
    assert workflow(task)['status']=='feasible',task
    assert saved(city)['preferences']=={'spice':'any','interests':[],'avoid':['shows']}
    assert '不想安排的类别：演出' in task['result']['reply']
    assert '你说过不想安排演出' in task['result']['reply']
    assert workflow(task)['unscheduled']['0'][0]['reason_code']=='preference_excluded'
    assert get(city)['route_version']==0


@pytest.mark.parametrize('multi',[False,True])
def test_all_excluded_reply_never_suggests_an_empty_basket(request,monkeypatch,multi):
    if multi:
        e,values=request.getfixturevalue('basket_trip');c,t,data=e;visitor={'client':c,'trip':t,'data':data}
    else:visitor=request.getfixturevalue('city');values=None
    prepared(visitor,values,items=(('food','food'),))
    run(visitor,monkeypatch)
    task=run(visitor,monkeypatch,'不安排美食',[field('avoid','food','不安排美食')])
    result=workflow(task)
    assert result['issues'][0]['code']=='PREFERENCE_EXCLUDED_ALL',task
    assert result['reply'] in task['result']['reply']
    assert '先从详情页加入' not in task['result']['reply']
    assert '素材篮里的' in task['result']['reply']


@pytest.mark.parametrize('text,fields,spice,avoid,interests',[
    ('晚上就不出去了',[field('avoid','night','晚上就不出去了')],None,['night'],[]),
    ('入夜以后我宁可待在住处',[field('avoid','night','入夜以后我宁可待在住处')],None,['night'],[]),
    ('夜市就免了',[field('avoid','night','夜市就免了')],None,['night'],[]),
    ('辣的不行',[field('spice','none','辣的不行')],'none',[],[]),
    ('我比较怕辣',[field('spice','none','我比较怕辣')],'none',[],[]),
    ('吃不了辣，别给我排吃的了，多去几个景点',[
        field('spice','none','吃不了辣'),field('avoid','food','别给我排吃的了'),
        field('interest','scenic','多去几个景点')],'none',['food'],['scenic']),
])
def test_pending_colloquial_preferences_pass_grounding_and_are_saved(city,monkeypatch,text,fields,spice,avoid,interests):
    prepared(city);run(city,monkeypatch)
    before=saved(city)
    calls=model(monkeypatch,text,fields)
    intent,meta=understand.parse(text,snapshot(city)[0])
    assert calls[0]['context']['planning_readiness']['awaiting']=='preferences'
    assert [f.model_dump(include={'field','value','quote'}) for f in intent.planning.fields]==fields
    assert not intent.constraints and not meta.get('dropped'),meta
    task=_run(city,text,'deep')
    assert workflow(task)['status']=='feasible',task
    assert not task['interpretation'].get('preference_fallback')
    assert not task['interpretation'].get('dropped'),task['interpretation']
    values=saved(city)
    assert values['preferences']=={'spice':spice,'interests':interests,'avoid':avoid}
    assert values['ends_at']==before['ends_at'] and values['preferences_pending'] is False
    assert get(city)['route_version']==0


def test_pending_colloquial_preferences_survive_misplacement_duplicates_and_chat(city,monkeypatch):
    prepared(city);run(city,monkeypatch)
    text='辣的我受不了，夜里就待在住处吧'
    spice=field('spice','none','辣的我受不了')
    model(monkeypatch,text,[dict(spice,extra='ignored'),spice],
        constraints=[field('avoid','night','夜里就待在住处吧')],conversation={'reply':'明白了'})
    task=_run(city,text,'deep')
    assert workflow(task)['status']=='feasible',task
    assert saved(city)['preferences']=={'spice':'none','interests':[],'avoid':['night']}
    assert not task['interpretation'].get('preference_fallback')


@pytest.mark.parametrize('text',['景点古迹少安排点','演出别太多','夜景安排少一点'])
def test_pending_reduced_category_answer_does_not_exclude_or_increase(city,monkeypatch,text):
    prepared(city);run(city,monkeypatch)
    before=saved(city)
    model(monkeypatch,text,actions=[],planning={'quote':text,'fields':[]})
    task=_run(city,text,'deep')
    assert workflow(task)['status']=='feasible',task
    assert saved(city)['preferences']==before['preferences']
    assert not any(v['reason_code']=='preference_excluded' for items in workflow(task)['unscheduled'].values() for v in items)


@pytest.mark.parametrize('text,fields',[
    ('都行',[field('spice','any','都行')]),
    ('换成轻松点',[field('pace','relaxed','轻松点')]),
])
def test_once_asked_any_answer_or_unrelated_condition_previews(city,monkeypatch,text,fields):
    prepared(city);run(city,monkeypatch)
    task=run(city,monkeypatch,text,fields)
    assert workflow(task)['status']=='feasible' and '开始排之前' not in task['result']['reply'],task
    assert saved(city)['preferences_asked'] is True


def test_quick_never_asks_but_applies_spice(city,monkeypatch):
    prepared(city);change_fact('vendor','vendor',spiciness='hot')
    task=run(city,monkeypatch,'不吃辣',[field('spice','none','不吃辣')],mode='quick')
    assert workflow(task)['status']=='feasible' and '开始排之前' not in task['result']['reply'],task
    assert saved(city)['preferences']['spice']=='none'
    assert '不吃辣' in task['result']['reply'] and '口味偏辣' in task['result']['reply']


@pytest.mark.parametrize('items,fields',[
    ((('place','museum'),),[]),
    ((('place','museum'),('place','garden'),('food','food')),[field('spice','none','不吃辣'),field('interest','scenic','爱古迹')]),
    ((('place','museum'),('place','garden')),[field('interest',None,'都行'),field('avoid',None,'都行')]),
])
def test_complete_preferences_or_single_scenic_do_not_ask(city,monkeypatch,items,fields):
    prepared(city,items=items)
    task=run(city,monkeypatch,'不吃辣，爱古迹，都行，开始安排',fields or [field('pace','standard','安排')])
    assert workflow(task)['status']=='feasible' and '开始排之前' not in task['result']['reply'],task


def test_required_conditions_take_priority_and_do_not_mark_asked(city,monkeypatch):
    prepared(city,values={})
    workspace(city,{'origin_place_id':'origin','destination_place_id':'hotel'})
    task=run(city,monkeypatch,fields=[field('pace','standard','安排')])
    assert '哪天' in task['result']['reply'] and not saved(city)['preferences_asked']


def test_multiday_same_question_then_preview_explains_night_exclusion(basket_trip,monkeypatch):
    e,values=basket_trip;c,t,data=e;visitor={'client':c,'trip':t,'data':data}
    change_fact('place','garden',place_type='night_visit')
    prepared(visitor,values)
    first=run(visitor,monkeypatch)
    assert first['result']['reply']==QUESTION,first
    task=run(visitor,monkeypatch,'不想安排夜游，辣的也能吃',[
        field('avoid','night','不想安排夜游'),field('spice','any','辣的也能吃')])
    result=workflow(task)
    assert result['status']=='preview',task
    assert saved(visitor)['preferences']=={'spice':'any','interests':[],'avoid':['night']}
    reply=task['result']['reply']
    assert reply.startswith('多天方案已准备好') and '按你的偏好没有安排' in reply
    assert '[合成测试]garden（你说过不想安排夜游）' in reply
    assert get(visitor)['route_version']==0


@pytest.mark.parametrize('multi',[False,True])
def test_merge_keeps_unmentioned_and_later_category_wins(basket_trip,monkeypatch,multi):
    e,values=basket_trip;c,t,data=e;visitor={'client':c,'trip':t,'data':data}
    if not multi:values=constraints(data)
    values.update(preferences={'spice':'mild','interests':['food','scenic'],'avoid':['night']},preferences_asked=True)
    prepared(visitor,values)
    text='演出多来点，古迹先不安排，夜里想逛逛，演出还是不看了'
    fields=[field('avoid','shows','演出还是不看了'),field('interest','shows','演出多来点'),
        field('avoid','scenic','古迹先不安排'),field('interest','night','夜里想逛逛')]
    task=run(visitor,monkeypatch,text,fields)
    assert workflow(task)['status'] in ('feasible','preview'),task
    assert saved(visitor)['preferences']=={'spice':'mild','interests':['food','night'],'avoid':['scenic','shows']}


def test_grounding_drops_only_invented_avoid_and_bad_enum(city,monkeypatch):
    prepared(city)
    text='口味不限，多点美食'
    fields=[field('spice','any','口味不限'),field('interest','food','多点美食'),
        field('avoid','night','别排夜景'),field('avoid','other','多点美食')]
    model(monkeypatch,text,fields)
    intent,meta=understand.parse(text,snapshot(city)[0])
    assert not meta.get('validation_failure'),meta
    assert [(f.field,f.value) for f in intent.planning.fields]==[('spice','any'),('interest','food')]
    assert any(d['field']=='avoid' and d['check']=='quote_not_in_request' for d in meta['dropped_fields'])
    assert len(meta['dropped'])>=2
    task=_run(city,text,'deep')
    assert workflow(task)['status']=='feasible' and saved(city)['preferences']['avoid']==[],task
    assert '不想安排的类别条件没听准，本次未采用' in task['result']['reply']


def test_misplaced_repeated_fields_and_chat_do_not_erase_preferences(city,monkeypatch):
    prepared(city)
    text='不吃辣，喜欢美食和夜景，不想安排演出'
    fields=[field('spice','none','不吃辣'),field('interest','food','喜欢美食和夜景'),
        field('interest','night','喜欢美食和夜景'),field('avoid','shows','不想安排演出')]
    model(monkeypatch,text,fields=[dict(fields[0],extra='ignored'),fields[0]],
        constraints=fields[1:],conversation={'reply':'好的'})
    task=_run(city,text,'deep')
    assert workflow(task)['status']=='feasible',task
    assert saved(city)['preferences']=={'spice':'none','interests':['food','night'],'avoid':['shows']}


def test_full_first_turn_with_negative_preference_preserves_dates_and_times(city,monkeypatch):
    for kind,identity in (('place','museum'),('place','garden'),('food','food')):
        collect(city['client'],city['trip'],kind,identity)
    text='2030-05-01上午9点到晚上8点，从[合成测试]origin出发，回[合成测试]hotel，不吃辣，不想安排夜游'
    task=run(city,monkeypatch,text,[field('date','2030-05-01','2030-05-01'),
        field('start_time','09:00','上午9点'),field('end_time','20:00','晚上8点'),
        field('origin','[合成测试]origin','[合成测试]origin'),field('destination','[合成测试]hotel','[合成测试]hotel'),
        field('spice','none','不吃辣'),field('avoid','night','不想安排夜游')])
    assert workflow(task)['status']=='feasible',task
    assert saved(city)['starts_at']=='2030-05-01T09:00:00+08:00'
    assert saved(city)['preferences']['avoid']==['night']


def test_spice_correction_uses_spoken_order(city,monkeypatch):
    prepared(city)
    task=run(city,monkeypatch,'不能吃辣，改成微辣可以，偏爱美食',[
        field('spice','mild','微辣可以'),field('spice','none','不能吃辣'),field('interest','food','偏爱美食')])
    assert workflow(task)['status']=='feasible' and saved(city)['preferences']['spice']=='mild',task


def test_misplaced_empty_category_answer_is_relocated_and_previews(city,monkeypatch):
    prepared(city);run(city,monkeypatch)
    task=run(city,monkeypatch,'都行',constraints=[field('interest',None,'都行'),field('avoid',None,'都行')],
        planning={'quote':'都行','fields':[]})
    assert workflow(task)['status']=='feasible',task
    assert saved(city)['preferences']=={'spice':None,'interests':[],'avoid':[]}
    assert saved(city)['preferences_asked'] is True


@pytest.mark.parametrize('text,quote',[
    ('如果不想安排夜游会怎样','不想安排夜游'),
    ('商家原文：“不想安排夜游”','不想安排夜游'),
])
def test_hypothetical_or_external_preference_cannot_write(city,monkeypatch,text,quote):
    prepared(city);before=saved(city)
    run(city,monkeypatch,text,[field('avoid','night',quote)])
    assert saved(city)==before


def test_single_to_multi_keeps_preferences_and_asked_marker(city,monkeypatch):
    prepared(city)
    values=saved(city);values.update(preferences={'spice':'mild','interests':['food'],'avoid':['night']},preferences_asked=True)
    workspace(city,values)
    task=run(city,monkeypatch,'玩两天',[field('trip_days',2,'两天')])
    assert saved(city)['schema_version']=='city-constraints/2',task
    assert saved(city)['preferences']==values['preferences'] and saved(city)['preferences_asked'] is True


def test_pending_clarify_falls_back_to_default_preview_and_is_consumed(city,monkeypatch):
    prepared(city)
    run(city,monkeypatch)
    calls=[]
    def clarify(system,payload,**kwargs):
        calls.append(payload)
        return {'actions':[],'planning':None,'unresolved':['这句话我没完全理解']},{'source':'mock_model'}
    monkeypatch.setattr(understand.language,'available',lambda:True)
    monkeypatch.setattr(understand.provider,'generate',clarify)
    task=_run(city,'随你安排','deep')
    assert workflow(task)['status']=='feasible',task
    assert task['result']['reply'].startswith('没有听出具体偏好，先按默认安排；之后想多安排或不想安排什么，随时告诉我。')
    assert saved(city)['preferences_pending'] is False
    assert calls[0]['context']['planning_readiness'].get('awaiting')=='preferences'


def test_pending_answer_keeps_restaurant_search_and_exposes_awaiting(city,monkeypatch):
    prepared(city)
    run(city,monkeypatch)
    calls=[]
    def search(system,payload,**kwargs):
        calls.append(payload)
        return {'actions':[{'kind':'search_dining','quote':'多找几家吃饭的店'}],'planning':None},{'source':'mock_model'}
    monkeypatch.setattr(understand.language,'available',lambda:True)
    monkeypatch.setattr(understand.provider,'generate',search)
    task=_run(city,'多找几家吃饭的店','deep')
    assert task['status'] in ('answered','needs_data','needs_clarification'),task
    assert saved(city)['preferences_pending'] is False
    assert calls[0]['context']['planning_readiness']['awaiting']=='preferences'


@pytest.mark.parametrize('value',['hot','spicy'])
def test_hot_spice_value_is_normalized_to_any(city,monkeypatch,value):
    prepared(city)
    run(city,monkeypatch)
    task=run(city,monkeypatch,'很能吃辣',[field('spice',value,'很能吃辣')])
    assert workflow(task)['status']=='feasible',task
    assert saved(city)['preferences']['spice']=='any'


def test_dropped_preference_quote_overlapping_adopted_quote_is_silent(city,monkeypatch):
    prepared(city)
    values=saved(city);values['preferences_asked']=True;workspace(city,values)
    text='多安排美食'
    model(monkeypatch,text,[field('interest','food',text),field('avoid','other',text)])
    task=_run(city,text,'deep')
    assert workflow(task)['status']=='feasible',task
    assert '没采用' not in task['result']['reply']


def test_dropped_preference_quote_without_overlap_is_reported(city,monkeypatch):
    prepared(city)
    values=saved(city);values['preferences_asked']=True;workspace(city,values)
    text='多安排美食，别排夜游'
    model(monkeypatch,text,[field('interest','food','多安排美食'),field('avoid','other','别排夜游')])
    task=_run(city,text,'deep')
    assert workflow(task)['status']=='feasible',task
    assert '没采用' in task['result']['reply']


@pytest.mark.parametrize('output',[
    {'actions':[{'kind':'modify','quote':'照你安排'}],'unresolved':['这句话我没完全理解']},
    {'conversation':{'reply':'好的'}},
    {'actions':[{'kind':'modify','quote':'照你安排'}]},
    {'planning':{'quote':'照你安排','fields':[]}},
    {'planning':{'quote':'照你安排','fields':[field('end_time','20:00','照你安排')]}},
])
def test_pending_without_executable_understanding_previews_saved_conditions(city,monkeypatch,output):
    prepared(city);run(city,monkeypatch)
    before=saved(city)
    model(monkeypatch,'照你安排',**{'actions':[],'planning':None,**output})
    task=_run(city,'照你安排','deep')
    assert workflow(task)['status']=='feasible',task
    assert task['result']['reply'].startswith('没有听出具体偏好，先按默认安排；')
    assert saved(city)==dict(before,preferences_pending=False)


def test_pending_payload_is_only_present_on_the_answer_turn(city,monkeypatch):
    prepared(city)
    first=model(monkeypatch,'开始安排',planning={'operation':'continue','quote':'开始安排'})
    task=_run(city,'开始安排','deep')
    assert task['result']['reply']==QUESTION
    assert 'awaiting' not in first[0]['context']['planning_readiness']
    second=model(monkeypatch,'没有特别要求',actions=[],planning=None,unresolved=['没理解'])
    task=_run(city,'没有特别要求','deep')
    assert workflow(task)['status']=='feasible',task
    assert second[0]['context']['planning_readiness']['awaiting']=='preferences'
    third=model(monkeypatch,'继续安排',planning={'operation':'continue','quote':'继续安排'})
    task=_run(city,'继续安排','deep')
    assert workflow(task)['status']=='feasible',task
    assert 'awaiting' not in third[0]['context']['planning_readiness']
    assert not saved(city)['preferences_pending'] and '开始排之前' not in task['result']['reply']


def test_pending_multiday_clarify_previews_without_changing_preferences(basket_trip,monkeypatch):
    e,values=basket_trip;c,t,data=e;visitor={'client':c,'trip':t,'data':data}
    prepared(visitor,values);run(visitor,monkeypatch)
    task=run(visitor,monkeypatch,'随意',actions=[],planning=None,unresolved=['没理解'])
    assert workflow(task)['status']=='preview',task
    assert task['result']['reply'].startswith('没有听出具体偏好，先按默认安排；')
    assert saved(visitor)['preferences_pending'] is False


def test_pending_is_consumed_even_if_understanding_provider_fails(city,monkeypatch):
    from fastapi import HTTPException
    prepared(city);run(city,monkeypatch)
    def failed(*args,**kwargs):
        raise HTTPException(429,detail={'code':'MODEL_REQUEST_BUDGET','http_attempts':0})
    monkeypatch.setattr(understand.provider,'generate',failed)
    task=_run(city,'随意','deep')
    assert task['status']=='budget_exhausted',task
    assert saved(city)['preferences_pending'] is False


def test_consuming_pending_does_not_overwrite_concurrent_conditions(city,monkeypatch):
    prepared(city);run(city,monkeypatch)
    updated=dict(saved(city),pace='compact')
    def changed(*args,**kwargs):
        workspace(city,updated)
        return {'unresolved':['没理解']},{'source':'mock_model'}
    monkeypatch.setattr(understand.provider,'generate',changed)
    task=_run(city,'随意','deep')
    assert task['status']=='stale',task
    assert saved(city)==updated


def test_consuming_pending_keeps_concurrent_basket_change_stale(city,monkeypatch):
    prepared(city);run(city,monkeypatch)
    def changed(*args,**kwargs):
        response=collect(city['client'],city['trip'],'show','s1')
        assert response.status_code==200,response.text
        return {'unresolved':['没理解']},{'source':'mock_model'}
    monkeypatch.setattr(understand.provider,'generate',changed)
    task=_run(city,'随意','deep')
    assert task['status']=='stale',task
    assert saved(city)['preferences_pending'] is False
    with connect() as db:assert db.execute('SELECT COUNT(*) FROM city_engine_candidates').fetchone()[0]==0


@pytest.mark.parametrize('adopted,dropped',[
    ('不吃辣','不吃辣'),('不吃辣','我不吃辣'),('我不吃辣','不吃辣'),
])
def test_invented_food_does_not_contradict_adopted_spice(city,monkeypatch,adopted,dropped):
    prepared(city)
    text='我不吃辣，偏爱古迹'
    task=run(city,monkeypatch,text,[field('spice','none',adopted),field('interest','scenic','偏爱古迹')],
        constraints=[{'field':'food','strength':'hard','value':'虚构菜品','quote':dropped}])
    assert workflow(task)['status']=='feasible',task
    assert saved(city)['preferences']['spice']=='none'
    assert any(d['reason']=='duplicate_constraint' for d in task['interpretation']['dropped'])
    assert '没采用' not in task['result']['reply']


@pytest.mark.parametrize('overlap',[False,True])
def test_grounded_preference_drop_only_reports_uncovered_quote(city,monkeypatch,overlap):
    prepared(city)
    values=saved(city);values['preferences_asked']=True;workspace(city,values)
    text='偏爱古迹，不想夜游'
    task=run(city,monkeypatch,text,[field('interest','scenic','偏爱古迹'),
        field('end_time','20:00','偏爱古迹' if overlap else '不想夜游')])
    assert workflow(task)['status']=='feasible',task
    assert ('没采用' in task['result']['reply']) is not overlap


@pytest.mark.parametrize('name',['interest','avoid'])
@pytest.mark.parametrize('value,expected',[
    ('shows night',['shows','night']),('shows,night',['shows','night']),('shows，night',['shows','night']),
    ('shows、night',['shows','night']),('shows/night',['shows','night']),('shows\t\nnight',['shows','night']),
    ('shows;night',['shows','night']),('shows；food',['shows','food']),('night|scenic',['night','scenic']),
    (['shows','night'],['shows','night']),(['shows/night'],['shows','night']),
    (['shows','night,food/scenic'],['shows','night','food','scenic']),
])
@pytest.mark.parametrize('misplaced',[False,True])
def test_multi_category_values_are_split_before_validation(city,monkeypatch,name,value,expected,misplaced):
    prepared(city)
    text='演出和夜游都别安排，其他随意' if name=='avoid' else '演出和夜游都多排点，其他随意'
    fields=[field(name,value,text)]
    model(monkeypatch,text,[] if misplaced else fields,**({'constraints':fields} if misplaced else {}))
    intent,meta=understand.parse(text,snapshot(city)[0])
    assert [(f.field,f.value,f.quote) for f in intent.planning.fields]==[(name,v,text) for v in expected]
    assert not meta.get('dropped'),meta
    task=_run(city,text,'deep')
    assert saved(city)['preferences']['avoid' if name=='avoid' else 'interests']==expected,task


@pytest.mark.parametrize('name',['interest','avoid'])
@pytest.mark.parametrize('value',['shows,xyz','shows;夜市',['shows','xyz'],['shows,xyz'],['shows',None],['shows','']])
def test_invalid_multi_category_value_drops_the_whole_item(city,monkeypatch,name,value):
    prepared(city)
    text='不吃辣，演出和其他都随意'
    task=run(city,monkeypatch,text,[field('spice','none','不吃辣'),field(name,value,'演出和其他都随意')])
    assert saved(city)['preferences']=={'spice':'none','interests':[],'avoid':[]},task
    assert '没采用' in task['result']['reply']
    assert any(d['path']=='planning.fields[1]' for d in task['interpretation']['dropped'])


@pytest.mark.parametrize('spice_quote,food_quote',[
    ('不能吃辣','不能吃辣'),('不能吃辣','我不能吃辣'),('我不能吃辣','不能吃辣'),
    ('很能吃辣','很能吃辣'),('口味不限','口味不限'),
])
def test_food_quoting_spice_is_removed_without_dish_advice(city,monkeypatch,spice_quote,food_quote):
    prepared(city)
    text='我不能吃辣，很能吃辣，口味不限'
    value='none' if '不能' in spice_quote else 'any'
    model(monkeypatch,text,[field('spice',value,spice_quote)],
        constraints=[{'field':'food','strength':'hard','value':food_quote,'quote':food_quote}])
    intent,meta=understand.parse(text,snapshot(city)[0])
    assert not intent.constraints and not meta.get('validation_failure'),meta
    task=_run(city,text,'deep')
    assert saved(city)['preferences']['spice']==value,task
    assert any(d['path']=='constraints[0]' and d['reason']=='duplicate_constraint' for d in task['interpretation']['dropped'])
    assert '想吃' not in task['result']['reply'] and '把有这道菜的店加进素材篮' not in task['result']['reply']
    assert '能吃辣吗' not in task['result']['reply']


@pytest.mark.parametrize('with_spice',[False,True])
def test_separate_dish_constraint_is_preserved(city,monkeypatch,with_spice):
    prepared(city)
    text='不吃辣，想吃灌汤包'
    fields=[field('spice','none','不吃辣')] if with_spice else [field('interest','scenic','古迹多排点')]
    text+='，古迹多排点'
    food={'field':'food','strength':'soft','value':'灌汤包','quote':'想吃灌汤包'}
    model(monkeypatch,text,fields,constraints=[food])
    intent,meta=understand.parse(text,snapshot(city)[0])
    assert [c.model_dump() for c in intent.constraints]==[food]
    assert not meta.get('dropped'),meta
    task=_run(city,text,'deep')
    assert '想吃灌汤包：把有这道菜的店加进素材篮' in task['result']['reply']
    assert saved(city)['preferences']['spice']==('none' if with_spice else None)


def test_food_only_spice_words_keep_existing_behavior(city,monkeypatch):
    prepared(city)
    text='不能吃辣，古迹多排点'
    food={'field':'food','strength':'hard','value':'不能吃辣','quote':'不能吃辣'}
    model(monkeypatch,text,[field('interest','scenic','古迹多排点')],constraints=[food])
    intent,meta=understand.parse(text,snapshot(city)[0])
    assert [c.model_dump() for c in intent.constraints]==[food]
    assert not meta.get('dropped'),meta
    task=_run(city,text,'deep')
    assert saved(city)['preferences']['spice'] is None
    assert '想吃不能吃辣：把有这道菜的店加进素材篮' in task['result']['reply']
