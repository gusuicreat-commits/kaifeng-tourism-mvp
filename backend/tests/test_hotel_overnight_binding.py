"""Hotel -> two daily endpoints -> route confirmation, on synthetic local data only."""
import copy
import json

import pytest

from app.city import hotel_binding, hotels, travel_services
from app.city.agent.requirement_context import planning_values
from app.db import connect
from test_city_engine_api import engine, preview, confirm, current as current_route, rid
from test_multi_day_api import multi_engine


def mapping(db, candidate='flyai:1', place='hotel', revision=1):
    hotel_binding.import_binding(db,{'candidate_id':candidate,'place_id':place,'revision':revision,
        'source':'synthetic hotel identity mapping for isolated tests',
        'checked_at':'2020-04-01T00:00:00+08:00','valid_from':'2020-04-01T00:00:00+08:00',
        'valid_until':'2030-06-01T00:00:00+08:00'})


@pytest.fixture
def hotel_trip(multi_engine,monkeypatch):
    e,req=multi_engine;c,t,_=e
    def search(_):
        return hotels.normalize({'status':0,'data':{'itemList':[
            {'shId':'1','name':'[合成]甲酒店','address':'甲测试地址','price':'¥188'},
            {'shId':'2','name':'[合成]乙酒店','address':'乙测试地址','price':'¥288'}]}})
    monkeypatch.setattr(hotels,'search',search)
    # Save incomplete planning intentions, before any formal route exists.
    from app.city import exploration_state
    values={k:v for k,v in req.items() if k not in ('alternatives','solve_seconds')}
    values['schema_version']='city-constraints/2'
    # Previously explicit first/last endpoints must survive hotel replacement.
    values['days'][-1]['destination_place_id']='origin'
    with connect() as db:
        exploration_state.save(db,t['id'],values,None,0)
        mapping(db);mapping(db,'flyai:2','origin')
    return e,req


def select(e,candidate='flyai:1',start='2030-05-01',nights=2):
    c,t,_=e;p='/api/city/v1/trips/'+t['id']
    w=c.get(p+'/services').json()
    q=c.post(p+'/services/query',json={'text':f'找酒店 {start} 入住，住{nights}晚','expected_version':w['version']})
    assert q.status_code==200,q.text
    w=q.json()['services']
    pvw=c.post(p+'/services/preview',json={'request_id':rid(),'expected_version':w['version'],'candidate_id':candidate})
    assert pvw.status_code==200,pvw.text
    body={'request_id':rid(),'expected_version':w['version'],'preview_id':pvw.json()['preview_id'],'confirmed':True}
    return p,c.post(p+'/services/confirm',json=body),body


def test_selection_projects_three_days_and_formal_confirm_connects_both_nights(hotel_trip):
    e,req=hotel_trip;c,t,_=e;before=c.get('/api/city/v1/trips/'+t['id']).json()
    p,r,body=select(e);assert r.status_code==200,r.text
    assert r.json()['binding']['status']=='bound_for_preview'
    assert not r.json()['formal_state_changed']
    assert c.post(p+'/services/confirm',json=body).json()==r.json()
    assert c.get(p).json()==before  # Selection never rewrites a confirmed/draft itinerary.
    proposed=copy.deepcopy(req)
    proposed['days'][0]['destination_place_id']='origin'
    proposed['days'][1]['origin_place_id']='origin'
    proposed['days'][1]['destination_place_id']='origin'
    proposed['days'][2]['origin_place_id']='origin'
    proposed['days'][-1]['destination_place_id']='origin'
    proposed['overnights']=[]
    response=preview(e,proposed);assert response.status_code==200,response.text
    assert response.json()['status']=='preview',response.text
    plan=response.json()['result']['itineraries'][0]
    assert plan['days'][0]['requirements']['origin_place_id']=='origin'
    assert plan['days'][-1]['requirements']['destination_place_id']=='origin'
    for a,b,o in zip(plan['days'],plan['days'][1:],plan['overnights']):
        assert a['requirements']['destination_place_id']==b['requirements']['origin_place_id']==o['place_id']=='hotel'
        assert o['source_kind']=='selected_hotel' and o['service_selection_id']=='flyai:1'
        assert o['binding_revision']==1 and o['service_version']==2
        assert o['stay_evidence']=='unknown'
    committed=confirm(e,response.json());assert committed.status_code==200,committed.text
    assert committed.json()['route_version']==1
    assert not current_route(e).json()['stale']
    saved=c.get(p).json()['draft']
    for a,b in zip(saved['days'],saved['days'][1:]):
        assert a['destination_place_id']==b['origin_place_id']=='hotel'
    export=c.get('/api/v1/memory/export').json()['city_trips'][0]
    assert export['travel_services']['selection']['hotels']['stay']['check_out']=='2030-05-03'
    assert json.loads(export['planning_engine']['versions'][0]['itinerary_json'])==committed.json()['itinerary']


def test_missing_mapping_degrades_to_a_note_and_keeps_original_endpoints(hotel_trip):
    """An unmapped hotel never blocks planning; it keeps the original overnight and says why."""
    e,req=hotel_trip;c,t,_=e
    with connect() as db:db.execute('DELETE FROM city_hotel_binding_metadata')
    p,r,_=select(e);assert r.status_code==200,r.text
    binding=r.json()['binding']
    assert binding['status']=='location_pending' and binding['issues'][0]['code']=='HOTEL_LOCATION_PENDING'
    assert '暂按原起终点安排' in binding['message']
    response=preview(e,req);assert response.status_code==200,response.text
    assert response.json()['candidate_id'] is not None
    assert 'HOTEL_LOCATION_PENDING' not in {i['code'] for i in response.json()['result'].get('issues',[])}
    assert c.get(p).json()['route_version']==0
    with connect() as db:
        from types import SimpleNamespace
        from app.city import exploration_state
        values=planning_values(SimpleNamespace(multi_draft=None,workspace=exploration_state.read(db,t['id']),
            services=travel_services.read(db,t['id'])))
    # The provider name never becomes an endpoint, and no selected_hotel night is invented.
    assert values['days'][1].get('origin_text')!='[合成]甲酒店'
    assert not any(o.get('source_kind')=='selected_hotel' for o in values.get('overnights',[]))
    from app.city.agent.multi_day_intent import _questions
    questions=_questions(values)
    assert not any('已选酒店的位置' in q for q in questions)
    assert not any('先选择酒店' in q for q in questions)


@pytest.mark.parametrize('start,nights',[('2030-05-02',1),('2030-05-01',1),('2030-05-05',1)])
def test_stay_mismatch_does_not_attach_hotel_to_other_nights(hotel_trip,start,nights):
    e,req=hotel_trip;p,r,_=select(e,start=start,nights=nights)
    assert r.status_code==200 and r.json()['binding']['issues'][0]['code']=='HOTEL_STAY_RANGE_MISMATCH'
    response=preview(e,req)
    assert response.status_code==200 and response.json()['status']=='needs_data',response.text
    assert response.json()['result']['issues'][0]['code']=='HOTEL_STAY_RANGE_MISMATCH'


@pytest.mark.parametrize('change',['switch','undo','search','mapping','mapping_removed','fact'])
def test_old_route_preview_rejected_after_selection_or_mapping_changes(hotel_trip,change):
    e,req=hotel_trip;c,t,_=e;p,r,_=select(e);assert r.status_code==200
    candidate=preview(e,req).json();assert candidate['status']=='preview',candidate
    if change=='switch':select(e,'flyai:2')
    elif change=='undo':
        assert c.post(p+'/services/undo',json={'expected_version':r.json()['workspace']['version']}).status_code==200
    elif change=='search':
        w=c.get(p+'/services').json()
        c.post(p+'/services/query',json={'text':'换一个酒店，200元以内','expected_version':w['version']})
    elif change=='mapping':
        with connect() as db:mapping(db,place='origin',revision=2)
    elif change=='mapping_removed':
        with connect() as db:db.execute("DELETE FROM city_object_bindings WHERE kind='flyai_hotel'")
    else:
        with connect() as db:db.execute("DELETE FROM city_engine_operations WHERE kind='journey'")
    r=confirm(e,candidate);assert r.status_code==409,r.text
    assert c.get(p).json()['route_version']==0


def test_switch_undo_restore_preserve_formal_history_and_projection(hotel_trip):
    e,req=hotel_trip;c,t,_=e;p,r,_=select(e)
    old=confirm(e,preview(e,req).json());assert old.status_code==200,old.text
    e[1]['version']=old.json()['version']
    saved=c.get(p).json();old_plan=old.json()['itinerary']
    _,r,_=select(e,'flyai:2');assert r.status_code==200,r.text
    assert c.get(p).json()==saved
    assert current_route(e).json()['stale']
    next_plan=preview(e,req);assert next_plan.json()['status']=='preview',next_plan.text
    assert next_plan.json()['result']['itineraries'][0]['overnights'][0]['place_id']=='origin'
    w=c.post(p+'/services/undo',json={'expected_version':r.json()['workspace']['version']}).json()
    assert not current_route(e).json()['stale']
    restored=preview(e,req);assert restored.json()['status']=='preview',restored.text
    assert restored.json()['result']['itineraries'][0]['overnights'][0]['place_id']=='hotel'
    w=c.post(p+'/services/undo',json={'expected_version':w['version']}).json()
    assert w['selection']['hotels']['id']=='flyai:2'
    assert c.get(p+'/engine/versions/1').json()['itinerary']==old_plan
    new=confirm(e,preview(e,req).json());assert new.status_code==200,new.text
    assert new.json()['route_version']==2


def test_selected_intention_survives_search_expiry_and_new_search_dates(hotel_trip):
    e,req=hotel_trip;c,t,_=e;p,r,_=select(e)
    with connect() as db:
        w=travel_services.read(db,t['id'])
        w['selection']['hotels']['expires_at']='2020-01-01T00:00:00+08:00'
        w['requirements'].update(check_in='2030-05-09',check_out='2030-05-10')
        travel_services.write(db,t['id'],w)
    response=preview(e,req);assert response.json()['status']=='preview',response.text
    assert response.json()['result']['itineraries'][0]['overnights'][0]['check_in'] is None
    assert c.get(p+'/services').json()['selection']['hotels']['stay']['check_in']=='2030-05-01'


@pytest.mark.parametrize('change',['expired','trip_version','service_version'])
def test_service_confirmation_rejects_expired_or_changed_preview_atomically(hotel_trip,change):
    e,req=hotel_trip;c,t,_=e;p,r,_=select(e)
    w=c.get(p+'/services').json()
    pre=c.post(p+'/services/preview',json={'request_id':rid(),'expected_version':w['version'],'candidate_id':'flyai:2'}).json()
    with connect() as db:
        if change=='trip_version':db.execute('UPDATE city_trip_states SET version=version+1 WHERE id=?',(t['id'],))
        else:
            ws=travel_services.read(db,t['id'])
            if change=='expired':ws['result']['preview']['expires_at']='2020-01-01T00:00:00+08:00'
            else:ws['version']+=1
            travel_services.write(db,t['id'],ws)
    before=c.get(p+'/services').json()
    r=c.post(p+'/services/confirm',json={'request_id':rid(),'expected_version':w['version'],
        'preview_id':pre['preview_id'],'confirmed':True})
    assert r.status_code==409 and c.get(p+'/services').json()==before


def test_mapping_requires_metadata_and_monotone_revision(hotel_trip):
    e,_=hotel_trip
    with connect() as db:
        mapping(db)  # identical re-import is safe
        with pytest.raises(ValueError,match='修订号'):mapping(db,place='origin')
        db.execute("UPDATE city_object_bindings SET fact_id='origin' WHERE kind='flyai_hotel' AND object_id='flyai:1'")
        assert hotel_binding.location(db,{'id':'flyai:1'}) is None


def test_undo_allows_fresh_hotel_question_in_saved_constraints(hotel_trip):
    e,req=hotel_trip;c,t,_=e;p,r,_=select(e)
    from app.city import exploration_state
    with connect() as db:
        w=travel_services.read(db,t['id']);base=exploration_state.read(db,t['id'])
        projected,_=hotel_binding.project(db,base['constraints'],w)
        exploration_state.save(db,t['id'],projected,None,0)
    c.post(p+'/services/undo',json={'expected_version':r.json()['workspace']['version']})
    with connect() as db:
        values,issues=hotel_binding.project(db,projected,travel_services.read(db,t['id']))
    assert not issues and not values['overnights']
    assert values['days'][0]['destination_place_id'] is None


def test_basket_preview_fills_missing_overnight_endpoints_and_can_be_confirmed(hotel_trip):
    e,req=hotel_trip;c,t,_=e;p,r,_=select(e)
    from app.city import exploration_state
    with connect() as db:w=exploration_state.read(db,t['id'])
    values=copy.deepcopy(w['constraints']);values['overnights']=[]
    for a,b in zip(values['days'],values['days'][1:]):
        a['destination_place_id']=b['origin_place_id']=None
    response=c.post(p+'/exploration/preview',json={'request_id':rid(),'expected_version':t['version'],
        'basket_version':0,'planning_version':w['version'],'constraints':values})
    assert response.status_code==200 and response.json()['status']=='preview',response.text
    assert response.json()['constraints']['overnights'][0]['source_kind']=='selected_hotel'
    result=confirm(e,response.json());assert result.status_code==200,result.text


def test_agent_continue_uses_selected_hotel_and_confirms_whole_route(hotel_trip,monkeypatch):
    e,req=hotel_trip;c,t,_=e;p,r,_=select(e)
    from app import language
    from app.city.agent import provider,service as agent_service
    from test_city_agent import run
    agent_service.TASKS.clear()
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'modify','quote':'继续安排'}],
        'planning':{'operation':'continue','quote':'继续安排','fields':[]}},{'source':'mock_model','http_attempts':0}))
    task=run({'client':c,'trip':t},'继续安排')
    assert task['status']=='needs_confirmation',task
    result=task['result']['planning_workflow']['result']
    assert len(result['result']['itineraries'][0]['days'])==3
    assert {o['place_id'] for o in result['result']['itineraries'][0]['overnights']}=={'hotel'}
    committed=confirm(e,result);assert committed.status_code==200,committed.text


def test_two_day_route_has_exactly_one_hotel_boundary_and_keeps_range(hotel_trip):
    e,req=hotel_trip;select(e)
    data=copy.deepcopy(req);data['days']=data['days'][:2];data['end_date']='2030-05-02'
    data['days'][-1]['destination_place_id']='origin'
    data['goals']=data['goals'][:2];data['overnights']=[]
    response=preview(e,data);assert response.status_code==200 and response.json()['status']=='preview',response.text
    plan=response.json()['result']['itineraries'][0]
    assert len(plan['days'])==2 and len(plan['overnights'])==1
    assert plan['end_date']=='2030-05-02'
    assert plan['days'][0]['requirements']['destination_place_id']==plan['days'][1]['requirements']['origin_place_id']=='hotel'
    assert plan['days'][1]['requirements']['destination_place_id']=='origin'


def test_new_trip_cannot_forge_complete_hotel_selection_metadata(hotel_trip):
    e,_=hotel_trip
    from multi_day_helpers import draft
    values=draft(2)
    values['overnights'][0].update(source_kind='selected_hotel',service_selection_id='flyai:1',
        service_version=2,binding_revision=1)
    r=e[0].post('/api/city/v1/trips',json={'request_id':rid(),'draft':values})
    assert r.status_code==422 and r.json()['detail']['code']=='INVALID_HOTEL_BINDING'
