"""R3: authorized persisted requirements, not chat replay, drive model context."""
import copy
import json
from datetime import timedelta

from app.city.agent import conversation, planning_intent
from app.city.agent.models import Action, PlanningIntent, Understanding
from app.city.assistant import context
from app.city import state
from app.db import connect
from test_city_assistant import city, get, path, rid, modify
from test_city_agent import snapshot
from app.city.agent.provider import MODEL_INPUT_CHARACTERS as LIMIT


def payload(snap):
    return conversation.model_payload('还缺什么？',snap,'只读测试')['context']


def test_readiness_and_planning_proposal_use_the_same_effective_values(city):
    modify(city,{'operation':'save_draft','draft':{'business_date':'2030-05-02',
        'starts_at':'2030-05-02T09:00:00+08:00','ends_at':'2030-05-02T18:00:00+08:00'}})
    snap,session=snapshot(city)
    snap.workspace['constraints']={'pace':'relaxed'}
    ctx=payload(snap)
    assert ctx['requirements']['business_date']==snap.envelope.requirements['business_date']
    assert not set(('business_date','starts_at','ends_at')) & set(ctx['planning_readiness']['missing_user_fields'])
    intent=Understanding(actions=(Action(kind='modify',quote='开始安排'),),
        planning=PlanningIntent(operation='continue',quote='开始安排'))
    result=planning_intent.proposal(session,city['trip']['id'],snap,intent,'开始安排')
    values=result['planning_action']['constraints']
    assert values['business_date']==ctx['requirements']['business_date']
    assert values['starts_at']==ctx['requirements']['starts_at']
    assert values['pace']=='relaxed'


def test_explicit_workspace_null_is_preserved_instead_of_restoring_old_draft(city):
    snap,_=snapshot(city)
    snap.workspace.update(version=2,constraints={'business_date':None,'starts_at':None,'ends_at':None})
    ctx=payload(snap)
    assert ctx['requirements']['business_date'] is None
    assert 'business_date' in ctx['planning_readiness']['missing_user_fields']
    assert ctx['state_versions']['planning']==2


def test_service_missing_fields_are_recomputed_and_selection_stays_an_intention(city):
    snap,_=snapshot(city)
    today=snap.envelope.identity.generated_at.date()
    snap.services={'version':4,'requirements':{'category':'hotels','nights':2,
        'check_in':(today+timedelta(days=1)).isoformat(),'check_out':(today+timedelta(days=3)).isoformat(),
        'stay_budget_fen':50000,'room_type':'标间','attributes':{'breakfast':{'value':True,'strength':'optional','quote':'secret-note'}}},
        'result':{'status':'needs_clarification','questions':['请提供入住日期。'],'raw':'private-token'},
        'selection':{'hotels':{'id':'synthetic:h1','name':'[合成]住宿意向','url':'https://invalid.test/private-token','raw':'private-token'}}}
    original=copy.deepcopy(snap.services)
    ctx=payload(snap);service=ctx['travel_service_workspace']
    assert service['missing_fields']==[] and service['missing_user_fields']==[]
    assert service['selection']=={'hotels':{'id':'synthetic:h1','name':'[合成]住宿意向'}}
    assert ctx['state_versions']['services']==4
    assert service['requirements']['stay_budget_fen']==50000
    assert 'private-token' not in json.dumps(ctx) and 'secret-note' not in json.dumps(ctx)
    assert snap.services==original


def test_service_missing_stay_length_does_not_repeat_known_checkin(city):
    snap,_=snapshot(city)
    today=snap.envelope.identity.generated_at.date()
    snap.services.update(version=1,requirements={'category':'hotels','check_in':(today+timedelta(days=1)).isoformat()},result=None)
    service=payload(snap)['travel_service_workspace']
    assert service['missing_user_fields']==['check_out_or_nights']
    assert service['missing_fields']==['请提供退房日期或住几晚。']


def test_legacy_planning_receipt_has_the_same_read_view_in_api_and_agent(city):
    snap,session=snapshot(city)
    saved={'business_date':'2030-05-02','starts_at':'2030-05-02T09:00:00+08:00','ends_at':'2030-05-02T18:00:00+08:00',
        'origin_text':'[合成]酒店出发','destination_text':'[合成]车站返程'}
    with connect() as db:
        state.remember(db,session,rid(),city['trip']['id'],'exploration:'+city['trip']['id'],{}, {'constraints':saved})
    before=get(city)
    api=city['client'].get(path(city,'/exploration/constraints')).json()
    fresh=context.load(session,city['trip']['id'],'modify')
    ctx=payload(fresh)
    assert fresh.workspace['constraints']==api['constraints']==saved
    assert ctx['requirements']['business_date']=='2030-05-02'
    assert ctx['planning_readiness']['missing_user_fields']==[]
    assert ctx['requirements_source']=='legacy_planning_receipt'
    assert get(city)==before


def test_context_allowlist_and_budget_scopes_do_not_promote_service_data(city):
    snap,_=snapshot(city)
    snap.workspace.update(version=3,constraints={'budget_fen':12300,'origin_text':'[合成]游玩起点','private':'private-token'})
    snap.services.update(version=5,requirements={'category':'hotels','check_in':'2030-05-03','nights':2,'stay_budget_fen':50000,'private':'private-token'})
    ctx=payload(snap)
    assert ctx['requirements']['budget_fen']==12300
    assert ctx['travel_service_workspace']['requirements']['stay_budget_fen']==50000
    assert ctx['requirements']['business_date']!='2030-05-03'
    assert 'private-token' not in json.dumps(ctx)
    assert ctx['state_versions']['trip_id']==city['trip']['id']


def test_history_off_keeps_saved_service_requirements_but_no_dialogue_or_other_trip(city):
    from app.city import travel_services
    snap,session=snapshot(city)
    with connect() as db:
        travel_services.write(db,city['trip']['id'],{'version':1,'requirements':{'category':'hotels','nights':2,'room_type':'标间'},'result':None,'selection':None,'previous':None})
    assert city['client'].put('/api/v1/memory/history',json={'enabled':False}).status_code==200
    snap=context.load(session,city['trip']['id'],'modify')
    ctx=payload(snap)
    assert ctx['recent_messages']==[]
    assert ctx['travel_service_workspace']['requirements']['nights']==2
    assert ctx['travel_service_workspace']['missing_user_fields']==['check_in']
    other=city['client'].post('/api/city/v1/trips',json={'request_id':rid(),'draft':{}}).json()
    other_ctx=payload(context.load(session,other['id'],'modify'))
    assert 'nights' not in other_ctx['travel_service_workspace']['requirements']
    assert other_ctx['state_versions']['services']==0
    assert other_ctx['state_versions']['trip_id']==other['id']


def test_long_history_cannot_evict_authoritative_conditions(city):
    snap,_=snapshot(city)
    snap.services.update(version=2,requirements={'category':'hotels','nights':2,'room_type':'标间','stay_budget_fen':50000})
    snap.envelope=snap.envelope.model_copy(update={'recent_messages':[{'role':'assistant','text':'过时聊天'*300} for _ in range(8)]})
    data=conversation.model_payload('明天入住',snap,'测试提示'*1200)
    assert len('测试提示'*1200)+len(json.dumps(data,ensure_ascii=False))<=LIMIT
    assert data['context']['travel_service_workspace']['requirements']['nights']==2
    assert data['context']['travel_service_workspace']['requirements']['stay_budget_fen']==50000
    assert len(data['context']['recent_messages'])<8
