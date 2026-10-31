"""Basket projection shares the single-day mapping, using isolated synthetic facts."""
from uuid import UUID,uuid5
import pytest
from app.db import connect
from app.city import facts
from app.city.engine.models import MultiDayConstraints
from test_multi_day_api import multi_engine
from test_city_engine_api import engine,rid,confirm
from test_exploration import base,collect
from multi_day_helpers import requirements


@pytest.fixture
def basket_trip(multi_engine):
    e,_=multi_engine;req=requirements(2)
    req['goals']=[]
    for d in req['days']:d['ends_at']=d['business_date']+'T20:00:00+08:00'
    values=MultiDayConstraints.model_validate(req|{'schema_version':'city-constraints/2'}).model_dump(mode='json')
    return e,values


def preview(e,values):
    c,t,_=e;b=c.get(base(t)+'/basket').json()
    response=c.post(base(t)+'/exploration/preview',json={'request_id':rid(),'expected_version':t['version'],
        'basket_version':b['version'],'constraints':values})
    assert response.status_code==200,response.text
    return response.json()


def goal_id(e,key):return 'basket:'+uuid5(UUID(e[1]['id']),key).hex


@pytest.mark.parametrize('matching',[True,False])
def test_direct_preview_links_only_matching_overnight_endpoints(basket_trip,matching):
    e,values=basket_trip;c,t,_=e
    assert collect(c,t,'place','museum','must').status_code==200
    for d in values['days']:d['origin_place_id']=d['destination_place_id']='hotel'
    if not matching:values['days'][1]['origin_place_id']='origin'
    values.pop('overnights')
    before=c.get(base(t)).json()
    result=preview(e,values)
    nights=result['constraints']['overnights']
    assert c.get(base(t)+'/exploration/constraints').json()['constraints']==result['constraints']
    assert c.get(base(t)).json()==before
    if matching:
        assert result['status']=='preview' and result['result']['status']=='feasible',result
        assert len(nights)==1 and nights[0]['source_kind']=='user_place' and nights[0]['place_id']=='hotel'
        assert all(p['overnights'][0]['place_id']=='hotel' for p in result['result']['itineraries'])
        assert not any(i['code']=='OVERNIGHT_PENDING' for i in result['result']['issues'])
    else:
        assert nights==[] and result['status']=='needs_data',result
        assert any(i['code']=='OVERNIGHT_PENDING' for i in result['result']['issues'])
        assert not result.get('candidate_id')


def test_two_basket_places_enter_whole_plan_and_saved_constraints(basket_trip):
    e,values=basket_trip;c,t,_=e
    for pid in ('museum','garden'):assert collect(c,t,'place',pid).status_code==200
    result=preview(e,values);assert result['status']=='preview',result
    goals=result['constraints']['goals']
    assert len(goals)==2 and {g['target_id'] for g in goals}=={'museum','garden'}
    assert all(g['allowed_dates']==['2030-05-01','2030-05-02'] and g['fixed_date'] is None for g in goals)
    assert {g['goal_id'] for g in goals}=={goal_id(e,'place:'+pid) for pid in ('museum','garden')}
    assert any(d['itinerary']['activities'] for p in result['result']['itineraries'] for d in p['days'])
    saved=c.get(base(t)+'/exploration/constraints').json()
    assert saved['constraints']['goals']==goals
    with connect() as db:
        import json
        req=json.loads(db.execute('SELECT requirements_json FROM city_engine_candidates WHERE id=?',(result['candidate_id'],)).fetchone()[0])
    assert req['goals']==goals
    assert c.get(base(t)).json()['route_version']==0
    committed=confirm(e,result)
    assert committed.status_code==200,committed.text
    assert committed.json()['route_version']==1


@pytest.mark.parametrize('priority,required,importance',[('must',True,10),('want',False,6),('optional',False,1)])
@pytest.mark.parametrize('allowed_dates',[['2030-05-02'],['2030-05-01','2030-05-02']])
def test_existing_day_choice_keeps_scope_and_updates_current_priority(basket_trip,priority,required,importance,allowed_dates):
    e,values=basket_trip;c,t,_=e
    collect(c,t,'place','museum',priority)
    fixed={'goal_id':goal_id(e,'place:museum'),'target_kind':'place','target_id':'museum','required':not required,
        'importance':3,'allowed_dates':allowed_dates,'fixed_date':'2030-05-02','duration_seconds':600}
    values['goals']=[fixed]
    result=preview(e,values);assert result['status']=='preview',result
    goal=result['constraints']['goals'][0]
    assert goal['allowed_dates']==fixed['allowed_dates'] and goal['fixed_date']==fixed['fixed_date']
    assert (goal['required'],goal['importance'],goal['duration_seconds'])==(required,importance,600)
    activities=[a for p in result['result']['itineraries'] for d in p['days'] for a in d['itinerary']['activities'] if a['goal_id']==goal['goal_id']]
    assert activities and all(a['starts_at'].startswith('2030-05-02') and a['required']==required for a in activities)


@pytest.mark.parametrize('change',['remove','standby'])
def test_removed_or_standby_basket_goal_disappears_but_formal_goal_stays(basket_trip,change):
    e,values=basket_trip;c,t,_=e
    collect(c,t,'place','museum');collect(c,t,'place','garden',status='standby')
    values['goals']=[{'goal_id':'formal:locked','target_kind':'place','target_id':'restaurant','kind':'dining',
        'required':True,'importance':9,'allowed_dates':['2030-05-02'],'fixed_date':'2030-05-02'}]
    first=preview(e,values);assert first['status']=='preview',first
    assert {g['goal_id'] for g in first['constraints']['goals']}=={'formal:locked',goal_id(e,'place:museum')}
    collect(c,t,'place','museum',**({'operation':'remove'} if change=='remove' else {'status':'standby'}))
    result=preview(e,first['constraints']);assert result['status']=='preview',result
    assert result['constraints']['goals']==[first['constraints']['goals'][0]]


@pytest.mark.parametrize('stale',[False,True])
def test_empty_basket_never_previews_an_empty_plan(basket_trip,stale):
    e,values=basket_trip;c,t,_=e
    if stale:
        collect(c,t,'place','museum')
        values=preview(e,values)['constraints']
        collect(c,t,'place','museum',operation='remove')
    result=preview(e,values)
    assert result['status']=='needs_data'
    assert result['reply']=='请先在素材篮里加入想去的地点，再生成多天方案。'
    assert not result.get('candidate_id') and not result.get('planning') and not result.get('result')
    assert result['constraints']['goals']==[]
    assert c.get(base(t)+'/exploration/constraints').json()['constraints']['goals']==[]


def test_unmapped_basket_item_is_reported_without_entering_goals(basket_trip):
    e,values=basket_trip;c,t,_=e
    collect(c,t,'place','museum');collect(c,t,'food','chaoliangfen')
    result=preview(e,values)
    assert result['status']=='preview' and result['result']['status']=='feasible',result
    assert [g['target_id'] for g in result['constraints']['goals']]==['museum']
    assert result['unscheduled']['0']==[{'goal_id':'unmapped:food:chaoliangfen','key':'food:chaoliangfen',
        'name':'炒凉粉','reason_code':'data_pending','reason':'资料待添加，暂时排不进行程'}]
    assert result.get('candidate_id')


def test_parent_park_and_child_share_single_day_pending_gate(basket_trip):
    e,values=basket_trip;c,t,_=e
    with connect() as db:
        child=facts.records(db)['place','garden']
        facts.import_bundle(facts.FactBundle(records=[child.model_copy(update={'version':child.version+1,'parent_place_id':'museum'})]),db,apply=True)
    collect(c,t,'place','museum');collect(c,t,'place','garden')
    values['goals']=[{'goal_id':goal_id(e,'place:garden'),'target_kind':'place','target_id':'garden',
        'allowed_dates':['2030-05-02'],'fixed_date':'2030-05-02'}]
    result=preview(e,values)
    assert result['status']=='needs_data' and result['issues'][0]['code']=='PARENT_CHILD_PENDING'
    assert result['issues'][0]['affected_ids']==['garden'] and not result.get('candidate_id')


@pytest.mark.parametrize('kind,identity,target_kind,target_id,activity_kind',[
    ('vendor','vendor','place','restaurant','dining'),('place','restaurant','place','restaurant','dining'),
    ('food','food','food','food','dining'),('show','s1','session','s1','show'),('program','program','program','program','show')])
def test_basket_fact_types_use_shared_goal_mapping(basket_trip,kind,identity,target_kind,target_id,activity_kind):
    e,values=basket_trip;c,t,_=e
    assert collect(c,t,kind,identity).status_code==200
    result=preview(e,values);goal=result['constraints']['goals'][0]
    assert (goal['target_kind'],goal['target_id'],goal['kind'],goal['required'],goal['importance'])==(target_kind,target_id,activity_kind,False,6)


def test_assistant_multi_day_preview_uses_the_same_basket_projection(basket_trip,monkeypatch):
    from app import language
    from app.city.agent import provider
    from test_city_agent import run
    e,values=basket_trip;c,t,_=e
    collect(c,t,'place','museum');collect(c,t,'place','garden')
    from app.city import exploration_state
    with connect() as db:exploration_state.save(db,t['id'],values,None,0)
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'modify','quote':'继续安排'}],
        'planning':{'operation':'continue','quote':'继续安排','fields':[]}},{'source':'mock_model','http_attempts':0}))
    task=run({'client':c,'trip':t},'继续安排')
    assert task['status']=='needs_confirmation',task
    result=task['result']['planning_workflow']['result']
    assert len(result['constraints']['goals'])==2
    assert any(d['itinerary']['activities'] for p in result['result']['itineraries'] for d in p['days'])


def test_assistant_empty_basket_returns_the_same_prompt(basket_trip,monkeypatch):
    from app import language
    from app.city import exploration_state
    from app.city.agent import provider
    from test_city_agent import run
    e,values=basket_trip;c,t,_=e
    with connect() as db:exploration_state.save(db,t['id'],values,None,0)
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'modify','quote':'继续安排'}],
        'planning':{'operation':'continue','quote':'继续安排','fields':[]}},{'source':'mock_model','http_attempts':0}))
    task=run({'client':c,'trip':t},'继续安排')
    assert task['status']=='needs_data',task
    assert task['result']['reply']=='请先在素材篮里加入想去的地点，再生成多天方案。'
    assert not task['result']['planning_workflow']['result'].get('candidate_id')
