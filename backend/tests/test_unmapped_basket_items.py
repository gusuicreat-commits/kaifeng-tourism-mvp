"""Unmapped basket intentions degrade to per-plan explanations when other goals remain."""
from test_city_engine_api import engine
from test_multi_day_api import multi_engine
from test_exploration import collect,plan
from test_multi_day_basket_goals import basket_trip,preview


def test_single_day_unmapped_item_does_not_block_two_mapped_goals(engine):
    c,t,_=engine
    for kind,identity in (('place','museum'),('place','garden'),('food','chaoliangfen')):
        collect(c,t,kind,identity)
    result=plan(engine).json()
    assert result['status']=='feasible',result
    assert result['planning']['result']['itineraries']
    planned={a['place_id'] for alternative in result['planning']['result']['itineraries']
             for a in alternative['activities']}
    assert planned >= {'museum','garden'}
    for rows in result['unscheduled'].values():
        assert any(row=={'goal_id':'unmapped:food:chaoliangfen','key':'food:chaoliangfen',
                         'name':'炒凉粉','reason_code':'data_pending','reason':'资料待添加，暂时排不进行程'}
                   for row in rows)


def test_multi_day_unmapped_item_does_not_block_two_mapped_goals(basket_trip):
    e,values=basket_trip;c,t,_=e
    for kind,identity in (('place','museum'),('place','garden'),('food','chaoliangfen')):
        collect(c,t,kind,identity)
    result=preview(e,values)
    assert result['status']=='preview' and result['result']['status']=='feasible',result
    assert result['result']['itineraries']
    planned={a['place_id'] for alternative in result['result']['itineraries']
             for day in alternative['days'] for a in day['itinerary']['activities']}
    assert planned >= {'museum','garden'}
    for rows in result['unscheduled'].values():
        assert any(row['key']=='food:chaoliangfen' and row['name']=='炒凉粉' and
                   row['reason_code']=='data_pending' and row['reason']=='资料待添加，暂时排不进行程'
                   for row in rows)


def test_all_unmapped_items_keep_existing_unknown_message(engine):
    c,t,_=engine;collect(c,t,'food','chaoliangfen')
    result=plan(engine).json()
    assert result['status']=='unknown'
    assert result['reply']=='所选内容的地点、商家或场次资料待添加；暂不能核验完整路线。'
    assert result['issues'][0]['affected_ids']==['food:chaoliangfen']
    assert not result.get('planning')


def test_multi_day_all_unmapped_items_keep_existing_unknown_message(basket_trip):
    e,values=basket_trip;c,t,_=e;collect(c,t,'food','chaoliangfen')
    result=preview(e,values)
    assert result['status']=='needs_data'
    assert result['reply']=='所选内容的地点、商家或场次资料待添加；暂不能核验完整路线。'
    assert result['issues'][0]['affected_ids']==['food:chaoliangfen']
    assert not result.get('candidate_id') and not result.get('result')


def test_unmapped_plus_preference_excluded_item_with_no_goal_stays_unknown(engine):
    c,t,_=engine
    collect(c,t,'food','chaoliangfen');collect(c,t,'food','food')
    result=plan(engine,preferences={'avoid':['food']}).json()
    assert result['status']=='unknown'
    assert result['reply']=='所选内容的地点、商家或场次资料待添加；暂不能核验完整路线。'
    assert result['issues'][0]['affected_ids']==['food:chaoliangfen']
    assert not result.get('planning')
