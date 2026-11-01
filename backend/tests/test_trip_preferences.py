"""Preferences project synthetic basket facts; no provider or runtime data is used."""
import json
import pytest
from pydantic import ValidationError
from app.db import connect
from app.city import exploration,facts
from app.city.engine.models import Goal,MultiDayConstraints
from app.city.preferences import TripPreferences
from test_city_engine_api import engine,confirm
from test_exploration import base,collect,plan
from test_multi_day_api import multi_engine
from test_multi_day_basket_goals import basket_trip,preview


def change_fact(kind,identity,**changes):
    with connect() as db:
        fact=facts.records(db)[kind,identity]
        facts.import_bundle(facts.FactBundle(records=[fact.model_copy(update={'version':fact.version+1,**changes})]),db,apply=True)


def candidate_goals(result):
    with connect() as db:
        row=db.execute('SELECT requirements_json FROM city_engine_candidates WHERE id=?',((result.get('planning') or result)['candidate_id'],)).fetchone()
    return json.loads(row[0])['goals']


def assert_excluded(result,name,reason):
    plans=(result.get('planning') or result).get('result',{}).get('itineraries',[]) or [None]
    assert set(result['unscheduled'])=={str(i) for i in range(len(plans))}
    for rows in result['unscheduled'].values():
        assert rows[0]['name']==name and rows[0]['reason_code']=='preference_excluded' and rows[0]['reason']==reason
        assert all('cause' not in row for row in rows)


@pytest.mark.parametrize('category,kind,identity,patch,name',[
    ('shows','show','s1',None,'演出'),('food','food','food',None,'美食'),
    ('night','place','garden','night_visit','夜游'),('scenic','place','garden','scenic','景区')])
def test_each_category_is_excluded_before_planning(engine,category,kind,identity,patch,name):
    c,t,_=engine
    if patch:change_fact('place',identity,place_type=patch)
    collect(c,t,'place','museum');collect(c,t,kind,identity)
    item=next(i for i in c.get(base(t)+'/basket').json()['items'] if i['id']==identity)
    result=plan(engine,preferences={'avoid':[category]}).json()
    assert result['status']=='feasible',result
    assert_excluded(result,item['name'],'你说过不想安排'+name)
    assert all(g['target_id']!=identity for g in candidate_goals(result))
    assert c.get(base(t)).json()['route_version']==0
    assert c.get(base(t)+'/exploration/constraints').json()['constraints']['preferences']=={'spice':None,'interests':[],'avoid':[category]}
    assert len(c.get(base(t)+'/basket').json()['items'])==2
    assert confirm(engine,result['planning']).status_code==200


@pytest.mark.parametrize('target_kind,target_id,kind,expected',[
    ('session','s1','show',{'shows','scenic'}),('program','program','show',{'shows','scenic'}),
    ('place','stage1','visit',{'shows','scenic'}),('place','garden','visit',{'scenic'}),
    ('place','restaurant','dining',{'food'}),('food','food','dining',{'food'})])
def test_shared_category_classifier_includes_park_children(engine,target_kind,target_id,kind,expected):
    change_fact('place','museum',place_type='scenic')
    for identity in ('garden','stage1'):change_fact('place',identity,parent_place_id='museum')
    change_fact('program','program',place_id='stage1')
    with connect() as db:items=facts.records(db)
    assert exploration.goal_categories(Goal(goal_id='g',target_kind=target_kind,target_id=target_id,kind=kind),items)==expected


@pytest.mark.parametrize('accept,actual,excluded',[
    ('none','none',False),('none','mild',True),('none','hot',True),('none',None,False),
    ('mild','none',False),('mild','mild',False),('mild','hot',True),('mild',None,False),
    ('any','hot',False),(None,'hot',False)])
@pytest.mark.parametrize('kind,identity',[('place','restaurant'),('vendor','vendor'),('food','food')])
def test_spice_acceptance_and_unknown(engine,accept,actual,excluded,kind,identity):
    change_fact('vendor','vendor',spiciness=actual)
    c,t,_=engine;collect(c,t,'place','museum');collect(c,t,kind,identity)
    result=plan(engine,preferences={'spice':accept}).json();assert result['status']=='feasible',result
    if excluded:
        name=next(i['name'] for i in c.get(base(t)+'/basket').json()['items'] if i['id']==identity)
        assert_excluded(result,name,'口味偏辣，和你说的“'+('不吃辣' if accept=='none' else '微辣可以')+'”不符')
        assert len(candidate_goals(result))==1
    else:
        assert len(candidate_goals(result))==2
        assert not any(r['reason_code']=='preference_excluded' for rows in result['unscheduled'].values() for r in rows)
    assert '不辣' not in result['reply']


@pytest.mark.parametrize('second,excluded',[('hot',True),('mild',True),('none',False),(None,False)])
def test_food_only_excluded_if_all_suppliers_exceed(engine,second,excluded):
    change_fact('vendor','vendor',spiciness='hot')
    with connect() as db:
        items=facts.records(db)
        vendor=items['vendor','vendor'].model_copy(update={'fact_id':'vendor:second','version':1,'spiciness':second})
        supply=items['supply','supply'].model_copy(update={'fact_id':'supply:second','vendor_id':vendor.fact_id})
        facts.import_bundle(facts.FactBundle(records=[vendor,supply]),db,apply=True)
    c,t,_=engine;collect(c,t,'place','museum');collect(c,t,'food','food')
    result=plan(engine,preferences={'spice':'none'}).json()
    assert result['status']=='feasible',result
    assert (len(candidate_goals(result))==1)==excluded
    assert any(r['reason_code']=='preference_excluded' for rows in result['unscheduled'].values() for r in rows)==excluded


@pytest.mark.parametrize('preferences,kind,identity',[
    ({'avoid':['food']},'food','food'),({'spice':'none'},'vendor','vendor')])
def test_must_kept_with_explicit_notice(engine,preferences,kind,identity):
    change_fact('vendor','vendor',spiciness='hot')
    c,t,_=engine;collect(c,t,kind,identity,'must')
    name=c.get(base(t)+'/basket').json()['items'][0]['name']
    result=plan(engine,preferences=preferences).json();assert result['status']=='feasible',result
    assert '「'+name+'」你设为必去，已保留，虽然它属于你说过不想安排的类别。' in result['reply']
    assert [(g['required'],g['importance']) for g in candidate_goals(result)]==[(True,10)]
    assert not result['unscheduled']


@pytest.mark.parametrize('priority,weight',[('want',8),('optional',3),('must',10)])
def test_interest_weights_and_repeat_preview_do_not_accumulate(engine,priority,weight):
    c,t,_=engine;collect(c,t,'food','food',priority)
    for _ in range(2):
        result=plan(engine,preferences={'interests':['food']}).json()
        assert candidate_goals(result)[0]['importance']==weight


def test_interest_wins_when_only_one_visit_fits(engine,monkeypatch):
    change_fact('place','garden',place_type='night_visit')
    c,t,_=engine
    for identity in ('museum','garden'):collect(c,t,'place',identity)
    # Equal durations make the two optional visits compete for the same time budget.
    from app.city.engine import service
    original=service.plan_trip
    def equal_durations(req,ctx):
        return original(req.model_copy(update={'goals':tuple(g.model_copy(update={'duration_seconds':1800}) for g in req.goals)}),ctx)
    monkeypatch.setattr(service,'plan_trip',equal_durations)
    result=plan(engine,ends_at='2030-05-01T10:00:00+08:00',preferences={'interests':['night']}).json()
    assert result['status']=='feasible',result
    assert [(g['target_id'],g['importance']) for g in candidate_goals(result)]==[('museum',6),('garden',8)]
    activities=result['planning']['result']['itineraries'][0]['activities']
    assert [a['place_id'] for a in activities]==['garden']


def test_multi_day_preferences_project_save_and_confirm(basket_trip):
    e,values=basket_trip;c,t,_=e
    collect(c,t,'place','museum');collect(c,t,'food','food')
    values['preferences']={'avoid':['food'],'interests':['scenic']}
    result=preview(e,values);assert result['status']=='preview',result
    name=next(i['name'] for i in c.get(base(t)+'/basket').json()['items'] if i['id']=='food')
    assert_excluded(result,name,'你说过不想安排美食')
    assert [g['target_id'] for g in result['constraints']['goals']]==['museum']
    saved=c.get(base(t)+'/exploration/constraints').json()
    assert saved['constraints']['preferences']==result['constraints']['preferences']
    assert saved['result']['unscheduled']==result['unscheduled']
    assert confirm(e,result).status_code==200


def test_multi_day_must_and_interest_keep_date_scope(basket_trip):
    e,values=basket_trip;c,t,_=e
    collect(c,t,'place','restaurant','must');collect(c,t,'food','food','optional')
    values['preferences']={'spice':'none','interests':['food']}
    change_fact('vendor','vendor',spiciness='hot')
    first=preview(e,values);assert first['status']=='preview',first
    assert '你设为必去，已保留' in first['reply']
    assert [g['target_id'] for g in first['constraints']['goals']]==['restaurant']
    change_fact('vendor','vendor',spiciness=None)
    second=preview(e,first['constraints']);assert second['status']=='preview',second
    goals=second['constraints']['goals']
    assert [(g['target_id'],g['importance']) for g in goals]==[('restaurant',10),('food',3)]
    assert all(g['allowed_dates']==['2030-05-01','2030-05-02'] for g in goals)


@pytest.mark.parametrize('model,base_values',[(exploration.Constraints,{}),(MultiDayConstraints,{'schema_version':'city-constraints/2'})])
def test_exclusive_categories_rejected_in_chinese(model,base_values):
    with pytest.raises(ValidationError,match='同一类别不能同时选为更感兴趣和不想安排：演出'):
        model.model_validate(base_values|{'preferences':{'interests':['shows'],'avoid':['shows']}})


@pytest.mark.parametrize('empty',[None,{}, {'spice':None,'interests':[],'avoid':[]}])
def test_empty_preferences_preserve_single_day_plans(engine,empty):
    c,t,_=engine;collect(c,t,'place','museum');collect(c,t,'food','food')
    before=plan(engine).json();after=plan(engine,preferences=empty).json()
    assert candidate_goals(before)==candidate_goals(after)
    assert before['planning']['result']['itineraries']==after['planning']['result']['itineraries']
    assert before['unscheduled']==after['unscheduled'] and before['reply']==after['reply']


@pytest.mark.parametrize('empty',[None,{}, {'spice':None,'interests':[],'avoid':[]}])
def test_empty_preferences_preserve_multi_day_plans(basket_trip,empty,monkeypatch):
    from app.city.engine import multi_day
    original=multi_day.plan_multi_day
    # Daily solve_seconds records the remaining wall-clock budget in the snapshot.
    monkeypatch.setattr(multi_day,'plan_multi_day',lambda req,ctx:original(req,ctx,clock=lambda:0))
    e,values=basket_trip;c,t,_=e;collect(c,t,'place','museum')
    before=preview(e,values);after=preview(e,values|{'preferences':empty})
    assert candidate_goals(before)==candidate_goals(after)
    assert before['result']['itineraries']==after['result']['itineraries']
    assert before.get('unscheduled')==after.get('unscheduled')


def test_all_excluded_still_explained_and_saved(engine):
    c,t,_=engine;collect(c,t,'food','food')
    result=plan(engine,preferences={'avoid':['food']}).json()
    assert_excluded(result,c.get(base(t)+'/basket').json()['items'][0]['name'],'你说过不想安排美食')
    assert not result.get('planning')
    assert c.get(base(t)+'/exploration/constraints').json()['result']['unscheduled']==result['unscheduled']


@pytest.mark.parametrize('items,code',[
    ((('place','garden'),('food','food')),'PREFERENCE_EXCLUDED_ALL'),
    ((('place','museum'),('food','food')),None),
    ((),'BASKET_EMPTY')])
@pytest.mark.parametrize('multi',[False,True])
def test_preference_exclusion_distinguishes_empty_and_partial(request,multi,items,code):
    e,values=request.getfixturevalue('basket_trip') if multi else (request.getfixturevalue('engine'),{})
    c,t,_=e
    change_fact('place','garden',place_type='scenic')
    for kind,identity in items:collect(c,t,kind,identity)
    preferences={'avoid':['scenic','food','shows']}
    result=preview(e,values|{'preferences':preferences}) if multi else plan(e,preferences=preferences).json()
    if code is None:
        assert result['status']==('preview' if multi else 'feasible'),result
        assert [g['target_id'] for g in candidate_goals(result)]==['museum']
        assert_excluded(result,next(i['name'] for i in c.get(base(t)+'/basket').json()['items'] if i['id']=='food'),'你说过不想安排美食')
    else:
        assert result['status']==('needs_data' if multi else 'unknown'),result
        assert result['issues'][0]['code']==code
        if items:
            names='、'.join(i['name'] for i in c.get(base(t)+'/basket').json()['items'])
            message='按你说的不想安排美食、景区，素材篮里的'+names+'都没有排进去。想保留的话，说一声“美食也可以安排”，或到素材篮调整。'
            assert result['reply']==result['issues'][0]['message']==message
            assert [i['name'] for i in result['unscheduled']['0']]==names.split('、')
            assert all(i['reason_code']=='preference_excluded' for i in result['unscheduled']['0'])
        else:assert not result.get('unscheduled')


@pytest.mark.parametrize('spice,quote',[('none','不吃辣'),('mild','微辣可以')])
@pytest.mark.parametrize('multi',[False,True])
def test_all_excluded_by_spice_uses_actual_reason(request,multi,spice,quote):
    e,values=request.getfixturevalue('basket_trip') if multi else (request.getfixturevalue('engine'),{})
    c,t,_=e;change_fact('vendor','vendor',spiciness='hot');collect(c,t,'food','food')
    preferences={'spice':spice}
    result=preview(e,values|{'preferences':preferences}) if multi else plan(e,preferences=preferences).json()
    name=c.get(base(t)+'/basket').json()['items'][0]['name']
    message='素材篮里的'+name+'口味偏辣，和你说的“'+quote+'”不符，都没有排进去。能接受的话说一声“辣一点也可以”，或到素材篮调整。'
    assert result['issues'][0]['code']=='PREFERENCE_EXCLUDED_ALL'
    assert result['reply']==result['issues'][0]['message']==message
    assert_excluded(result,name,'口味偏辣，和你说的“'+quote+'”不符')
    assert c.get(base(t)+'/exploration/constraints').json()['result']['unscheduled']==result['unscheduled']


@pytest.mark.parametrize('multi',[False,True])
def test_all_excluded_by_category_and_spice_lists_each_reason(request,multi):
    e,values=request.getfixturevalue('basket_trip') if multi else (request.getfixturevalue('engine'),{})
    c,t,_=e;change_fact('vendor','vendor',spiciness='hot')
    for identity in ('garden','museum'):
        change_fact('place',identity,place_type='scenic');collect(c,t,'place',identity)
    collect(c,t,'food','food')
    preferences={'avoid':['night','scenic','shows'],'spice':'none'}
    result=preview(e,values|{'preferences':preferences}) if multi else plan(e,preferences=preferences).json()
    items=c.get(base(t)+'/basket').json()['items']
    names='、'.join(i['name'] for i in items if i['kind']=='place')
    food=next(i['name'] for i in items if i['id']=='food')
    message='按你说的不想安排景区，素材篮里的'+names+'都没有排进去。想保留的话，说一声“景区也可以安排”，或到素材篮调整。'+food+'口味偏辣，也没有排进去。'
    assert result['issues'][0]['code']=='PREFERENCE_EXCLUDED_ALL'
    assert result['reply']==result['issues'][0]['message']==message
    assert [row['name'] for row in result['unscheduled']['0']]==[i['name'] for i in items]
    assert all('cause' not in row for row in result['unscheduled']['0'])
    assert c.get(base(t)+'/exploration/constraints').json()['result']['unscheduled']==result['unscheduled']


def test_preferences_preserve_locked_formal_goals(engine):
    from test_city_engine_api import preview as engine_preview
    c,t,_=engine
    first=engine_preview(engine).json()
    committed=confirm(engine,first);assert committed.status_code==200,committed.text
    t['version']=committed.json()['version']
    collect(c,t,'food','food','want')
    result=plan(engine,preferences={'avoid':['food']}).json()
    assert result['status']=='feasible',result
    assert '你设为必去，已保留' in result['reply']
    assert all(g['required'] for g in candidate_goals(result))
    assert any(g['target_kind']=='food' for g in candidate_goals(result))
    assert not result['unscheduled']
