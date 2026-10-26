"""The reported hotel exchange, isolated state and semantic/provider fakes."""
from datetime import datetime, timedelta

import pytest

from app import language
from app.city import hotels, travel_services as services
from app.city.agent import provider
from app.city.agent.models import ServiceIntent
from app.city.schema import TZ
from test_city_agent import run
from test_travel_services import env, query, raw

FIRST='我要标间，离清明上河园近的，住两个晚上，有没有早餐都行，住两个晚上，预算两天500'


def field(name,value,quote,**extra):
    return {'field':name,'value':value,'quote':quote,**extra}


def test_exact_exchange_uses_saved_service_context_and_never_itinerary(env,monkeypatch):
    client,path=env
    before=client.get(path).json()
    planning_before=client.get(path+'/exploration/constraints').json()
    calls=[];searches=[]
    monkeypatch.setattr(language,'available',lambda:True)
    def semantic(system,payload,**kwargs):
        calls.append(payload)
        if payload['text']==FIRST:
            fields=[field('room_type','标间','我要标间'),field('location_text','清明上河园','离清明上河园近的'),
                    field('nights',2,'住两个晚上'),field('breakfast',True,'有没有早餐都行',strength='optional'),
                    field('stay_budget_fen',50000,'预算两天500')]
        else:
            saved=payload['context']['travel_service_workspace']
            assert saved['requirements']['nights']==2
            assert saved['requirements']['stay_budget_fen']==50000
            assert saved['missing_fields']==['请提供入住日期。']
            fields=[field('check_in','明天','明天入住')]
        return {'actions':[{'kind':'search_services','quote':payload['text']}],
                'service_query':{'quote':payload['text'],'fields':fields}},{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',semantic)
    monkeypatch.setattr(services,'parse',lambda *_:pytest.fail('typed intent must not be reinterpreted by keyword parser'))
    monkeypatch.setattr(hotels,'search',lambda q: searches.append(q) or hotels.normalize(raw()))
    trip={'client':client,'trip':before}
    first=run(trip,FIRST)
    assert first['mode']=='travel_services'
    assert first['result']['services']['result']['questions']==['请提供入住日期。']
    assert not searches
    second=run(trip,'明天入住')
    assert second['mode']=='travel_services' and 'planning_action' not in second['result']
    assert len(calls)==2 and len(searches)==1
    tomorrow=(datetime.now(TZ).date()+timedelta(days=1))
    saved=client.get(path+'/services').json()
    assert saved['requirements']['check_in']==tomorrow.isoformat()
    assert saved['requirements']['check_out']==(tomorrow+timedelta(days=2)).isoformat()
    assert saved['requirements']['room_type']=='标间'
    assert saved['requirements']['location_text']=='清明上河园'
    assert saved['requirements']['attributes']['breakfast']['strength']=='optional'
    assert saved['requirements']['stay_budget_fen']==50000
    assert not saved['result'].get('questions')
    assert not saved['result']['items'][0]['hard_conditions_verified']
    assert client.get(path).json()==before
    assert client.get(path+'/exploration/constraints').json()==planning_before


def test_legacy_service_endpoint_retains_nights_before_checkin():
    first,questions=services.parse(FIRST)
    assert questions==['请提供入住日期。']
    assert first['nights']==2 and first['stay_budget_fen']==50000
    assert first['attributes']['breakfast']['strength']=='optional'
    second,questions=services.parse('明天入住',first)
    assert not questions
    assert (datetime.fromisoformat(second['check_out'])-datetime.fromisoformat(second['check_in'])).days==2
    third,questions=services.parse('后天入住',second)
    assert not questions and third['nights']==2 and third['room_type']=='标间'


def assert_dropped(entry,check):
    dropped=[];text=entry['quote']
    req=services.semantic_requirements(ServiceIntent(quote=text,fields=[entry]),text,{},datetime.now(TZ).date(),dropped)
    assert entry['field'] not in req and entry['field'] not in req['attributes']
    assert entry['field'] not in req['field_strengths']
    assert dropped==[{'field':entry['field'],'check':check,'quote':entry['quote']}]


@pytest.mark.parametrize('entry',[
    field('nights',3,'住两个晚上'),
    field('nights',2,'两个人'),
])
def test_numeric_fields_still_cannot_invent_values(entry):
    assert_dropped(entry,'value_not_in_quote')


@pytest.mark.parametrize('entry',[
    field('breakfast',True,'有没有早餐都行',strength='hard'),
    field('room_type','大床房','我要标间'),
    field('window',True,'我要标间'),
    field('room_type','无窗房','不要无窗房'),
])
def test_model_semantics_are_kept_when_quote_is_grounded(entry):
    dropped=[];text=entry['quote']
    req=services.semantic_requirements(ServiceIntent(quote=text,fields=[entry]),text,{},datetime.now(TZ).date(),dropped)
    assert not dropped
    assert (req.get('attributes',{}).get(entry['field']) or req).get('value',req.get(entry['field']))==entry['value']

@pytest.mark.parametrize('entry',[
    field('nights',2,'两个人，住三晚'),
    field('rooms',2,'两个人，一间房'),
    field('stay_budget_fen',50123,'住宿预算501元'),
])
def test_numeric_field_binds_its_unit(entry):
    assert_dropped(entry,'value_not_in_quote')


@pytest.mark.parametrize('entry',[
    field('max_price',500,'预算两天500元'),
    field('stay_budget_fen',50000,'整趟旅行预算500元'),
    field('stay_budget_fen',50000,'每晚预算500元'),
    field('max_price',200,'预算200以内'),
    field('stay_budget_fen',20000,'住宿合计两百块'),
])
def test_money_scope_is_model_selected_and_amount_is_grounded(entry):
    dropped=[];text=entry['quote']
    req=services.semantic_requirements(ServiceIntent(quote=text,fields=[entry]),text,{},datetime.now(TZ).date(),dropped)
    assert not dropped and req[entry['field']]==entry['value']


def test_date_patch_order_and_conflicts_preserve_explicit_stay():
    today=datetime.now(TZ).date()
    def apply(entries,previous=None,dropped=None):
        message='，'.join(e['quote'] for e in entries)
        return services.semantic_requirements(ServiceIntent(quote=message,fields=entries),message,previous or {},today,dropped)
    saved=apply([field('nights',2,'住两个晚上'),field('room_type','标间','我要标间'),
                 field('location_text','清明上河园','离清明上河园近'),field('stay_budget_fen',50000,'预算两天500')])
    changed=apply([field('check_in','明天','明天入住')],saved)
    assert changed['nights']==2 and changed['check_out']==(today+timedelta(days=3)).isoformat()
    assert 'guests' not in changed and 'rooms' not in changed and 'total_budget_fen' not in changed
    assert changed['stay_budget_fen']==50000
    for entries in ([field('check_in','明天','明天入住'),field('check_out','后天','后天退房'),field('nights',2,'住两晚')],
                    [field('nights',2,'住两晚'),field('check_out','后天','后天退房'),field('check_in','明天','明天入住')]):
        dropped=[];result=apply(entries,dropped=dropped)
        assert result['nights']==2 and result['check_out']==(today+timedelta(days=3)).isoformat()
        assert 'check_out' not in result['field_strengths']
        assert dropped==[{'field':'check_out','check':'conflicting_values','quote':'后天退房'}]
    assert saved.get('check_in') is None  # patches never mutate the original

@pytest.mark.parametrize('entry',[
    field('breakfast',False,'必须有早餐',strength='hard'),
    field('window',True,'不要窗户',strength='hard'),
    field('room_type','标间','标间大床房都行',strength='hard'),
    field('location_text','清明上河园','必须靠近清明上河园',strength='soft'),
])
def test_condition_polarity_and_strength_follow_model(entry):
    dropped=[];text=entry['quote']
    req=services.semantic_requirements(ServiceIntent(quote=text,fields=[entry]),text,{},datetime.now(TZ).date(),dropped)
    assert not dropped
    value=req.get('attributes',{}).get(entry['field'],req).get('strength',req['field_strengths'][entry['field']])
    assert value==entry['strength']


def test_preference_strength_is_replaced_by_the_model():
    today=datetime.now(TZ).date()
    def apply(entry,prev):
        return services.semantic_requirements(ServiceIntent(quote=entry['quote'],fields=[entry]),entry['quote'],prev,today)
    first=apply(field('window',True,'必须有窗户'),{})
    second=apply(field('window',True,'最好有窗户',strength='soft'),first)
    assert second['attributes']['window']['strength']=='soft'
    third=apply(field('window',True,'窗户没有也行',strength='optional'),second)
    assert third['attributes']['window']['strength']=='optional'


def test_optional_room_and_area_remain_unknown_without_becoming_must(env,monkeypatch):
    client,path=env
    quote='查明天入住两晚酒店，标间没有也行，清明上河园附近最好'
    fields=[field('check_in','明天','明天入住'),field('nights',2,'两晚'),
            field('room_type','标间','标间没有也行',strength='optional'),
            field('location_text','清明上河园','清明上河园附近最好',strength='soft')]
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_services','quote':quote}],
        'service_query':{'quote':quote,'fields':fields}},{'source':'mock_model'}))
    task=run({'client':client,'trip':client.get(path).json()},quote)
    item=task['result']['services']['result']['items'][0]
    assert item['condition_status']=='unknown' and not item['budget_verified']
    assert {v['field']:v['strength'] for v in item['condition_evidence']}=={'room_type':'optional','location_text':'soft'}
    assert all(v['status']=='unknown' for v in item['condition_evidence'])


@pytest.mark.parametrize('entry,check',[
    (field('free_parking',True,'原话没有这个条件'),'quote_not_in_request'),
    (field('nights',0,'住0晚'),'invalid_value'),
    (field('nights','两晚','住两晚'),'invalid_value'),
    (field('nights',2,'住一一晚'),'value_not_in_quote'),
    (field('check_in','2030-02-30','2030-02-30入住'),'date_unresolved'),
    (field('check_in','下个月','下个月入住'),'date_unresolved'),
    (field('check_in','后天','明天入住'),'value_not_in_quote'),
    (field('window','true','有窗户'),'invalid_value'),
    (field('room_type','','标间'),'invalid_value'),
])
def test_bad_field_does_not_discard_a_valid_field(entry,check):
    text='酒店要干净，'+('方便停车' if check=='quote_not_in_request' else entry['quote'])
    dropped=[]
    req=services.semantic_requirements(ServiceIntent(quote=text,fields=[field('clean',True,'干净'),entry]),text,{},datetime.now(TZ).date(),dropped)
    assert req['attributes']['clean']['value'] is True
    assert entry['field'] not in req and entry['field'] not in req['attributes']
    assert entry['field'] not in req['field_strengths']
    assert dropped==[{'field':entry['field'],'check':check,'quote':entry['quote']}]


@pytest.mark.parametrize('reverse',[False,True])
def test_conflicting_field_keeps_previous_value_and_other_valid_fields(reverse):
    today=datetime.now(TZ).date();saved={'category':'hotels','nights':3,'field_strengths':{'nights':'hard'}}
    entries=[field('nights',1,'住一晚'),field('nights',2,'住两晚')]
    if reverse:entries.reverse()
    text='酒店要干净，住一晚，住两晚';dropped=[]
    req=services.semantic_requirements(ServiceIntent(quote=text,fields=[field('clean',True,'干净'),*entries]),text,saved,today,dropped)
    assert req['nights']==3 and req['field_strengths']['nights']=='hard'
    assert req['attributes']['clean']['value'] is True
    assert dropped==[{'field':'nights','check':'conflicting_values','quote':e['quote']} for e in entries]
    assert saved=={'category':'hotels','nights':3,'field_strengths':{'nights':'hard'}}


def test_service_query_itself_still_needs_current_evidence(env,monkeypatch):
    client,path=env;message='酒店希望干净';before=client.get(path+'/services').json()
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'service_query':{'quote':'不在原话里的查询',
        'fields':[field('clean',True,'干净')]}},{'source':'mock_model'}))
    monkeypatch.setattr(hotels,'search',lambda q:pytest.fail('invalid query must not reach provider'))
    task=run({'client':client,'trip':client.get(path).json()},message)
    assert task['status']=='needs_clarification' and task['metrics']['tools']==0
    assert task['interpretation']['validation_failure']['check']=='ungrounded evidence quote'
    assert client.get(path+'/services').json()==before
    with pytest.raises(ValueError,match='current query evidence missing'):
        services.semantic_requirements(ServiceIntent(quote='不在原话里的查询',fields=[field('clean',True,'干净')]),message,{},datetime.now(TZ).date())


@pytest.mark.parametrize('saved_dates',[False,True])
def test_pitch_hotel_fields_query_and_name_the_dropped_parking_condition(env,monkeypatch,caplog,saved_dates):
    import json
    import logging
    from app.city.agent import service
    client,path=env;searches=[]
    # A1 reuses explicit saved dates; interpreting a two-day weekend is a B task.
    if saved_dates:query(env,'找明天入住住两晚的酒店')
    message='我准备周末和朋友去开封玩两天，预算每人800元，想去万岁山和清明上河园，想吃开封特色美食。酒店希望干净、有窗户，最好附近方便停车，行程不要太赶。'
    quote='酒店希望干净、有窗户，最好附近方便停车'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_services','quote':quote}],
        'service_query':{'quote':quote,'fields':[field('clean',True,'干净'),field('window',True,'有窗户'),
            field('free_parking',True,'最好附近方便停车',strength='soft')]}},{'source':'mock_model'}))
    monkeypatch.setattr(hotels,'search',lambda q:searches.append(q) or hotels.normalize(raw()))
    monkeypatch.setattr(service.LOG,'handlers',[caplog.handler])
    monkeypatch.setattr(service.LOG,'propagate',False)
    with caplog.at_level(logging.INFO,logger='tourism.agent'):
        task=run({'client':client,'trip':client.get(path).json()},message)
    assert task['mode']=='travel_services' and task['status']==('answered' if saved_dates else 'needs_clarification')
    assert len(searches)==int(saved_dates)
    result=task['result'];req=result['services']['requirements']
    assert set(req['attributes'])=={'clean','window','free_parking'} and all(a['value'] is True for a in req['attributes'].values())
    assert req['attributes']['free_parking']['strength']=='soft'
    assert bool(result['services']['result']['items']) is saved_dates
    assert '酒店条件：' in result['reply'] and '免费停车（最好）' in result['reply']
    assert '没完全理解' not in result['reply']
    assert 'dropped_fields' not in task['interpretation']
    lines=[json.loads(r.getMessage()) for r in caplog.records if r.name=='tourism.agent']
    assert len(lines)==1 and lines[0]['dropped'] is None
    assert '最好附近方便停车' not in caplog.text


def test_compound_service_and_planning_drops_share_trace_without_duplicate_prompts(env,monkeypatch,caplog):
    import json
    import logging
    from app.city.agent import service
    client,path=env
    message='查明天入住两晚酒店，有窗户，方便停车；游玩后天从开封府出发'
    quote='查明天入住两晚酒店，有窗户，方便停车'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'service_query':{'quote':quote,'fields':[
        field('check_in','明天','明天入住'),field('nights',2,'两晚'),field('window',True,'有窗户'),
        field('free_parking',True,'方便停车'),field('breakfast',True,'有窗户')]},
        'planning':{'quote':'游玩后天从开封府出发','fields':[field('date','后天','后天'),field('origin','编造的酒店','开封府')]}},{'source':'mock_model'}))
    monkeypatch.setattr(service.LOG,'handlers',[caplog.handler])
    monkeypatch.setattr(service.LOG,'propagate',False)
    with caplog.at_level(logging.INFO,logger='tourism.agent'):
        task=run({'client':client,'trip':client.get(path).json()},message)
    assert task['mode']=='compound' and task['result']['services']['result']['items']
    assert '酒店条件：' in task['result']['reply'] and '免费停车（必须）' in task['result']['reply']
    assert '出发地没听准' in task['result']['reply'] and 'service.' not in task['result']['reply']
    line=json.loads([r for r in caplog.records if r.name=='tourism.agent'][-1].getMessage())
    assert line['dropped']==[{'field':'origin','check':'place_not_in_quote'}]


def test_hotel_missing_dates_use_normal_sentence_punctuation(env,monkeypatch):
    client,path=env;text='找干净的酒店'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'service_query':{'quote':text,'fields':[field('clean',True,'干净')]}},{'source':'mock_model'}))
    task=run({'client':client,'trip':client.get(path).json()},text)
    assert '请提供入住日期。请提供退房日期或住几晚。' in task['result']['reply']
    assert '。；' not in task['result']['reply']
