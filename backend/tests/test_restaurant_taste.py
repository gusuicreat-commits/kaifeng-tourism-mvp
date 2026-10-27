"""Restaurant imports and semantic dining filters use synthetic temporary catalogs only."""
import csv
import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from app import language
from app.db import connect, database_path
from app.city import facts
from app.city.agent import dining, provider, toolbelt, understand
from app.city.agent.models import Action, Understanding
from test_city_assistant import city, get, walking
from test_city_agent import run, snapshot

SPEC=importlib.util.spec_from_file_location('import_restaurant_taste',Path(__file__).parents[2]/'scripts/import_restaurant_taste.py')
IMPORT=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(IMPORT)


def write_csv(tmp_path,rows):
    target=tmp_path/'taste.csv'
    with target.open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=IMPORT.HEADERS);writer.writeheader()
        for row in rows:writer.writerow({IMPORT.HEADERS[k]:v for k,v in row.items()})
    return target


def row(**changes):
    result={0:'[合成测试]restaurant',2:'合成菜系',3:'清淡、甜口',4:'不辣',5:'合成招牌、[合成测试]菜品',6:'40-50',7:'早餐、夜宵',
            8:'合成资料，仅供测试',9:'test-screenshot.png',10:'测试采集人',11:'2020-01-02'}
    result.update({int(k):v for k,v in changes.items()});return result


def catalog():
    with connect() as db:return facts.records(db),[tuple(r) for r in db.execute('SELECT * FROM city_fact_revisions ORDER BY kind,id,version')]


def cli(tmp_path,rows,*args):
    return IMPORT.main([str(write_csv(tmp_path,rows)),'--database',str(database_path()),*args])


def test_import_reference_provenance_alias_and_repeat(city,tmp_path):
    with connect() as db:
        before=facts.records(db)
        place=before['place','restaurant'].model_copy(update={'version':2,'aliases':('合成别名',)})
        vendor=before['vendor','vendor'].model_copy(update={'version':2,'price_band':'60-70元/人'})
        facts.import_bundle(facts.FactBundle(records=(place,vendor)),db,apply=True)
    assert cli(tmp_path,[row(**{'0':'合成别名'})])==0
    items,revisions=catalog();v=items['vendor','vendor']
    assert (v.version,v.cuisine,v.taste_tags,v.spiciness,v.signature_dishes,v.meal_periods,v.price_band)==(
        3,'合成菜系',('清淡','甜口'),'none',('合成招牌','[合成测试]菜品'),('早餐','夜宵'),'40-50元/人')
    assert v.validation_status=='unverified' and v.planning_status=='reference' and v.verified_at is None and v.reviewer is None
    assert len(v.source_ids)==2 and items['source',v.source_ids[-1]].publisher=='测试采集人'
    assert any(json.loads(r[3]).get('price_band')=='60-70元/人' for r in revisions)
    assert len([r for r in items.values() if isinstance(r,facts.Supply)])==1
    assert cli(tmp_path,[row(**{'0':'合成别名'})])==0
    assert catalog()==(items,revisions)


@pytest.mark.parametrize('bad',[
    {'0':'未登记商家'},{'4':'很辣'},{'6':'五十'},{'6':'70-40'},{'7':'下午茶'},
    {'9':''},{'10':''},{'11':'2099-01-01'},{'11':'昨天'},{'3':'清淡、、甜口'}])
def test_import_rejects_entire_batch(city,tmp_path,bad,capsys):
    before=catalog()
    assert cli(tmp_path,[row(**bad)])==1
    assert '第2行' in capsys.readouterr().err and catalog()==before


def test_import_batch_does_not_partially_write(city,tmp_path,capsys):
    before=catalog()
    assert cli(tmp_path,[row(),row(**{'0':'无此店'})])==1
    assert '精确匹配' in capsys.readouterr().err and catalog()==before


def test_import_blank_cells_keep_previous_values_and_dry_run(city,tmp_path):
    before=catalog();assert cli(tmp_path,[row()],'--dry-run')==0;assert catalog()==before
    assert cli(tmp_path,[row()])==0
    imported=catalog()
    assert cli(tmp_path,[{0:'[合成测试]restaurant'}])==0 and catalog()==imported
    assert cli(tmp_path,[row(**{'2':'','3':'','4':'','5':'','6':'','7':'午餐'})])==0
    v=catalog()[0]['vendor','vendor'];old=imported[0]['vendor','vendor']
    assert (v.cuisine,v.taste_tags,v.spiciness,v.signature_dishes,v.price_band)==(
        old.cuisine,old.taste_tags,old.spiciness,old.signature_dishes,old.price_band)
    assert v.meal_periods==('午餐',)


@pytest.mark.parametrize('value,expected',[('50.50元／人','50.5元/人'),('40.25～50.5','40.25-50.5元/人'),('0','0元/人')])
def test_import_prices_are_normalized_and_queryable(city,tmp_path,value,expected):
    assert cli(tmp_path,[row(**{'6':value})])==0
    vendor=catalog()[0]['vendor','vendor'];assert vendor.price_band==expected
    assert not dining.failures(vendor,[{'max_fen':5050}])


def test_imported_reference_attributes_work_in_queries(city,tmp_path):
    assert cli(tmp_path,[row()])==0
    snap,_=snapshot(city)
    result=toolbelt.find_food(snap,intent('每人五十不辣早餐',{'max_fen':5000,'spicy':'none','period':'早餐'}))
    assert len(result['items'])==1 and result['items'][0]['validation_status']=='unverified'
    assert result['items'][0]['planning_status']=='reference' and '尚未现场核实' in result['reply']


@pytest.fixture
def restaurants(city):
    with connect() as db:
        items=facts.records(db);vendor=items['vendor','vendor'];place=items['place','restaurant']
        good=vendor.model_copy(update={'version':2,'cuisine':'合成菜系','taste_tags':('清淡','甜口','偏咸'),
            'spiciness':'none','signature_dishes':('[合成测试]菜品','未建档招牌'),
            'price_band':'40-50元/人','meal_periods':('早餐','夜宵')})
        other=vendor.model_copy(update={'fact_id':'other-vendor','place_id':'other-place','spiciness':'hot',
            'taste_tags':('浓郁',),'price_band':'60-70元/人','meal_periods':('午餐',)})
        unknown=vendor.model_copy(update={'fact_id':'unknown-vendor','place_id':'unknown-place'})
        facts.import_bundle(facts.FactBundle(records=(good,other,unknown,
            place.model_copy(update={'fact_id':'other-place','name':'合成辣店'}),
            place.model_copy(update={'fact_id':'unknown-place','name':'合成资料不全店'}))),db,apply=True)
    return city


CASES=[
    ('有没有口味轻一些的饭店',{'taste':'清淡'}),('吃起来清爽一点就好',{'taste':'清淡'}),('找家做菜不那么重口的',{'taste':'清淡'}),
    ('一点辣椒都碰不了，推荐吃饭的店',{'spicy':'none'}),('吃饭别让我辣到',{'spicy':'none'}),('想找完全无辣的餐馆',{'spicy':'none'}),
    ('每位控制在五十块，哪家合适',{'max_fen':5000}),('吃饭平均一个人最多50元',{'max_fen':5000}),('推荐人均不超50的店',{'max_fen':5000}),
    ('刚起床想找店吃早饭',{'period':'早餐'}),('天亮了先吃点再出门，有什么店',{'period':'早餐'}),('半夜饿了哪儿有吃的',{'period':'夜宵'}),
    ('找点甜丝丝的吃食',{'taste':'甜口'}),('我就喜欢咸一点的馆子',{'taste':'偏咸'}),
]


@pytest.mark.parametrize('text,filters',CASES)
def test_semantic_conditions_across_phrasings(restaurants,monkeypatch,text,filters):
    before=get(restaurants)
    monkeypatch.setattr(language,'available',lambda:True)
    def generate(system,payload,**kwargs):
        assert payload['text']==text
        return {'actions':[{'kind':'search_dining','quote':text,'dining':filters}]},{'source':'mock_model'}
    monkeypatch.setattr(provider,'generate',generate)
    task=run(restaurants,text)
    assert task['status']=='answered' and task['mode']=='find',task
    result=task['result'];assert [i['vendor_id'] for i in result['items']]==['vendor']
    assert '按' in result['reply'] and '筛选' in result['reply'] and '资料不全' in result['reply']
    assert '招牌菜为商家资料，以店内为准' in result['reply']
    assert result['items'][0]['signature_dishes']==[{'name':'[合成测试]菜品','food_id':'food'},{'name':'未建档招牌','food_id':None}]
    assert get(restaurants)==before


def intent(text,filters,**extra):
    return Understanding(actions=(Action(kind='search_dining',quote=text,dining=filters,**extra),))


def test_combined_conditions_unknown_and_no_results(restaurants):
    snap,_=snapshot(restaurants)
    filters={'taste':'清淡、甜口','spicy':'none','max_fen':5000,'period':'夜宵'}
    result=toolbelt.find_food(snap,intent('合成请求',filters))
    assert [i['vendor_id'] for i in result['items']]==['vendor']
    assert '人均上限' in result['reply'] and '不辣' in result['reply'] and '夜宵' in result['reply']
    result=toolbelt.find_food(snap,intent('合成请求',filters|{'max_fen':4900}))
    assert not result['items'] and result['status']=='needs_data'
    assert '没有商家满足全部条件' in result['reply'] and '人均上限不符合' in result['reply'] and '资料不全，未确认' in result['reply']
    for key,value,label in [('taste','未知口味','口味'),('spicy','mild','辣度'),('period','晚餐','适合时段')]:
        result=toolbelt.find_food(snap,intent('合成请求',{key:value}))
        assert not result['items'] and label+'不符合' in result['reply']


@pytest.mark.parametrize('text,value',[('每人五十元',5000),('每位50.5元以内',5050),('吃饭不超一百二十元',12000)])
def test_price_grounded_in_original_quote(text,value):
    assert understand.grounded(intent(text,{'max_fen':value}),text)
    with pytest.raises(ValueError,match='invented dining price'):understand.grounded(intent(text,{'max_fen':value+100}),text)
    with pytest.raises(ValueError,match='ungrounded'):understand.grounded(intent('别处说的五十',{'max_fen':5000}),text)


@pytest.mark.parametrize('filters',[{'unknown':'清淡'},{'spicy':'medium'},{'period':'下午茶'},{'max_fen':True},{'max_fen':-1},{'taste':[]},{'taste':''}])
def test_off_format_conditions_are_dropped_not_fatal(filters):
    assert intent('合成请求',filters).actions[0].dining=={}


@pytest.mark.parametrize('filters,kept',[({'spicy':'不辣','period':'夜宵'},{'spicy':'none','period':'夜宵'}),
                                         ({'spicy':'medium','taste':'清淡'},{'taste':'清淡'})])
def test_valid_conditions_survive_a_sibling_off_format_one(filters,kept):
    assert intent('合成请求',filters).actions[0].dining==kept


def test_signature_query_named_shop_no_supply_created(restaurants,monkeypatch):
    text='[合成测试]restaurant有什么招牌菜'
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_dining','quote':text,'target_name':'[合成测试]restaurant'}]},{}))
    before=catalog();task=run(restaurants,text)
    assert task['status']=='answered' and '未建档招牌' in task['result']['reply']
    assert catalog()==before
    snap,_=snapshot(restaurants)
    # Beside an applicable condition an unknown name is set aside and named, never claimed.
    result=toolbelt.find_food(snap,intent('未知菜',{ 'spicy':'none'},target_name='未知菜'))
    assert '“未知菜”没有对应的商家资料，未按它筛选' in result['reply']
    assert all('未知菜' not in str(i['signature_dishes']) for i in result['items'])
    assert not toolbelt.find_food(snap,intent('未知菜',{},target_name='未知菜'))['items']


@pytest.mark.parametrize('text',['有什么招牌菜','给我看看店家拿手的菜色','哪家餐厅有特色推荐菜'])
def test_general_signature_query(restaurants,monkeypatch,text):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_dining','quote':text,'dining':{'dish':True}}]},{}))
    task=run(restaurants,text)
    assert task['status']=='answered' and '未建档招牌' in task['result']['reply']
    assert [i['vendor_id'] for i in task['result']['items']]==['vendor']


def test_price_sort_and_range_upper_bound(restaurants):
    with connect() as db:
        vendor=facts.records(db)['vendor','other-vendor']
        facts.import_bundle(facts.FactBundle(records=(vendor.model_copy(update={'version':2,'price_band':'30元/人'}),)),db,apply=True)
    snap,_=snapshot(restaurants)
    result=toolbelt.find_food(snap,intent('每人五十',{'max_fen':5000}))
    assert [i['vendor_id'] for i in result['items']]==['other-vendor','vendor']


def test_in_trip_search_uses_same_filters_and_explanations(restaurants,monkeypatch):
    walking(restaurants);snap,_=snapshot(restaurants)
    result=toolbelt.search_dining(snap,intent('附近不辣的店',{'spicy':'none'},near=True))
    assert [i['place_id'] for i in result['items']]==['restaurant']
    text='附近找一家人均四十以内的店'
    monkeypatch.setattr(language,'available',lambda:True)
    def generate(system,payload,**kwargs):
        if '下一步选择器' in system:return {'tool':'search_dining' if not payload.get('observations',{}).get('search_dining') else 'finish'},{}
        return {'actions':[{'kind':'search_dining','quote':text,'near':True,'dining':{'max_fen':4000}}]},{}
    monkeypatch.setattr(provider,'generate',generate)
    task=run(restaurants,text)
    assert task['status']=='needs_data' and '人均上限不符合' in task['result']['reply'] and '40元/人以内' in task['result']['reply']


def test_signature_food_link_never_substitutes_supply(restaurants):
    with connect() as db:
        food=facts.records(db)['food','food'].model_copy(update={'fact_id':'special-food','name':'未建档招牌','aliases':('合成别称',)})
        facts.import_bundle(facts.FactBundle(records=(food,)),db,apply=True)
    walking(restaurants);snap,_=snapshot(restaurants)
    lookup=toolbelt.find_food(snap,intent('问招牌',{'dish':True}))
    assert lookup['items'][0]['signature_dishes'][-1]=={'name':'未建档招牌','food_id':'special-food'}
    named=intent('想吃合成别称，找个不辣的店',{'spicy':'none'},target_name='合成别称')
    assert not toolbelt.find_food(snap,named)['items']
    assert not toolbelt.search_dining(snap,named)['items']


@pytest.mark.parametrize('target,expected',[('合成菜系',['vendor']),('甜口',['vendor']),('浓郁的',['other-vendor'])])
def test_a_cuisine_or_taste_in_the_name_slot_becomes_that_filter(restaurants,monkeypatch,target,expected):
    # Live model output (2026-10-27): 想吃点辣的烧烤 / 推荐个酸甜口的店 carried the cuisine or taste as target_name.
    text=f'想吃{target}，推荐一家';monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_dining','quote':text,'target_name':target}]},{'source':'mock_model'}))
    task=run(restaurants,text)
    assert task['status']=='answered' and [i['vendor_id'] for i in task['result']['items']]==expected,task


def test_a_name_no_record_shares_stays_unknown(restaurants,monkeypatch):
    text='想吃不存在的菜系';monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_dining','quote':text,'target_name':'火星料理'}]},{'source':'mock_model'}))
    task=run(restaurants,text)
    # Not a filter: the general food answer, which says a dish without records has none.
    assert '按' not in task['result']['reply'] and '没有列出的菜暂时没有资料' in task['result']['reply'],task


def test_conditions_without_any_recorded_data_are_set_aside(city):
    # Before the team's taste data is imported no vendor records meal periods.
    snap,_=snapshot(city)
    result=toolbelt.find_food(snap,intent('晚上吃什么',{'period':'晚餐'}),general=True)
    assert '暂未按适合时段筛选' in result.get('reply','')
    assert not dining.restaurant_query(snap,intent('晚上吃什么',{'period':'晚餐'}))


def test_dish_flag_beside_named_dish_is_not_a_filter(restaurants):
    assert dining.conditions(intent('想吃灌汤包',{'dish':True},target_name='灌汤包'))==[]
    assert dining.conditions(intent('有什么招牌菜',{'dish':True}))==[{'dish':True}]


@pytest.mark.parametrize('text,filters,asks',[
    ('不吃辣，有什么早点',{'spicy':'none','period':'早餐'},False),
    ('不吃香菜，有什么早点',{'period':'早餐'},True)])
def test_avoided_food_question_only_when_no_filter_says_it(restaurants,monkeypatch,text,filters,asks):
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:({'actions':[{'kind':'search_dining','quote':text,'dining':filters}]},{}))
    task=run(restaurants,text)
    assert (understand.ASK_EXCLUDED_FOOD in str(task['result'])) is asks


@pytest.mark.parametrize('empty',[None,{},[],''])
def test_empty_dining_value_is_no_condition(empty):
    action=Action.model_validate({'kind':'search_dining','quote':'开封小吃有哪些','target_name':'开封小吃','dining':empty})
    assert action.dining=={}


def test_label_target_needs_close_length(restaurants):
    snap,_=snapshot(restaurants)
    assert dining.label_targets(snap.planning,['合成菜系口','甜口'])==['合成菜系口','甜口']
    assert dining.label_targets(snap.planning,['开封当地合成菜系'])==[]
