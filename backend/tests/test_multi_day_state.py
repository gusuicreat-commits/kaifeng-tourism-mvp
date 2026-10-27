"""Multi-day state gates with synthetic facts only."""
import copy
from datetime import datetime
from uuid import uuid4

import pytest
from app.city import state
from app.db import connect
from test_city_engine_api import engine
from multi_day_helpers import draft


def rid():return str(uuid4())


def create(engine, monkeypatch):
    c,_,_=engine
    monkeypatch.setattr(state,'now',lambda:datetime.fromisoformat('2030-05-02T09:45:00+08:00'))
    value=draft(3)
    for i,d in enumerate(value['days']):
        d['stops']=[{'stop_id':f'visit:{i}','place_id':'museum','kind':'visit','duration_seconds':600}]
    body={'request_id':rid(),'draft':value}
    r=c.post('/api/city/v1/trips',json=body)
    assert r.status_code==201,r.text
    assert c.post('/api/city/v1/trips',json=body).json()==r.json()
    return r.json()


def update(engine,t,action,proposal=False,request_id=None,version=None):
    c=engine[0];body={'schema_version':'city-state/2','request_id':request_id or rid(),
                   'expected_version':version or t['version'],'action':action}
    return c.request('POST' if proposal else 'PATCH','/api/city/v1/trips/'+t['id']+('/changes' if proposal else ''),json=body)


def activate(engine,t):
    r=update(engine,t,{'operation':'transition','status':'ACTIVE'})
    assert r.status_code==200,r.text
    return r.json()


def test_v2_create_and_today_progress_are_idempotent_and_leave_other_days_frozen(engine,monkeypatch):
    t=create(engine,monkeypatch);t=activate(engine,t)
    before=copy.deepcopy(t['draft']['days']);request=rid()
    action={'operation':'mark_completed','day_id':'day:2030-05-02','stop_id':'visit:1',
            'actual_start':'2030-05-02T09:00:00+08:00','actual_end':'2030-05-02T09:30:00+08:00'}
    r=update(engine,t,action,request_id=request)
    assert r.status_code==200,r.text
    saved=r.json();assert saved['version']==t['version']+1 and saved['route_version']==t['route_version']
    assert update(engine,t,action,request_id=request).json()==saved
    assert saved['draft']['days'][0]==before[0] and saved['draft']['days'][2]==before[2]
    assert saved['draft']['days'][1]['stops'][0]['progress']=='completed'
    assert update(engine,t,action).status_code==409
    with connect() as db:
        assert db.execute('SELECT count(*) FROM city_trip_events WHERE trip_id=? AND event_type=?',(t['id'],'mark_completed')).fetchone()[0]==1


@pytest.mark.parametrize('day,code',[('2030-05-01','DAY_FROZEN'),('2030-05-03','FUTURE_DAY_LOCKED')])
def test_history_and_future_state_writes_rejected_without_changes(engine,monkeypatch,day,code):
    t=create(engine,monkeypatch);t=activate(engine,t)
    r=update(engine,t,{'operation':'skip_stop','day_id':'day:'+day,'stop_id':'visit:0' if day.endswith('01') else 'visit:2'})
    assert r.status_code==409 and r.json()['detail']['code']==code
    assert engine[0].get('/api/city/v1/trips/'+t['id']).json()==t


def test_v2_today_anchor_and_closed_day_freeze(engine,monkeypatch):
    t=create(engine,monkeypatch);t=activate(engine,t)
    r=update(engine,t,{'operation':'set_anchor','day_id':'day:2030-05-02',
                     'anchor':{'place_id':'museum','confirmed_at':'2030-05-02T09:40:00+08:00'}})
    assert r.status_code==200,r.text
    t=r.json()
    assert t['anchor']['place_id']=='museum'
    assert update(engine,t,{'operation':'close_day','day_id':'day:2030-05-02'}).json()['detail']['code']=='DAY_HAS_PENDING'
    r=update(engine,t,{'operation':'skip_stop','day_id':'day:2030-05-02','stop_id':'visit:1'})
    assert r.status_code==200,r.text
    t=r.json();action={'operation':'close_day','day_id':'day:2030-05-02'}
    assert update(engine,t,action).json()['detail']['code']=='CONFIRMATION_REQUIRED'
    p=update(engine,t,action,proposal=True)
    assert p.status_code==200,p.text
    assert engine[0].get('/api/city/v1/trips/'+t['id']).json()==t
    body={'request_id':rid(),'expected_version':t['version'],'proposal_id':p.json()['proposal_id']}
    path='/api/city/v1/trips/'+t['id']+'/confirm'
    r=engine[0].post(path,json=body)
    assert r.status_code==200,r.text
    assert engine[0].post(path,json=body).json()==r.json()
    t=r.json();assert t['draft']['days'][1]['status']=='closed'
    assert update(engine,t,{'operation':'set_anchor','day_id':'day:2030-05-02','anchor':{'place_id':'museum','confirmed_at':'2030-05-02T09:40:00+08:00'}}).json()['detail']['code']=='DAY_FROZEN'
    with connect() as db:assert db.execute('SELECT ended_at FROM city_trip_states WHERE id=?',(t['id'],)).fetchone()[0] is None


def test_v2_locked_skip_needs_confirmation_and_conflict_rolls_back(engine,monkeypatch):
    t=create(engine,monkeypatch)
    with connect() as db:
        value=copy.deepcopy(t['draft']);value['days'][1]['stops'][0]['locked']=True
        import json
        db.execute('UPDATE city_trip_states SET draft_json=? WHERE id=?',(json.dumps(value),t['id']))
    t=activate(engine,t);action={'operation':'skip_stop','day_id':'day:2030-05-02','stop_id':'visit:1'}
    assert update(engine,t,action).json()['detail']['code']=='CONFIRMATION_REQUIRED'
    p=update(engine,t,action,proposal=True).json()
    t2=update(engine,t,{'operation':'transition','status':'PAUSED'}).json()
    r=engine[0].post('/api/city/v1/trips/'+t['id']+'/confirm',json={'request_id':rid(),'expected_version':t2['version'],'proposal_id':p['proposal_id']})
    assert r.status_code==409
    assert engine[0].get('/api/city/v1/trips/'+t['id']).json()==t2
    with connect() as db:assert db.execute('SELECT consumed FROM city_proposals WHERE id=?',(p['proposal_id'],)).fetchone()[0]==0


def test_v2_candidate_cannot_be_applied_after_day_freezes(engine,monkeypatch):
    t=create(engine,monkeypatch);t=activate(engine,t)
    action={'operation':'skip_stop','day_id':'day:2030-05-02','stop_id':'visit:1'}
    p=update(engine,t,action,proposal=True).json()
    monkeypatch.setattr(state,'now',lambda:datetime.fromisoformat('2030-05-03T09:45:00+08:00'))
    r=engine[0].post('/api/city/v1/trips/'+t['id']+'/confirm',json={
        'request_id':rid(),'expected_version':t['version'],'proposal_id':p['proposal_id']})
    assert r.status_code==409 and r.json()['detail']['code']=='DAY_FROZEN'
    with connect() as db:
        assert db.execute('SELECT version FROM city_trip_states WHERE id=?',(t['id'],)).fetchone()[0]==t['version']
        assert db.execute('SELECT consumed FROM city_proposals WHERE id=?',(p['proposal_id'],)).fetchone()[0]==0


def test_v2_create_rejects_forged_history_and_unknown_overnight(engine,monkeypatch):
    create(engine,monkeypatch)
    c=engine[0]
    value=draft(3);value['days'][0]['status']='closed'
    r=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':value})
    assert r.status_code==422
    value=draft(3);value['overnights'][0]['source_kind']='selected_hotel'
    r=c.post('/api/city/v1/trips',json={'request_id':rid(),'draft':value})
    assert r.status_code==503 and r.json()['detail']['code']=='MULTI_DAY_HOTEL_UNAVAILABLE'
