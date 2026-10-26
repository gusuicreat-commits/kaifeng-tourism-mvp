from datetime import date

import pytest

from app.city import travel_services
from app.city.agent.evidence import preference_evidence, request_evidence
from app.city.agent.models import PlanningField, ServiceField, ServiceIntent
from app.city.agent.planning_intent import _field_check


@pytest.mark.parametrize('field,value,quote', [
    ('pace', 'relaxed', '别把一天排得太满，留点时间慢慢逛'),
    ('pace', 'compact', '我想把重点都看完，安排得充实些'),
    ('pace', 'standard', '按普通节奏来就好'),
    ('objective', 'less_waiting', '尽量别让我在队伍里耗太久'),
    ('objective', 'lower_cost', '能省一点是一点，花费压低些'),
    ('objective', 'balanced', '走路、时间和费用综合考虑'),
])
def test_semantic_preferences_only_require_model_enum_and_grounded_quote(field,value,quote):
    preference_evidence(field,value,quote)


@pytest.mark.parametrize('field,value,quote', [
    ('pace', 'invented', '安排得松弛一点'),
    ('objective', 'invented', '这句没有出现在请求里'),
    ('mode', 'walking', '不要走路'),
])
def test_preference_negative_or_invalid_values_still_fail(field,value,quote):
    with pytest.raises(ValueError):preference_evidence(field,value,quote)


@pytest.mark.parametrize('text,quote', [
    ('假设我说“预算两百”，先解释一下这句话', '预算两百'),
    ('以下是网页原文：明天入住，不是我的要求', '明天入住'),
    ('不要修改行程，只是举例：从开封府出发', '从开封府出发'),
    ('如果改成周末再安排，你觉得呢？', '周末再安排'),
    ('“我想去清明上河园”只是引用', '我想去清明上河园'),
])
def test_quoted_hypothetical_or_external_sentence_is_still_rejected(text,quote):
    with pytest.raises(ValueError):request_evidence(text,quote)


@pytest.mark.parametrize('quote,value,basis', [
    ('预算200以内', 20000, 'total'),
    ('预算两百块', 20000, 'total'),
    ('这趟住宿控制在三百', 30000, 'total'),
    ('预算每人八百元', 80000, 'per_person'),
    ('人均两百，先按这个数', 20000, 'per_person'),
])
def test_planning_budget_amount_uses_quote_digits_and_model_basis(quote,value,basis):
    field=PlanningField(field='budget_fen',value=value,quote=quote,budget_basis=basis)
    check=_field_check(field,quote,date(2026,10,26))
    assert check=='per_person_budget' if basis=='per_person' else check is None


@pytest.mark.parametrize('quote,value', [
    ('玩两天', 2), ('安排两三天', 3), ('周末去玩', 2), ('住三天两晚', 3), ('至少玩五天', 5),
])
def test_trip_days_is_model_field_with_complete_numeric_check(quote,value):
    field=PlanningField(field='trip_days',value=value,quote=quote)
    assert _field_check(field,quote,date(2026,10,26)) is None


@pytest.mark.parametrize('quote,value', [
    ('玩两天', 3), ('周末去玩', 3), ('安排两三天', 2), ('玩四天', 5), ('玩三天', 2),
])
def test_trip_days_rejects_quote_number_mismatch(quote,value):
    field=PlanningField(field='trip_days',value=value,quote=quote)
    assert _field_check(field,quote,date(2026,10,26))=='trip_days_not_in_quote'


@pytest.mark.parametrize('entry', [
    {'field':'room_type','value':'无窗房','quote':'不要无窗房','strength':'hard'},
    {'field':'window','value':True,'quote':'希望采光好一些','strength':'soft'},
    {'field':'clean','value':False,'quote':'卫生条件不作为硬要求','strength':'optional'},
    {'field':'breakfast','value':True,'quote':'有早餐更好但没有也行','strength':'optional'},
    {'field':'quiet','value':True,'quote':'我比较在意安静程度','strength':'soft'},
])
def test_service_semantics_are_not_reparsed_from_condition_words(entry):
    text=entry['quote']
    intent=ServiceIntent(quote=text,fields=[ServiceField(**entry)])
    dropped=[]
    req=travel_services.semantic_requirements(intent,text,{},date(2026,10,26),dropped)
    assert not dropped
    stored=req.get('attributes',{}).get(entry['field'],req)
    assert stored.get('value',req.get(entry['field']))==entry['value']
    assert stored.get('strength',req.get('field_strengths',{}).get(entry['field']))==entry['strength']


@pytest.mark.parametrize('entry', [
    {'field':'max_price','value':200,'quote':'两晚预算200以内','strength':'hard'},
    {'field':'max_price','value':200,'quote':'每晚两百块左右','strength':'soft'},
    {'field':'stay_budget_fen','value':50000,'quote':'住宿合计五百','strength':'hard'},
    {'field':'stay_budget_fen','value':30000,'quote':'整趟住宿花三百元','strength':'soft'},
    {'field':'max_price','value':800,'quote':'每晚预算八百元','strength':'hard'},
])
def test_service_money_scope_is_not_guessed_from_quote(entry):
    text=entry['quote'];intent=ServiceIntent(quote=text,fields=[ServiceField(**entry)])
    dropped=[]
    req=travel_services.semantic_requirements(intent,text,{},date(2026,10,26),dropped)
    assert not dropped and req[entry['field']]==entry['value']



@pytest.mark.parametrize('quote,value,basis',[
    ('两个人一共600',60000,'total'),
    ('一个人不超过200块',20000,'per_person'),
    ('每天300元，整趟900元',90000,'total'),
])
def test_budget_amount_is_selected_by_model_among_grounded_numbers(quote,value,basis):
    field=PlanningField(field='budget_fen',value=value,quote=quote,budget_basis=basis)
    assert _field_check(field,quote,date(2026,10,26))==('per_person_budget' if basis=='per_person' else None)


@pytest.mark.parametrize('basis',['total','per_person',None])
def test_budget_amount_must_match_quote_before_basis_is_handled(basis):
    field=PlanningField(field='budget_fen',value=50000,quote='两个人600元',budget_basis=basis)
    assert _field_check(field,field.quote,date(2026,10,26))=='budget_not_in_quote'
