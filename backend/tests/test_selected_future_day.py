"""Single future-day edits exercise production transactions on isolated synthetic data."""
import copy,json
from datetime import datetime,timedelta
import pytest
from app.db import connect
from app.city import state
from app.city.engine import service
from test_city_engine_api import engine,confirm,current,rid
from test_multi_day_api import multi_engine,committed,start_today,remaining,saved_rows
from test_hotel_overnight_binding import hotel_trip
from test_trip_budget import price_ops,priced_select,options


def edit_future(e,req,day='day:2030-05-03',**extra):
    return remaining(e,req,scope='selected_future_day',day_id=day,**extra)


def test_future_preview_keeps_other_days_then_confirm_and_read_without_anchor(multi_engine):
    e,req,old=committed(multi_engine);c,t,_=e
    changed=copy.deepcopy(req);changed['days'][2].update(starts_at='2030-05-03T10:00:00+08:00',pace='relaxed')
    changed['goals'][-1]['duration_seconds']=1800
    before=c.get('/api/city/v1/trips/'+t['id']).json()
    p=edit_future(e,changed)
    assert p.status_code==200 and p.json()['status']=='preview',p.text
    assert p.json()['scope']=='selected_future_day' and p.json()['business_date']=='2030-05-03'
    assert c.get('/api/city/v1/trips/'+t['id']).json()==before
    for plan in p.json()['result']['itineraries']:
        assert plan['days'][:2]==old['itinerary']['days'][:2]
        target=plan['days'][2]
        assert target['requirements']['starts_at']=='2030-05-03T10:00:00+08:00'
        assert target['requirements']['origin_place_id']=='hotel'
        assert target['itinerary']['activities'][0]['duration_seconds']==1800
        assert target['itinerary']['ongoing'] is None and not target['itinerary']['completed_prefix']
    result=confirm(e,p.json());assert result.status_code==200,result.text
    assert result.json()['route_version']==2
    after=c.get('/api/city/v1/trips/'+t['id']).json()
    assert after['draft']['days'][:2]==before['draft']['days'][:2]
    assert after['draft']['days'][2]['starts_at']=='2030-05-03T10:00:00+08:00'
    assert not current(e).json()['stale']


def test_edit_tomorrow_while_today_is_ongoing_preserves_it_verbatim(multi_engine,monkeypatch):
    e,req,old=committed(multi_engine)
    a=old['itinerary']['days'][1]['itinerary']['activities'][0]
    moment=datetime.fromisoformat(a['starts_at'])+timedelta(minutes=1)
    start_today(e,monkeypatch,moment.isoformat(),'museum')
    today=remaining(e,req,ongoing={'stop_id':a['goal_id'],'remaining_seconds':1200})
    saved=confirm(e,today.json());assert saved.status_code==200,saved.text
    e[1]['version']=saved.json()['version'];baseline=current(e).json();req=baseline['requirements']
    req['days'][2]['ends_at']='2030-05-03T18:00:00+08:00'
    # Today's location is stale, yet tomorrow's independent plan needs no fresh anchor.
    monkeypatch.setattr(service,'now',lambda:moment+timedelta(minutes=16))
    p=edit_future(e,req);assert p.status_code==200 and p.json()['status']=='preview',p.text
    plan=p.json()['result']['itineraries'][0]
    assert plan['days'][0]==baseline['itinerary']['days'][0]
    assert plan['days'][1]==baseline['itinerary']['days'][1]
    result=confirm(e,p.json());assert result.status_code==200,result.text


@pytest.mark.parametrize('change',['today','other_future','budget','global_mode','overnight','goal_elsewhere'])
def test_selected_scope_rejects_changes_to_locked_objects(multi_engine,change):
    e,req,_=committed(multi_engine);changed=copy.deepcopy(req)
    if change=='today':changed['days'][0]['starts_at']='2030-05-01T10:00:00+08:00'
    elif change=='other_future':changed['days'][1]['pace']='relaxed'
    elif change=='budget':changed['pricing']=options(total_budget_fen=100000)
    elif change=='global_mode':changed['modes']=['driving']
    elif change=='overnight':changed['overnights'][0]['place_id']='origin'
    else:changed['goals'][0]['duration_seconds']=3000
    before=saved_rows();r=edit_future(e,changed)
    assert r.status_code in (409,422),r.text
    assert saved_rows()==before


@pytest.mark.parametrize('at,day',[('2030-05-02T23:59:00+08:00','day:2030-05-02'),('2030-05-03T00:00:00+08:00','day:2030-05-02')])
def test_today_or_past_day_cannot_enter_future_scope(multi_engine,monkeypatch,at,day):
    e,req,_=committed(multi_engine);monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat(at))
    before=saved_rows();r=edit_future(e,req,day);assert r.status_code==422,r.text
    assert saved_rows()==before


def test_cannot_silently_drop_required_but_explicit_cancel_is_previewed(multi_engine):
    e,req,old=committed(multi_engine);changed=copy.deepcopy(req);goal=changed['goals'].pop()
    assert edit_future(e,changed).status_code==409
    p=edit_future(e,changed,skipped_goal_ids=[goal['goal_id']]);assert p.status_code==200 and p.json()['status']=='preview',p.text
    assert p.json()['changes']['days'][2]['removed']==[goal['goal_id']]
    result=confirm(e,p.json());assert result.status_code==200,result.text
    assert not result.json()['itinerary']['days'][2]['itinerary']['activities']


def test_new_goal_and_order_stay_in_selected_day(multi_engine):
    e,req,_=committed(multi_engine);changed=copy.deepcopy(req)
    changed['goals'].append({'goal_id':'future-food','target_kind':'place','target_id':'restaurant','kind':'dining',
        'required':True,'duration_seconds':900,'allowed_dates':['2030-05-03'],'fixed_date':'2030-05-03'})
    p=edit_future(e,changed,fixed_goal_ids=['future-food','visit:2030-05-03'])
    assert p.status_code==200 and p.json()['candidate_id'],p.text
    stops=p.json()['result']['itineraries'][0]['days'][2]['itinerary']['activities']
    assert [a['goal_id'] for a in stops]==['future-food','visit:2030-05-03']
    assert confirm(e,p.json()).status_code==200
    changed['goals'][-1]['allowed_dates']=['2030-05-02','2030-05-03']
    e[1]['version']+=1
    # A new request must use the current saved original; moving a goal remains forbidden.
    assert edit_future(e,changed).status_code==409


@pytest.mark.parametrize('change',['midnight','version','fact','hotel','other_day_tamper','budget_tamper'])
def test_confirmation_rechecks_future_scope_and_is_atomic(multi_engine,monkeypatch,change):
    e,req,_=committed(multi_engine);p=edit_future(e,req).json();assert p['candidate_id'],p
    with connect() as db:
        if change=='midnight':monkeypatch.setattr(service,'now',lambda:datetime.fromisoformat('2030-05-03T00:01:00+08:00'))
        elif change=='version':db.execute('UPDATE city_trip_states SET version=version+1 WHERE id=?',(e[1]['id'],))
        elif change=='fact':db.execute("DELETE FROM city_engine_operations WHERE kind='journey'")
        elif change=='hotel':
            from app.city import travel_services
            w=travel_services.read(db,e[1]['id']);w['version']+=1;travel_services.write(db,e[1]['id'],w)
        else:
            data=json.loads(db.execute('SELECT result_json FROM city_engine_candidates WHERE id=?',(p['candidate_id'],)).fetchone()[0])
            if change=='other_day_tamper':data['itineraries'][0]['days'][0]['requirements']['pace']='relaxed'
            else:data['itineraries'][0]['known_cost_upper_fen']+=100
            db.execute('UPDATE city_engine_candidates SET result_json=? WHERE id=?',(json.dumps(data),p['candidate_id']))
    before=saved_rows();r=confirm(e,p,version=3 if change=='version' else 2)
    assert r.status_code==409,r.text
    assert saved_rows()==before


def test_future_replan_keeps_hotel_and_whole_budget(hotel_trip):
    from test_city_engine_api import preview
    e,req=hotel_trip;price_ops(e);priced_select(e);req['pricing']=options(total_budget_fen=180000)
    saved=confirm(e,preview(e,req).json());assert saved.status_code==200,saved.text
    e[1]['version']=saved.json()['version'];baseline=current(e).json();changed=baseline['requirements']
    changed['days'][2]['starts_at']='2030-05-03T10:00:00+08:00'
    p=edit_future(e,changed);assert p.status_code==200 and p.json()['candidate_id'],p.text
    plan=p.json()['result']['itineraries'][0]
    assert plan['overnights']==baseline['itinerary']['overnights']
    assert plan['budget']['by_category']['lodging']==30000
    assert plan['budget']['by_category']['dining']==6000
    assert confirm(e,p.json()).status_code==200


@pytest.mark.parametrize('kind',['planning','rest','skip'])
@pytest.mark.parametrize('active',[False,True])
def test_future_agent_to_engine_context_and_confirmation(multi_engine,monkeypatch,kind,active):
    from app import language
    from app.city.agent import provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    e,req,old=committed(multi_engine)
    if active:
        moment=start_today(e,monkeypatch)
    else:moment=datetime.fromisoformat('2030-04-30T08:00:00+08:00')
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    agent.TASKS.clear()
    if kind=='planning':
        text='第三天改为十点出发，少走路'
        raw={'planning':{'operation':'update','quote':text,'fields':[
            {'field':'start_time','value':'10:00','quote':'十点出发','day_ref':'第三天'},
            {'field':'objective','value':'less_walking','quote':'少走路','day_ref':'第三天'}]}}
    else:
        text='第三天先休息20分钟' if kind=='rest' else '第三天的下一站不去了，重新安排'
        raw={'state_request':{'operation':'rest' if kind=='rest' else 'skip_replan','quote':text,
            'scope':'selected_future_day','scope_quote':'第三天','minutes':20 if kind=='rest' else None,'next_stop':kind=='skip'}}
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    city={'client':e[0],'trip':e[1]};before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()
    task=run(city,text);assert task['status']=='needs_confirmation',json.dumps(task,ensure_ascii=False)
    result=task['result'];p=result['planning'];assert p['scope']=='selected_future_day'
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before
    assert p['result']['itineraries'][0]['days'][:2]==old['itinerary']['days'][:2]
    if kind=='planning':assert p['result']['itineraries'][0]['days'][2]['requirements']['starts_at'].endswith('10:00:00+08:00')
    if kind=='rest':assert any(a['kind']=='rest' for a in p['result']['itineraries'][0]['days'][2]['itinerary']['activities'])
    pending=e[0].get('/api/city/v1/trips/'+e[1]['id']+'/assistant/context?purpose=modify').json()['pending_changes']
    candidate=next(c for c in pending if c.get('candidate_id')==p['candidate_id'])
    assert candidate['scope']=='selected_future_day' and candidate['day_id']=='day:2030-05-03' and candidate['status']=='valid'
    response=e[0].post('/api/city/v1/trips/'+e[1]['id']+'/assistant/confirm',json={'request_id':rid(),'confirmed':True,**result['confirmation']})
    assert response.status_code==200,response.text
    assert current(e).json()['itinerary']['days'][:2]==old['itinerary']['days'][:2]
    agent.TASKS.clear()
