"""A10.2: replay malformed model output without any live model/provider calls."""
import copy
import json
import httpx
import pytest
from fastapi import HTTPException
from app import language
from app.city.agent import understand,provider,shows,service
from app.city.agent.models import Action,Constraint,Understanding
from test_city_assistant import city,get,path,planned
from test_city_agent import run,snapshot,empty_tasks
from test_agent_provider import configured


READ='看看行程'
QUERY={'kind':'query','purpose':'trip','quote':READ}


def emit(monkeypatch,raw):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model','http_attempts':0}))


def parse(city,monkeypatch,raw,text):
    emit(monkeypatch,raw)
    return understand.parse(text,snapshot(city)[0])


def dropped(meta,path=None,category=None,reason=None):
    entries=meta.get('dropped',[])
    assert entries,meta
    assert all({'path','category','reason'}<=set(d) and d['category'] in ('A','B','C') for d in entries)
    matches=[d for d in entries if (path is None or d['path']==path) and (category is None or d['category']==category) and (reason is None or reason in d['reason'])]
    assert matches,(path,category,reason,entries)
    return matches


@pytest.mark.parametrize('branch,bad',[
    ('actions',[{'kind':'planning.choices','quote':'额外要求'}]),
    ('actions',[{'kind':'execute_sql','quote':'额外要求'}]),
    ('actions',[{'kind':'query','quote':None}]),
    ('constraints',[{'field':'pace','value':[],'strength':'hard','quote':'额外要求'}]),
    ('constraints',[{'field':'unknown','value':'x','strength':'hard','quote':'额外要求'}]),
    ('corrections',[{'before':'旧值','after':[],'quote':'额外要求'}]),
    ('planning',{'operation':'unknown','quote':'额外要求','fields':[]}),
    ('planning',{'quote':7,'fields':[]}),
    ('service_query',{'category':'lodging','quote':'额外要求'}),
    ('weather_query',{'quote':[]} ),
    ('route_query',{'quote':'额外要求','origin':'甲','destination':'乙','mode':'flying'}),
    ('state_request',{'operation':'unknown','quote':'额外要求'}),
    ('conversation',{'reply':None}),
])
def test_bad_item_or_branch_preserves_the_independent_query(city,monkeypatch,branch,bad):
    raw={'actions':[QUERY]}
    if branch=='actions':raw['actions']+=bad
    else:raw[branch]=bad
    before=get(city);emit(monkeypatch,raw);task=run(city,READ+'，额外要求')
    assert task['mode']=='direct' and task['status'] in ('answered','needs_data'),task
    assert task['understanding']['actions']==[Action.model_validate(QUERY).model_dump(mode='json')]
    dropped(task['interpretation'])
    assert not task['interpretation'].get('validation_failure') and get(city)==before
    if branch in ('actions','constraints','corrections','planning','service_query','route_query','state_request') and '额外要求' in json.dumps(bad,ensure_ascii=False):
        assert '这部分我没采用：『额外要求』' in task['result']['reply']


@pytest.mark.parametrize('raw',[None,[],{},'text',{'actions':[{'kind':'unknown','quote':'额外要求'}]},
    {'constraints':[{'field':'pace','value':[],'strength':'hard','quote':'额外要求'}]},
    {'conversation':{'reply':' \n '}}, {'planning':{'operation':'unknown','quote':'额外要求'}}])
def test_only_invalid_content_still_asks_to_rephrase(city,monkeypatch,raw):
    before=get(city);emit(monkeypatch,raw);task=run(city,'额外要求')
    assert task['status']=='needs_clarification' and '没完全理解' in task['result']['reply']
    assert task['metrics']['tools']==0 and not task['understanding']['actions'] and get(city)==before


@pytest.mark.parametrize('branch', ['actions','constraints','corrections','planning','service_query','weather_query','route_query','state_request','conversation'])
@pytest.mark.parametrize('value',[[],42,'wrong'])
def test_wrong_container_types_are_isolated(city,monkeypatch,branch,value):
    raw={'actions':[QUERY]}
    if branch=='actions':raw['actions']=[QUERY,value]
    else:raw[branch]=value
    intent,meta=parse(city,monkeypatch,raw,READ)
    assert any(a.purpose=='trip' for a in intent.actions)
    if value!=[] or branch not in ('constraints','corrections'):dropped(meta)


def test_misplaced_hotel_fields_merge_with_existing_query_and_a_bad_field_is_local(city,monkeypatch):
    hotel='查酒店，要干净和有窗户，额外要求'
    raw={'actions':[{'kind':'search_services','quote':hotel,'category':'hotels','fields':[
        {'field':'window','value':True,'quote':'有窗户'}, {'field':'unknown','value':True,'quote':'额外要求'}]}],
        'service_query':{'quote':'查酒店，要干净','fields':[{'field':'clean','value':True,'quote':'干净'}]}}
    emit(monkeypatch,raw);task=run(city,hotel)
    assert task['mode']=='travel_services' and task['status']=='needs_clarification'
    req=task['result']['services']['requirements']
    assert set(req['attributes'])=={'clean','window'}
    dropped(task['interpretation'],'service_query.fields[2]','C')
    assert '这部分我没采用：『额外要求』' in task['result']['reply']
    assert 'unknown' not in req and task['interpretation']['relocated']


def test_unknown_service_category_never_becomes_default_hotels(city,monkeypatch):
    raw={'actions':[QUERY,{'kind':'search_services','quote':'额外要求'}],
        'service_query':{'quote':'额外要求','category':'anything'}}
    intent,meta=parse(city,monkeypatch,raw,READ+'，额外要求')
    assert intent.service_query is None and [a.kind for a in intent.actions]==['query']
    dropped(meta,'service_query','C')


def test_planning_fields_and_choices_validate_independently_and_rebuild_action_index(city,monkeypatch):
    text='明天出发，额外要求，想去[合成测试]museum'
    raw={'actions':[{'kind':'planning.choices','quote':text}], 'planning':{'quote':text,'fields':[
        {'field':'date','value':'明天','quote':'明天'}, {'field':'pace','value':[],'quote':'额外要求'},
        {'field':'origin','value':'甲','quote':None}], 'choices':[
        {'name':'[合成测试]museum','priority':'want','quote':'想去[合成测试]museum'},
        {'name':'编造','priority':'unknown','quote':'额外要求'}]}}
    emit(monkeypatch,raw);task=run(city,text)
    assert task['status']=='needs_confirmation' and len(task['result']['basket_proposal']['items'])==1
    assert task['understanding']['planning']['fields'][0]['field']=='date'
    assert [a['kind'] for a in task['understanding']['actions']]==['modify']
    dropped(task['interpretation'],'planning.fields[1]','C')
    dropped(task['interpretation'],'planning.choices[1]','C')
    assert '这部分我没采用：『额外要求』' in task['result']['reply']


@pytest.mark.parametrize('source,item,category',[
    ('actions',{'kind':'modify','quote':'并不存在的原文'},'A'),
    ('constraints',{'field':'return_by','value':'18:00','strength':'hard','quote':'截至十点'},'A'),
    ('constraints',{'field':'food','value':'不存在的菜','strength':'hard','quote':'想吃点心'},'A'),
    ('constraints',{'field':'walking_seconds','value':600,'strength':'hard','quote':'走路限五分钟'},'A'),
    ('constraints',{'field':'pace','value':'relaxed','strength':'soft','quote':'假如改得悠闲些'},'B'),
    ('actions',{'kind':'query','quote':'查天气','purpose':'trip'},'B'),
    ('corrections',{'before':'甲','after':'乙','quote':'假如改得悠闲些'},'B'),
])
def test_item_evidence_errors_are_not_whole_turn_errors(city,monkeypatch,source,item,category):
    monkeypatch.setattr(understand,'rules',lambda *a:Understanding())
    text=READ+'。截至十点，想吃点心，走路限五分钟。假如改得悠闲些。不要查天气。'
    raw={'actions':[QUERY]};raw.setdefault(source,[]).append(item)
    before=get(city);emit(monkeypatch,raw);task=run(city,text)
    assert task['status'] in ('answered','needs_data') and task['mode']=='direct' and get(city)==before,task
    dropped(task['interpretation'],category=category)
    assert task['metrics']['tools']==0
    if item['quote'] in text:assert '这部分我没采用：『'+item['quote']+'』' in task['result']['reply']


@pytest.mark.parametrize('quote',['不要把日期改成明天','假如把日期改成明天','请解释“把日期改成明天”'])
def test_rejected_planning_authorization_preserves_safe_conversation(city,monkeypatch,quote):
    raw={'planning':{'quote':quote,'fields':[{'field':'date','value':'明天','quote':'明天'}]},'conversation':{'reply':'可以先了解操作流程。'}}
    before=get(city);emit(monkeypatch,raw);task=run(city,quote)
    assert task['status']=='answered' and task['mode']=='conversation'
    assert task['understanding']['planning'] is None and not task['understanding']['actions']
    dropped(task['interpretation'],'planning','B')
    assert '这部分我没采用' in task['result']['reply'] and get(city)==before


def test_bad_planning_value_does_not_hide_a_valid_value_of_the_same_field(city,monkeypatch):
    text='明天出发，从[合成测试]origin出发'
    raw={'planning':{'quote':text,'fields':[{'field':'date','value':'明天','quote':'明天'},
        {'field':'date','value':'后天','quote':'明天'},
        {'field':'origin','value':'[合成测试]origin','quote':'从[合成测试]origin出发'}]}}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert [f.value for f in intent.planning.fields if f.field=='date']==['明天']
    assert not meta.get('planning_conflicts')
    dropped(meta,'planning.fields[1]','A')


@pytest.mark.parametrize('reverse',[False,True])
def test_two_valid_planning_values_only_ask_that_field_and_save_other_fields(city,monkeypatch,reverse):
    text='明天或后天出发，从[合成测试]origin出发'
    dates=[{'field':'date','value':v,'quote':v} for v in ('明天','后天')]
    raw={'planning':{'quote':text,'fields':[*dates[::(-1 if reverse else 1)],{'field':'origin','value':'[合成测试]origin','quote':'从[合成测试]origin出发'}]}}
    emit(monkeypatch,raw);task=run(city,text)
    assert task['status']=='needs_clarification' and '你说的是' in task['result']['reply']
    assert '明天' in task['result']['reply'] and '后天' in task['result']['reply']
    assert '几点' not in task['result']['reply'] and '从哪里' not in task['result']['reply']
    saved=city['client'].get(path(city,'/exploration/constraints')).json()['constraints']
    assert saved['origin_place_id']=='origin' and saved['business_date'] is None
    assert not any(f['field']=='date' for f in task['understanding']['planning']['fields'])
    dropped(task['interpretation'],reason='conflicting_values')


def test_continue_with_conditions_becomes_update_without_losing_conditions(city,monkeypatch):
    text='开始安排，明天出发'
    emit(monkeypatch,{'planning':{'operation':'continue','quote':text,'fields':[{'field':'date','value':'明天','quote':'明天'}]}})
    task=run(city,text)
    assert task['understanding']['planning']['operation']=='update'
    assert task['result']['planning_workflow']['result']['constraints']['business_date']=='2030-05-02'
    dropped(task['interpretation'],'planning.operation','C','continue_with_conditions')


@pytest.mark.parametrize('reply',[' \n ','一条说明。'*400],ids=['blank','long'])
def test_empty_or_long_chat_does_not_block_the_business_query(city,monkeypatch,reply):
    emit(monkeypatch,{'actions':[QUERY],'conversation':{'reply':reply}})
    task=run(city,READ)
    assert any(a['kind']=='query' for a in task['understanding']['actions']) and task['status']!='unavailable'
    dropped(task['interpretation'],'conversation.reply','C')
    assert '这部分我没采用' not in task['result']['reply']
    chat=task['understanding']['conversation']
    if reply.strip():assert len(chat['reply'])<=1200 and chat['reply'].endswith('。')
    else:assert chat is None


@pytest.mark.parametrize('key,limit', [('actions',8),('constraints',16),('corrections',4),('planning.fields',12),('planning.choices',12),('service_query.fields',16)])
def test_exact_duplicates_are_removed_before_capacity_checks(city,monkeypatch,key,limit):
    text=READ+'，明天，轻松一点，想去[合成测试]museum，酒店要干净'
    entries={'actions':QUERY,'constraints':{'field':'pace','value':'relaxed','strength':'soft','quote':'轻松一点'},
        'corrections':{'before':'','after':'明天','quote':'明天'},'planning.fields':{'field':'date','value':'明天','quote':'明天'},
        'planning.choices':{'name':'[合成测试]museum','priority':'want','quote':'想去[合成测试]museum'},
        'service_query.fields':{'field':'clean','value':True,'quote':'干净'}}
    raw={'actions':[QUERY]}
    if '.' in key:
        branch,field=key.split('.');raw[branch]={'quote':text,field:[entries[key]]*(limit+2)}
    else:raw[key]=[entries[key]]*(limit+2)
    intent,meta=parse(city,monkeypatch,raw,text)
    assert not intent.unresolved and not meta.get('validation_failure'),meta
    value=getattr(intent,key) if '.' not in key else getattr(getattr(intent,key.split('.')[0]),key.split('.')[1])
    assert len(value)==1
    dropped(meta,reason='duplicate_item')


def test_real_capacity_overflow_does_not_truncate_hard_constraints(city,monkeypatch):
    entries=[{'field':'food','value':f'点心{i}','quote':f'想吃点心{i}','strength':'hard'} for i in range(17)]
    text=READ+'，'+'，'.join(c['quote'] for c in entries)
    emit(monkeypatch,{'actions':[QUERY],'constraints':entries})
    task=run(city,text)
    assert task['status']=='needs_clarification' and '要求太多' in task['result']['reply']
    assert not task['understanding']['actions'] and not task['understanding']['constraints'] and task['metrics']['tools']==0


@pytest.mark.parametrize('companion',['position','conversation','query','weather','route'])
def test_state_can_share_restatement_explanation_and_reads(city,monkeypatch,companion):
    text='完成[合成测试]museum';raw={'state_request':{'operation':'complete','quote':text,'target_name':'[合成测试]museum'}}
    if companion=='position':raw['actions']=[{'kind':'position','quote':text}]
    if companion=='conversation':raw['conversation']={'reply':'先核对这次进度操作。'}
    if companion=='query':raw['actions']=[QUERY];text+='。'+READ
    if companion=='weather':raw['weather_query']={'quote':'查天气'};text+='。查天气'
    if companion=='route':raw['route_query']={'quote':'从甲到乙怎么走','origin':'甲','destination':'乙'};text+='。从甲到乙怎么走'
    intent,meta=parse(city,monkeypatch,raw,text)
    assert intent.state_request and intent.state_request.operation=='complete',meta
    assert not intent.unresolved
    if companion=='position':
        assert [a.kind for a in intent.actions]==['progress'];dropped(meta,reason='duplicate_index')
    else:assert understand.route(intent,snapshot(city)[0])[0]=='compound'


def test_state_and_query_both_reach_their_existing_executor(city,monkeypatch):
    planned(city);name='[合成测试]museum';text='完成'+name+'。'+READ
    emit(monkeypatch,{'state_request':{'operation':'complete','quote':'完成'+name,'target_name':name},'actions':[QUERY]})
    before=get(city);task=run(city,text)
    assert task['mode']=='compound' and task['result'].get('answers')
    assert any(i['kind']=='planning' for i in task['result']['compound']['items'])
    assert get(city)==before


@pytest.mark.parametrize('other',['planning','constraint','different_state_action'])
def test_distinct_writes_are_not_merged_but_a_query_can_continue(city,monkeypatch,other):
    text='完成[合成测试]museum。明天安排。当前位置在[合成测试]hotel。'+READ
    raw={'state_request':{'operation':'complete','quote':'完成[合成测试]museum','target_name':'[合成测试]museum'},'actions':[QUERY]}
    if other=='planning':raw['planning']={'quote':'明天安排','fields':[{'field':'date','value':'明天','quote':'明天'}]}
    if other=='constraint':raw['constraints']=[{'field':'pace','value':'relaxed','strength':'soft','quote':'明天安排'}]
    if other=='different_state_action':raw['actions'].append({'kind':'position','quote':'当前位置在[合成测试]hotel','target_name':'[合成测试]hotel'})
    before=get(city);emit(monkeypatch,raw);task=run(city,text)
    assert task['status']=='needs_clarification' and '先告诉我要做哪一个' in task['result']['reply']
    assert task['understanding']['state_request'] is None and task['understanding']['planning'] is None
    assert task['result'].get('answers') and get(city)==before
    dropped(task['interpretation'],'state_request','B','multiple_writes')


def test_inapplicable_state_fields_are_dropped_without_erasing_state(city,monkeypatch):
    text='完成[合成测试]museum'
    intent,meta=parse(city,monkeypatch,{'state_request':{'operation':'complete','quote':text,
        'target_name':'[合成测试]museum','minutes':20,'restore_version':3}},text)
    assert intent.state_request and intent.state_request.minutes is None and intent.state_request.restore_version is None
    dropped(meta,'state_request.minutes','C','not_applicable');dropped(meta,'state_request.restore_version','C','not_applicable')
    assert not understand.dropped_reply(intent,meta,text)


@pytest.mark.parametrize('state,reason',[
    ({'operation':'rest','minutes':30,'quote':'休息十分钟'},'duration_not_in_quote'),
    ({'operation':'rest','quote':'想休息一下'},'duration_required'),
    ({'operation':'restore','restore_version':7,'quote':'恢复第2版'},'restore version absent'),
    ({'operation':'complete','quote':'完成甲地','target_name':'乙地'},'state target absent'),
])
def test_ungrounded_state_item_never_executes_and_query_survives(city,monkeypatch,state,reason):
    text=state['quote']+'。'+READ
    emit(monkeypatch,{'state_request':state,'actions':[QUERY]});before=get(city);task=run(city,text)
    assert task['understanding']['state_request'] is None and get(city)==before
    assert task['result'].get('answers') or task['mode']=='direct'
    dropped(task['interpretation'],'state_request','A',reason)
    assert '这部分我没采用' in task['result']['reply']


def test_invalid_actual_start_only_drops_the_time(city,monkeypatch):
    text='完成[合成测试]museum，九点开始'
    intent,meta=parse(city,monkeypatch,{'state_request':{'operation':'complete','quote':text,
        'target_name':'[合成测试]museum','start_time':'15:00','start_quote':'九点开始'}},text)
    assert intent.state_request and intent.state_request.start_time is None
    dropped(meta,'state_request.start_time','A','invented state start')


def test_show_invalid_date_is_local_to_that_read_query(city,monkeypatch):
    snap,_=snapshot(city)
    result=shows.query(Action(kind='query',purpose='shows',quote='2030-02-30演出几点'),snap)
    assert result['status']=='needs_clarification' and '日期' in result['reply']
    emit(monkeypatch,{'actions':[QUERY,{'kind':'query','purpose':'shows','quote':'2030-02-30演出几点'}]})
    task=run(city,READ+'。2030-02-30演出几点')
    assert len(task['result']['answers'])==2 and any(a['status']=='needs_clarification' for a in task['result']['answers'])


@pytest.mark.parametrize('content',[
    '我来整理一下。{"actions": []}', '```json\n{"actions": []}\n```',
    '说明文字\n{"actions": [], "conversation":{"reply":"包含 {花括号} 的字符串"}}\n以上是结果。',
])
def test_single_complete_json_object_can_have_a_wrapper(content):
    assert isinstance(provider.output_object(content),dict)


@pytest.mark.parametrize('content',['{} {}','说明 {} 然后 {}','{} {','{ "x": {}','[{}]','null','说明文字','{} }'])
def test_ambiguous_or_incomplete_json_wrapper_stops(content):
    with pytest.raises(ValueError):provider.output_object(content)


@pytest.mark.parametrize('wrapper',[True,False])
def test_provider_wrapper_replay_uses_mock_transport_and_reports_output_format(city,configured,monkeypatch,wrapper):
    original=httpx.Client
    content='我先整理一下。'+json.dumps({'actions':[QUERY]},ensure_ascii=False) if wrapper else '{} {}'
    def response(request):return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':content}}]})
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(**kw,transport=httpx.MockTransport(response)))
    monkeypatch.setattr(language,'available',lambda:True)
    task=run(city,READ)
    if wrapper:assert task['status'] in ('answered','needs_data') and task['mode']=='direct'
    else:
        assert task['status']=='unavailable' and task['result']['code']=='MODEL_OUTPUT_INVALID'
        assert '输出格式' in task['result']['reply'] and '连接中断' not in task['result']['reply']


@pytest.mark.parametrize('key,limit',[('actions',8),('constraints',16),('corrections',4),('unresolved',8),
    ('planning.fields',12),('planning.choices',12),('service_query.fields',16)])
def test_distinct_items_over_capacity_always_stop_instead_of_truncating(city,monkeypatch,key,limit):
    quotes=[f'要求{i}' for i in range(limit+1)];text='，'.join(quotes)
    fields={'actions':lambda q:{'kind':'query','purpose':'trip','quote':q},
        'constraints':lambda q:{'field':'pace','value':'relaxed','strength':'hard','quote':q},
        'corrections':lambda q:{'before':'','after':q,'quote':q},'unresolved':lambda q:q,
        'planning.fields':lambda q:{'field':'city','value':q,'quote':q},
        'planning.choices':lambda q:{'name':q,'priority':'must','quote':q},
        'service_query.fields':lambda q:{'field':'room_type','value':q,'quote':q}}
    raw={};entries=[fields[key](q) for q in quotes]
    if '.' in key:
        branch,field=key.split('.');raw[branch]={'quote':text,field:entries}
    else:raw[key]=entries
    intent,meta=parse(city,monkeypatch,raw,text)
    assert not intent.actions and not intent.constraints and '要求太多' in intent.unresolved[0]
    dropped(meta,key,'C','capacity_exceeded')


def test_pending_constraint_merge_deduplicates_before_enforcing_capacity(city):
    snap,_=snapshot(city)
    constraints=tuple(Constraint(field='food',value='点心'+str(i),strength='hard',quote='点心'+str(i)) for i in range(10))
    previous=Understanding(actions=(Action(kind='modify',quote='调整需求'),),constraints=constraints)
    snap.pending.append({'status':'valid','action':{'operation':'assistant_v3_intent','understanding':previous.model_dump(mode='json')}})
    # The legacy pending record can contain exact copies; never discard a hard condition to fit.
    snap.pending[-1]['action']['understanding']['constraints']=[c.model_dump(mode='json') for c in (*constraints,*constraints[:6])]
    current=Understanding(actions=(Action(kind='modify',quote='调整需求'),),constraints=(
        Constraint(field='pace',value='relaxed',strength='soft',quote='放松些'),))
    merged=service._merge(current,snap)
    assert not merged.unresolved and len(merged.constraints)==11
    assert all(c in merged.constraints for c in constraints)
    additional=tuple(Constraint(field='food',value='新点心'+str(i),strength='soft',quote='新点心'+str(i)) for i in range(8))
    rejected=service._merge(current.model_copy(update={'constraints':additional}),snap)
    assert rejected.unresolved and '要求太多' in rejected.unresolved[0] and not rejected.constraints


@pytest.mark.parametrize('item',[
    {'field':'budget_fen','value':10000,'quote':'额外要求','budget_basis':'nightly'},
    {'field':'pace','value':'未知节奏','quote':'额外要求'},
    {'field':'mode','value':'teleport','quote':'额外要求'},
])
def test_unknown_planning_enums_do_not_default_to_a_legal_value(city,monkeypatch,item):
    text='明天，额外要求';raw={'planning':{'quote':text,'fields':[{'field':'date','value':'明天','quote':'明天'},item]}}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert len(intent.planning.fields)==1 and intent.planning.fields[0].field=='date'
    dropped(meta,'planning.fields[1]','C')


@pytest.mark.parametrize('item',[
    {'field':'clean','value':True,'quote':'额外要求','strength':'mandatory'},
    {'field':'unknown_facility','value':True,'quote':'额外要求'},
])
def test_unknown_service_field_or_strength_does_not_get_guessed(city,monkeypatch,item):
    text='查酒店，要干净，额外要求'
    intent,meta=parse(city,monkeypatch,{'service_query':{'quote':text,'fields':[
        {'field':'clean','value':True,'quote':'干净'},item]}},text)
    assert len(intent.service_query.fields)==1 and intent.service_query.fields[0].strength=='hard'
    dropped(meta,'service_query.fields[1]','C')


@pytest.mark.parametrize('changes',[{'priority':'urgent'},{'status':'arranged'}])
def test_unknown_material_enums_cannot_change_basket(city,monkeypatch,changes):
    text='明天去[合成测试]museum';before=get(city)
    raw={'planning':{'quote':text,'fields':[{'field':'date','value':'明天','quote':'明天'}], 'choices':[
        {'name':'[合成测试]museum','priority':'want','status':'pending','quote':'去[合成测试]museum',**changes}]}}
    emit(monkeypatch,raw);task=run(city,text)
    assert not task['understanding']['planning']['choices'] and not task['result'].get('basket_proposal')
    assert get(city)==before
    dropped(task['interpretation'],'planning.choices[0]','C')
    assert '这部分我没采用：『去[合成测试]museum』' in task['result']['reply']


def test_every_extra_field_is_audited_without_disturbing_the_query(city,monkeypatch):
    raw={'actions':[QUERY|{'unexpected':True}],'weather_query':{'quote':'查天气','unexpected':True},'unexpected':True}
    intent,meta=parse(city,monkeypatch,raw,READ+'。查天气')
    assert intent.weather_query and any(a.purpose=='trip' for a in intent.actions)
    for path in ('unexpected','actions[0].unexpected','weather_query.unexpected'):dropped(meta,path,'C','extra_field')
    assert not understand.dropped_reply(intent,meta,READ+'。查天气')


def test_a_valid_field_covering_the_same_quote_suppresses_the_omission_notice(city,monkeypatch):
    text='明天出发';raw={'planning':{'quote':text,'fields':[
        {'field':'date','value':'明天','quote':'明天'}, {'field':'date','value':'后天','quote':'明天'}]}}
    intent,meta=parse(city,monkeypatch,raw,text)
    dropped(meta,'planning.fields[1]','A')
    assert not understand.dropped_reply(intent,meta,text)
    assert not any(d['quote']=='明天' and d['reason']=='date_not_in_quote' and d['path']=='planning.fields[0]' for d in meta['dropped'])


def test_invalid_planning_clock_cannot_erase_a_good_constraint_before_evidence_checks(city,monkeypatch):
    text='十点前回来';raw={'actions':[QUERY], 'planning':{'quote':text,'fields':[
        {'field':'end_time','value':'22:00','quote':'凭空写的证据'}]},'constraints':[
        {'field':'return_by','value':'22:00','strength':'hard','quote':text}]}
    intent,meta=parse(city,monkeypatch,raw,READ+'。'+text)
    assert any(c.field=='return_by' and c.value=='22:00' for c in intent.constraints)
    assert not any(d['reason']=='duplicate_constraint' for d in meta.get('dropped',()))


def test_equivalent_date_spellings_are_not_a_conflict(city,monkeypatch):
    text='明天出发';raw={'planning':{'quote':text,'fields':[
        {'field':'date','value':'明天','quote':'明天'}, {'field':'date','value':'2030-05-02','quote':'明天'}]}}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert intent.planning.fields and not meta.get('planning_conflicts')


def test_no_sentence_boundary_in_oversize_chat_drops_only_chat(city,monkeypatch):
    emit(monkeypatch,{'actions':[QUERY],'conversation':{'reply':'长'*1201}})
    task=run(city,READ)
    assert task['mode']=='direct' and task['understanding']['conversation'] is None
    dropped(task['interpretation'],'conversation.reply','C','chat_truncated')


@pytest.mark.parametrize('raw,text',[
    ({'route_query':{'quote':'去甲地怎么走','destination':'甲地','use_current_position':True}},'去甲地怎么走'),
    ({'planning':{'operation':'weather_replan','quote':'想根据天气安排'}},'想根据天气安排'),
    ({'planning':{'operation':'continue','quote':'情况不错'}},'情况不错'),
])
def test_protected_authorization_gates_still_stop_the_whole_turn(city,monkeypatch,raw,text):
    raw={**raw,'conversation':{'reply':'这条说明不应绕过授权门。'}}
    before=get(city);emit(monkeypatch,raw);task=run(city,text)
    assert task['status']=='needs_clarification' and task['metrics']['tools']==0 and get(city)==before
    assert task['interpretation'].get('validation_failure') and '不应绕过' not in task['result']['reply']


def test_invalid_items_do_not_consume_the_valid_item_capacity(city,monkeypatch):
    quotes=[f'看行程{i}' for i in range(8)]
    raw={'actions':[{'kind':'query','purpose':'trip','quote':q} for q in quotes]+[{'kind':'unknown','quote':'额外要求'}]}
    intent,meta=parse(city,monkeypatch,raw,'，'.join([*quotes,'额外要求']))
    assert len(intent.actions)==8 and not intent.unresolved
    dropped(meta,'actions[8]','C')


def test_all_valid_read_actions_in_the_allowed_capacity_are_processed(city,monkeypatch):
    quotes=[f'看行程{i}' for i in range(6)]
    emit(monkeypatch,{'actions':[{'kind':'query','purpose':'trip','quote':q} for q in quotes]})
    task=run(city,'，'.join(quotes))
    assert task['mode']=='compound' and len(task['result']['answers'])==6


@pytest.mark.parametrize('raw,text',[
    ({'planning':{'quote':'玩一天','fields':[{'field':'trip_days','value':True,'quote':'玩一天'}]}},'玩一天'),
    ({'state_request':{'operation':'rest','minutes':True,'quote':'休息一分钟'}},'休息一分钟'),
])
def test_booleans_cannot_turn_into_user_supplied_numbers(city,monkeypatch,raw,text):
    raw['conversation']={'reply':'请核对数值。'}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert intent.planning is None and intent.state_request is None
    dropped(meta,category='C',reason='invalid numeric type')


def test_conflicting_field_cannot_resume_planning_through_a_basket_confirmation(city,monkeypatch):
    text='明天或者后天，想去[合成测试]museum，从[合成测试]origin出发'
    emit(monkeypatch,{'planning':{'quote':text,'fields':[
        {'field':'date','value':'明天','quote':'明天'}, {'field':'date','value':'后天','quote':'后天'},
        {'field':'origin','value':'[合成测试]origin','quote':'从[合成测试]origin出发'}], 'choices':[
        {'name':'[合成测试]museum','priority':'want','quote':'想去[合成测试]museum'}]}})
    before=get(city);task=run(city,text)
    assert task['status']=='needs_clarification' and not task['result'].get('basket_proposal') and not task['result'].get('basket_choices')
    assert '你说的是' in task['result']['reply'] and get(city)==before
    assert task['understanding']['planning']['choices']
    saved=city['client'].get(path(city,'/exploration/constraints')).json()
    assert saved['constraints']['origin_place_id']=='origin' and saved['result'] is None
