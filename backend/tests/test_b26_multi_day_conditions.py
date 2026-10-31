import pytest

from app.city import hotels
from app.city.agent.date_range import daily_evidence
from test_city_assistant import city, path, get
from test_city_agent import run
from test_agent_output_tolerance import emit, parse
from test_planning_understanding import field, model
from multi_day_helpers import basket_material


def range_fields(end='2030-05-05'):
    quote='2030-05-03到'+end
    return [field('start_date','2030-05-03',quote),field('end_date',end,quote)]


def daily_fields(scope=None, destination='origin'):
    return [dict(field(name,value,quote),**({'scope':scope} if scope else {}))
        for name,value,quote in [('start_time','08:00','早上8点'),('end_time','20:00','20点结束'),
            ('origin','[合成测试]origin','从[合成测试]origin出发'),
            ('destination','[合成测试]'+destination,'回[合成测试]'+destination)]]


def daily_text(marker='三天都', destination='origin', end='2030-05-05'):
    return f'2030-05-03到{end}，{marker}这样安排，早上8点开始、20点结束，从[合成测试]origin出发，回[合成测试]{destination}'


def saved(city):
    return city['client'].get(path(city,'/exploration/constraints')).json()['constraints']


@pytest.mark.parametrize('marker',['每天','每一天','每日都一样','天天','各天','3天都','三天都','两天全都','这几天都','所有天都'])
def test_daily_scope_grammar_covers_a_class_of_expressions(marker):
    assert daily_evidence('8点出门',marker+'相同，8点出门')


@pytest.mark.parametrize('boundary',['。','！','？','；','\n','!','?',';'])
def test_daily_evidence_cannot_cross_sentence_boundary(boundary):
    assert not daily_evidence('8点出门','每天看节目'+boundary+'8点出门')


@pytest.mark.parametrize('scope',[None,'every_day'])
@pytest.mark.parametrize('same_sentence',[False,True])
def test_overlong_field_quote_still_uses_only_the_sentence_containing_its_value(city,monkeypatch,scope,same_sentence):
    tail='每天早上8点出发。下午看看风景' if same_sentence else '每天看看风景。早上8点出发'
    text='2030-05-03到2030-05-05。'+tail
    item=field('start_time','08:00',text)
    if scope:item['scope']=scope
    emit(monkeypatch,{'planning':{'quote':text,'fields':range_fields()+[item]}})
    task=run(city,text)
    assert all(bool(d['starts_at'])==same_sentence for d in saved(city)['days'])
    if not same_sentence:
        assert task['status']=='needs_clarification'
        assert not any(r.get('source')=='sentence_scope' for r in task['interpretation'].get('recovered_fields',[]))


def test_repeated_short_quote_in_unrelated_sentences_cannot_borrow_scope():
    assert not daily_evidence('8点出发','每天8点出发。第二天8点出发')


@pytest.mark.parametrize('scope',[None,'every_day'])
@pytest.mark.parametrize('marker',['这几天都','每日都一样'])
def test_short_quotes_in_parallel_clauses_apply_to_all_days(city,monkeypatch,scope,marker,basket_material):
    text=daily_text(marker);before=get(city)
    model(monkeypatch,text,range_fields()+daily_fields(scope));task=run(city,text)
    assert task['status']=='needs_confirmation',task
    values=saved(city)
    assert len(values['days'])==3 and not values['pending_fields']
    assert all(d['starts_at'].endswith('T08:00:00+08:00') and d['ends_at'].endswith('T20:00:00+08:00')
        and d['origin_place_id']==d['destination_place_id']=='origin' for d in values['days'])
    assert [o['place_id'] for o in values['overnights']]==['origin','origin']
    assert '过夜' not in task['result']['reply'] and get(city)==before
    assert len(task['result']['planning_workflow']['result']['result']['itineraries'][0]['days'])==3
    recovered=task['interpretation'].get('recovered_fields',[])
    assert {r['field'] for r in recovered if r.get('source')=='sentence_scope'}==(
        {'start_time','end_time','origin','destination'} if scope is None else set())


@pytest.mark.parametrize('scope',[None,'every_day'])
def test_unrelated_sentence_does_not_supply_scope_and_still_asks(city,monkeypatch,scope):
    text='2030-05-03到2030-05-05，每天看风景。早上8点开始'
    fields=range_fields()+[dict(field('start_time','08:00','早上8点'),**({'scope':scope} if scope else {}))]
    model(monkeypatch,text,fields);task=run(city,text)
    assert task['status']=='needs_clarification'
    assert all(d['starts_at'] is None for d in saved(city)['days'])
    assert '出发时间用于哪一天，还是每天' in task['result']['reply'] if scope is None else '几点开始' in task['result']['reply']
    assert not any(r.get('source')=='sentence_scope' for r in task['interpretation'].get('recovered_fields',[]))
    if scope:assert any(d['check']=='scope_not_in_quote' for d in task['interpretation']['dropped_fields'])


def test_two_values_for_a_field_never_get_implicit_daily_scope(city,monkeypatch):
    text='2030-05-03到2030-05-05，三天都可以早上8点或者9点开始'
    raw={'planning':{'quote':text,'fields':range_fields()+[
        field('start_time','08:00','早上8点'),field('start_time','09:00','9点')]}}
    intent,meta=parse(city,monkeypatch,raw,text)
    assert [v.field for v in intent.planning.fields]==['start_date','end_date']
    assert meta['planning_conflicts'][0]['field']=='start_time'
    assert not any(r.get('source')=='sentence_scope' for r in meta.get('recovered_fields',[]))


def test_saved_multi_day_range_supports_scope_recovery(city,monkeypatch):
    text='2030-05-03到2030-05-05'
    model(monkeypatch,text,range_fields());run(city,text)
    text='三天全都早上8点开始'
    emit(monkeypatch,{'planning':{'quote':text,'fields':[field('start_time','08:00','早上8点')]}});run(city,text)
    assert all(d['starts_at'].endswith('T08:00:00+08:00') for d in saved(city)['days'])


def test_single_day_does_not_get_implicit_daily_scope(city,monkeypatch):
    text='2030-05-03，三天都早上8点开始'
    intent,meta=parse(city,monkeypatch,{'planning':{'quote':text,'fields':[
        field('date','2030-05-03','2030-05-03'),field('start_time','08:00','早上8点')]}},text)
    assert intent.planning.fields[-1].scope is None
    assert not any(r.get('source')=='sentence_scope' for r in meta.get('recovered_fields',[]))


@pytest.mark.parametrize('location',[None,'[合成测试]hotel','未登记的住处'])
def test_different_boundaries_or_unmatched_hotel_location_still_ask(city,monkeypatch,location):
    destination='hotel' if location is None else 'origin'
    text=daily_text(destination=destination)
    extra={}
    if location:
        quote='帮我查住在'+location+'的酒店';text+='，'+quote
        extra['service_query']={'quote':quote,'fields':[field('location_text',location,location)]}
        monkeypatch.setattr(hotels,'search',lambda q:{'status':'ok','items':[]})
    model(monkeypatch,text,range_fields()+daily_fields('every_day',destination),**extra)
    before=get(city);task=run(city,text)
    assert task['status']=='needs_clarification' and '过夜' in task['result']['reply']
    assert all(d['destination_place_id']==destination for d in saved(city)['days']) and get(city)==before
    assert not task['result']['planning_workflow']['result'].get('result')


@pytest.mark.parametrize('own_fields,expected_in,expected_nights',[
    ([], '2030-05-03',2),
    ([field('nights',4,'住4晚')], '2030-05-03',4),
    ([field('check_in','2030-05-04','2030-05-04入住')], '2030-05-04',2),
    ([field('check_in','2030-05-04','2030-05-04入住'),field('nights',3,'住3晚')], '2030-05-04',3),
    ([field('check_out','2030-05-06','2030-05-06退房')], '2030-05-03',3),
    ([field('check_in','那晚','那晚'),field('nights','一宿吧','一宿吧')], '2030-05-03',2),
    ([field('check_in',['那晚'],'那晚'),field('nights',{'value':2},'住那晚')], '2030-05-03',2),
])
def test_hotel_dates_use_trip_but_preserve_visitor_values_and_echo(city,monkeypatch,own_fields,expected_in,expected_nights):
    quote='帮我查酒店'+''.join('，'+f['quote'] for f in own_fields)
    text=daily_text()+'，'+quote;searches=[]
    monkeypatch.setattr(hotels,'search',lambda q:searches.append(q) or {'status':'ok','items':[]})
    model(monkeypatch,text,range_fields()+daily_fields(),service_query={'quote':quote,'fields':own_fields})
    task=run(city,text);req=task['result']['services']['requirements']
    assert req['check_in']==expected_in and req['nights']==expected_nights
    assert len(searches)==1 and searches[0].check_in==expected_in
    if not all(name in {f['field'] for f in own_fields} for name in ('check_in','nights')) or expected_in=='2030-05-03':
        assert f"按行程 5 月 {int(expected_in[-2:])} 日入住、住 {expected_nights} 晚" in task['result']['reply']
    assert '请提供入住日期' not in task['result']['reply'] and '请提供退房日期或住几晚' not in task['result']['reply']
    assert '这部分我没采用' not in task['result']['reply']
    if any(f['value']=='那晚' for f in own_fields):
        assert {d['path'] for d in task['interpretation']['dropped']}=={'service.check_in','service.nights'}
        assert task['interpretation']['service_date_defaults']==['check_in','nights']


def test_hotel_query_uses_saved_range_without_new_planning(city,monkeypatch):
    text='2030-05-03到2030-05-05'
    model(monkeypatch,text,range_fields());run(city,text)
    text='帮我查酒店';searches=[]
    monkeypatch.setattr(hotels,'search',lambda q:searches.append(q) or {'status':'ok','items':[]})
    emit(monkeypatch,{'service_query':{'quote':text,'fields':[]}});task=run(city,text)
    assert task['mode']=='travel_services'
    assert task['result']['services']['requirements']['check_in']=='2030-05-03'
    assert task['result']['services']['requirements']['nights']==2 and len(searches)==1
    assert '按行程 5 月 3 日入住、住 2 晚' in task['result']['reply']


def test_later_daily_answer_replaces_unscoped_pending_values(city,monkeypatch):
    text='2030-05-03到2030-05-05，早上8点开始'
    model(monkeypatch,text,range_fields()+[field('start_time','08:00','早上8点')]);run(city,text)
    assert saved(city)['pending_fields'][0]['scope'] is None
    text='每日都早上8点开始'
    emit(monkeypatch,{'planning':{'quote':text,'fields':[field('start_time','08:00','早上8点')]}})
    task=run(city,text)
    assert not saved(city)['pending_fields']
    assert all(d['starts_at'].endswith('T08:00:00+08:00') for d in saved(city)['days'])
    assert '出发时间用于哪一天' not in task['result']['reply']


def test_lodging_location_can_be_corrected_without_changing_endpoints(city,monkeypatch,basket_material):
    text=daily_text()+'，帮我查住在未登记住处的酒店'
    monkeypatch.setattr(hotels,'search',lambda q:{'status':'ok','items':[]})
    model(monkeypatch,text,range_fields()+daily_fields(),service_query={'quote':'帮我查住在未登记住处的酒店',
        'fields':[field('location_text','未登记住处','未登记住处')]})
    assert run(city,text)['status']=='needs_clarification'
    text='帮我查住在[合成测试]origin的酒店，开始安排'
    emit(monkeypatch,{'service_query':{'quote':text,'fields':[field('location_text','[合成测试]origin','[合成测试]origin')]},
        'planning':{'operation':'continue','quote':'开始安排','fields':[]}})
    task=run(city,text)
    assert task['status']=='needs_confirmation' and '过夜' not in task['result']['reply']
    assert not saved(city)['clarifications']


def test_later_hotel_location_disagreement_is_asked_and_never_promoted_to_endpoint(city,monkeypatch):
    text=daily_text();model(monkeypatch,text,range_fields()+daily_fields());run(city,text)
    text='帮我查住在未登记住处的酒店'
    emit(monkeypatch,{'service_query':{'quote':text,'fields':[field('location_text','未登记住处','未登记住处')]}})
    monkeypatch.setattr(hotels,'search',lambda q:{'status':'ok','items':[]})
    task=run(city,text)
    assert '过夜' in task['result']['reply']
    assert all(d['destination_place_id']==d['origin_place_id']=='origin' for d in saved(city)['days'])
    text='开始安排';emit(monkeypatch,{'planning':{'operation':'continue','quote':text}})
    task=run(city,text)
    assert task['status']=='needs_clarification' and '过夜' in task['result']['reply']


@pytest.mark.parametrize('multi_schema',[False,True])
def test_single_day_never_supplies_hotel_dates(city,monkeypatch,multi_schema):
    text='2030-05-03到2030-05-03，帮我查酒店'
    fields=range_fields('2030-05-03') if multi_schema else [field('date','2030-05-03','2030-05-03')]
    monkeypatch.setattr(hotels,'search',lambda q:pytest.fail('single day must still ask dates'))
    model(monkeypatch,text,fields,service_query={'quote':'帮我查酒店','fields':[]});task=run(city,text)
    req=task['result']['services']['requirements']
    assert not req.get('check_in') and not req.get('nights')
    assert '请提供入住日期' in task['result']['reply'] and '请提供退房日期或住几晚' in task['result']['reply']


def test_one_utterance_with_short_quotes_and_lodging_reaches_whole_preview(city,monkeypatch,basket_material):
    text=daily_text('各天')+'，帮我查住在[合成测试]origin的酒店'
    monkeypatch.setattr(hotels,'search',lambda q:{'status':'ok','items':[]})
    model(monkeypatch,text,range_fields()+daily_fields(),service_query={
        'quote':'帮我查住在[合成测试]origin的酒店','fields':[field('location_text','[合成测试]origin','[合成测试]origin')]})
    before=get(city);task=run(city,text)
    assert task['status']=='needs_confirmation',task
    result=task['result']['planning_workflow']['result']['result']
    assert len(result['itineraries'][0]['days'])>=2
    assert len(saved(city)['overnights'])==2 and get(city)==before
    assert '按行程 5 月 3 日入住、住 2 晚' in task['result']['reply'] and '过夜' not in task['result']['reply']


@pytest.mark.parametrize('sentence,daily',[('周六那天都在城里转','') ,('这几天都早上八点出门','x'),('三天全都住在城里','x')])
def test_every_day_evidence_needs_a_count_or_range_before_dou(sentence,daily):
    from app.city.agent.date_range import EVERY_DAY
    assert bool(EVERY_DAY.search(sentence))==bool(daily)


@pytest.mark.parametrize('stay,asks',[('[合成测试]origin附近',False),('[合成测试]origin那边',False),('未登记住处附近',True)])
def test_stay_described_around_the_shared_endpoint_is_that_overnight_place(city,monkeypatch,stay,asks,basket_material):
    text=daily_text()+f'，晚上住{stay}'
    monkeypatch.setattr(hotels,'search',lambda q:{'status':'ok','items':[]})
    model(monkeypatch,text,range_fields()+daily_fields(),service_query={'quote':f'晚上住{stay}','fields':[field('location_text',stay,stay)]})
    task=run(city,text)
    assert ('过夜' in task['result']['reply'])==asks,task['result']['reply']
    if not asks:assert task['status']=='needs_confirmation'


@pytest.mark.parametrize('nights,end',[(1,'2030-05-04'),(2,'2030-05-05')])
def test_nights_without_days_or_end_fix_the_end_date(city,monkeypatch,nights,end):
    text=f'2030-05-03过来，住{"一" if nights==1 else "两"}晚'
    model(monkeypatch,text,[field('start_date','2030-05-03','2030-05-03'),field('trip_nights',nights,f'住{"一" if nights==1 else "两"}晚')])
    run(city,text)
    assert saved(city)['end_date']==end and len(saved(city)['days'])==nights+1
