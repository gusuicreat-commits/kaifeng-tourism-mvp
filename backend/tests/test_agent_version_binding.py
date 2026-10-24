"""Understanding uses exactly the service/planning versions it observed."""
import pytest
from app import language
from app.city.agent import provider, service
from app.city import hotels, travel_services, exploration_state
from app.db import connect
from test_travel_services import env,query,raw
from test_city_agent import run


@pytest.mark.parametrize('changed',['services','planning'])
def test_change_during_understanding_stops_old_interpretation(env,monkeypatch,changed):
    c,p=env;trip=c.get(p).json();searches=[]
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(hotels,'search',lambda q:searches.append(q) or hotels.normalize(raw()))
    message='查明天入住两晚酒店'
    def generate(*a,**k):
        with connect() as db:
            if changed=='services':
                w=travel_services.read(db,trip['id']);w.update(version=w['version']+1,requirements={'category':'hotels','nights':3})
                travel_services.write(db,trip['id'],w)
            else:exploration_state.save(db,trip['id'],{},None,0)
        return {'actions':[{'kind':'search_services','quote':message}],
            'service_query':{'quote':message,'fields':[{'field':'check_in','value':'明天','quote':'明天入住'},
                {'field':'nights','value':2,'quote':'两晚'}]}},{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',generate)
    task=run({'client':c,'trip':trip},message)
    assert task['status']=='stale' and task['metrics']['tools']==0 and not searches,task
    assert task['interpretation']['state_versions']['services']==0
    if changed=='services':assert c.get(p+'/services').json()['requirements']['nights']==3


@pytest.mark.parametrize('during',['cancel','newer_conditions'])
def test_inflight_service_result_is_not_published_after_cancel_or_newer_conditions(env,monkeypatch,during):
    c,p=env;trip=c.get(p).json();message='查明天入住两晚酒店'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_services','quote':message}],
        'service_query':{'quote':message,'fields':[{'field':'check_in','value':'明天','quote':'明天入住'},
            {'field':'nights','value':2,'quote':'两晚'}]}},{'source':'mock_model'}))
    def search(q):
        task=next(t for t in service.TASKS.values() if t.trip_id==trip['id'])
        if during=='cancel':
            response=c.post(p+'/agent/tasks/'+task.id+'/cancel');assert response.status_code==200
        else:
            with connect() as db:
                w=travel_services.read(db,trip['id']);w.update(version=1,requirements={'category':'hotels','nights':5})
                travel_services.write(db,trip['id'],w)
        return hotels.normalize(raw())
    monkeypatch.setattr(hotels,'search',search)
    task=run({'client':c,'trip':trip},message)
    assert task['status']==('cancelled' if during=='cancel' else 'stale')
    w=c.get(p+'/services').json()
    assert w['version']==(0 if during=='cancel' else 1) and w['result'] is None
    assert c.get(p).json()==trip


def test_relative_stay_date_stays_bound_across_midnight(env,monkeypatch):
    from datetime import datetime,timedelta
    from app.city.schema import TZ
    from app.city.assistant import context
    c,p=env;original=datetime.now(TZ);clock=[original]
    monkeypatch.setattr(context,'now',lambda:clock[0])
    monkeypatch.setattr(language,'available',lambda:True)
    message='查明天入住两晚酒店'
    def generate(*a,**k):
        clock[0]=original+timedelta(days=1)
        return {'actions':[{'kind':'search_services','quote':message}],
            'service_query':{'quote':message,'fields':[{'field':'check_in','value':'明天','quote':'明天入住'},
                {'field':'nights','value':2,'quote':'两晚'}]}},{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',generate)
    task=run({'client':c,'trip':c.get(p).json()},message)
    assert task['result']['services']['requirements']['check_in']==(original.date()+timedelta(days=1)).isoformat()
