"""The reported hotel exchange, isolated state and semantic/provider fakes."""
from datetime import datetime, timedelta

import pytest

from app import language
from app.city import hotels, travel_services as services
from app.city.agent import provider
from app.city.agent.models import ServiceIntent
from app.city.schema import TZ
from test_city_agent import run
from test_travel_services import env, raw

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


@pytest.mark.parametrize('entry',[
    field('nights',3,'住两个晚上'),
    field('nights',2,'两个人'),
    field('breakfast',True,'有没有早餐都行',strength='hard'),
    field('room_type','大床房','我要标间'),
    field('window',True,'我要标间'),
])
def test_semantic_fields_cannot_invent_or_change_scope(entry):
    text=entry['quote']
    intent=ServiceIntent(quote=text,fields=[entry])
    with pytest.raises(ValueError):
        services.semantic_requirements(intent,text,{},datetime.now(TZ).date())

@pytest.mark.parametrize('entry',[
    field('nights',2,'两个人，住三晚'),
    field('rooms',2,'两个人，一间房'),
    field('max_price',500,'预算两天500元'),
    field('stay_budget_fen',50000,'整趟旅行预算500元'),
    field('stay_budget_fen',50000,'每晚预算500元'),
    field('stay_budget_fen',50123,'住宿预算501元'),
])
def test_numeric_field_binds_its_unit_and_budget_scope(entry):
    with pytest.raises(ValueError):
        services.semantic_requirements(ServiceIntent(quote=entry['quote'],fields=[entry]),entry['quote'],{},datetime.now(TZ).date())


def test_date_patch_order_and_conflicts_preserve_explicit_stay():
    today=datetime.now(TZ).date()
    def apply(entries,previous=None):
        message='，'.join(e['quote'] for e in entries)
        return services.semantic_requirements(ServiceIntent(quote=message,fields=entries),message,previous or {},today)
    saved=apply([field('nights',2,'住两个晚上'),field('room_type','标间','我要标间'),
                 field('location_text','清明上河园','离清明上河园近'),field('stay_budget_fen',50000,'预算两天500')])
    changed=apply([field('check_in','明天','明天入住')],saved)
    assert changed['nights']==2 and changed['check_out']==(today+timedelta(days=3)).isoformat()
    assert 'guests' not in changed and 'rooms' not in changed and 'total_budget_fen' not in changed
    assert changed['stay_budget_fen']==50000
    for entries in ([field('check_in','明天','明天入住'),field('check_out','后天','后天退房'),field('nights',2,'住两晚')],
                    [field('nights',2,'住两晚'),field('check_out','后天','后天退房'),field('check_in','明天','明天入住')]):
        with pytest.raises(ValueError):apply(entries)
    assert saved.get('check_in') is None  # patches never mutate the original

@pytest.mark.parametrize('entry',[
    field('breakfast',False,'必须有早餐'),
    field('window',True,'不要窗户'),
    field('room_type','标间','标间大床房都行',strength='hard'),
    field('location_text','清明上河园','必须靠近清明上河园',strength='soft'),
])
def test_condition_polarity_and_strength_cannot_be_reversed(entry):
    with pytest.raises(ValueError):
        services.semantic_requirements(ServiceIntent(quote=entry['quote'],fields=[entry]),entry['quote'],{},datetime.now(TZ).date())


def test_preference_keeps_saved_must_until_explicit_waiver():
    today=datetime.now(TZ).date()
    def apply(entry,prev):
        return services.semantic_requirements(ServiceIntent(quote=entry['quote'],fields=[entry]),entry['quote'],prev,today)
    first=apply(field('window',True,'必须有窗户'),{})
    second=apply(field('window',True,'最好有窗户',strength='soft'),first)
    assert second['attributes']['window']['strength']=='hard'
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
