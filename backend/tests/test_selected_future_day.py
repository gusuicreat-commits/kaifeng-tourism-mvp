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


@pytest.mark.parametrize('ref,quote,expected',[
    ('2026-11-02','第三天改成十点出发','第三天'),
    ('2026-11-01','第二天少走点路','第二天'),
    ('2026-10-31','明天早点出发','明天'),
    ('第三天','第三天改成十点出发','第三天'),
    ('2026-11-02','2026-11-02改成十点出发','2026-11-02'),
    ('2026-11-02','第二天和第三天都十点出发',None),
    ('2026-11-02','改成十点出发',None),
    ('2026-11-02','第一天九点出发','第一天'),
    ('2026-11-02','最后一天早点出发','最后一天'),
    ('2026-11-02','首日早点出发','首日'),
    ('2026-11-02','末日早点出发','末日'),
    ('2026-11-02','周六早点出发','周六'),
    ('2026-11-02','11月2日早点出发','11月2日'),
    ('2026-11-02','明天和第三天都十点出发',None),
    ('2026-11-02','第三天早些出门，第三天十点出发','第三天'),
])
def test_quoted_day_ref_recovers_only_one_literal_expression(ref,quote,expected):
    from app.city.agent.date_range import quoted_day_ref
    assert quoted_day_ref(ref,quote,quote)==expected


def test_quoted_day_ref_does_not_recover_an_expression_absent_from_request():
    from app.city.agent.date_range import quoted_day_ref
    assert quoted_day_ref('2026-11-02','第三天改成十点出发','改成十点出发') is None
    assert quoted_day_ref('第三天','十点出发','第三天改成十点出发')=='第三天'


@pytest.mark.parametrize('text,expected',[
    ('第三天改成十点出发','第三天'),
    ('第二天九点，第三天改成十点出发','第三天'),
    ('第二天第三天都改成十点出发',None),
    ('第三天改成十点出发，第二天也十点出发',None),
    ('第三天早些出门，改成十点出发',None),
    ('11月2日改成十点出发','11月2日'),
])
def test_quoted_day_ref_recovers_only_an_unambiguous_day_in_the_same_clause(text,expected):
    from app.city.agent.date_range import quoted_day_ref
    assert quoted_day_ref('2026-11-02','十点出发',text)==expected


@pytest.mark.parametrize('separator',list('，,。；;！!？?\n'))
def test_quoted_day_ref_clause_boundaries(separator):
    from app.city.agent.date_range import quoted_day_ref
    assert quoted_day_ref('2026-11-02','十点出发','第二天九点'+separator+'第三天改成十点出发')=='第三天'


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
    assert result['reply'].count('本次只调整')==1 and '本次仅调整' not in result['reply']
    if kind=='planning':assert result['reply'].count('2030-05-03')==1
    assert ('今天及其他日期' in result['reply']) is active
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


@pytest.mark.parametrize('status',['ACTIVE','PAUSED'])
@pytest.mark.parametrize('text,day_ref',[
    ('今天后面的安排帮我改一下，少走点路',None),
    ('今天剩余的行程少走路','今天'),
    ('第二天的行程少走路','第二天'),
])
def test_today_only_agent_edit_points_to_trip_page_without_changing_versions(multi_engine,monkeypatch,status,text,day_ref):
    from app import language
    from app.city.agent import provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    e,req,_=committed(multi_engine);moment=start_today(e,monkeypatch)
    if status=='PAUSED':
        with connect() as db:db.execute("UPDATE city_trip_states SET status='PAUSED' WHERE id=?",(e[1]['id'],))
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    agent.TASKS.clear()
    raw={'planning':{'operation':'update','quote':text,'fields':[
        {'field':'objective','value':'less_walking','quote':text,'day_ref':day_ref}]}}
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json();route=current(e).json()
    task=run({'client':e[0],'trip':e[1]},text)
    assert task['status']=='needs_clarification',json.dumps(task,ensure_ascii=False)
    assert task['result']['reply']=='多天行程里只调整今天剩余的安排，请在行程页使用“调整今天剩余”；如果要连同后面几天一起改，可以说“今天和明天都……”。'
    assert task['result']['planning']=={'status':'needs_clarification','reply':task['result']['reply']}
    assert not task['result'].get('confirmation')
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before
    assert current(e).json()==route
    agent.TASKS.clear()


@pytest.mark.parametrize('phrase,day_ref',[
    ('明天','明天'),('明天',None),('第三天','第三天'),('第三天',None),('5月3日',None),('最后一天',None),
])
def test_active_agent_future_reference_still_selects_future_day(multi_engine,monkeypatch,phrase,day_ref):
    from app import language
    from app.city.agent import provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    e,req,old=committed(multi_engine);moment=start_today(e,monkeypatch)
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    agent.TASKS.clear();text=phrase+'改成十点出发'
    raw={'planning':{'operation':'update','quote':text,'fields':[
        {'field':'start_time','value':'10:00','quote':'十点出发','day_ref':day_ref}]}}
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    task=run({'client':e[0],'trip':e[1]},text)
    assert task['status']=='needs_confirmation',json.dumps(task,ensure_ascii=False)
    p=task['result']['planning'];assert p['scope']=='selected_future_day'
    assert p['result']['itineraries'][0]['days'][:2]==old['itinerary']['days'][:2]
    assert p['result']['itineraries'][0]['days'][2]['requirements']['starts_at'].endswith('10:00:00+08:00')
    assert current(e).json()['route_version']==1
    agent.TASKS.clear()


@pytest.mark.parametrize('text',['第九天少走路','2月30日少走路','6月1日少走路'])
def test_active_invalid_or_outside_date_keeps_existing_future_scope_refusal(multi_engine,monkeypatch,text):
    from fastapi import HTTPException
    from app.memory.session import Session
    from app.city.agent import future_planning
    from app.city.agent.models import Understanding
    from app.city.assistant import context,tools
    e,req,_=committed(multi_engine);moment=start_today(e,monkeypatch)
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    with connect() as db:
        row=db.execute('SELECT * FROM conversation_sessions LIMIT 1').fetchone();session=Session(row['id'],row['guest_token_hash'],None)
    snapshot=context.load(session,e[1]['id'],'modify');before=saved_rows()
    with pytest.raises(HTTPException) as exc:tools.selected_future_day_scope(snapshot,text)
    intent=Understanding(planning={'operation':'update','quote':text,'fields':[
        {'field':'objective','value':'less_walking','quote':'少走路'}]})
    result=future_planning.preview(session,e[1]['id'],snapshot,intent,rid(),text)
    detail=exc.value.detail
    assert result['status']=='needs_clarification' and result['reply']==(detail['message'] if isinstance(detail,dict) else detail)
    assert saved_rows()==before


@pytest.mark.parametrize('text,quote,field,value,index,ref',[
    ('第三天改为十点出发','第三天改为十点出发','start_time','10:00',2,'2030-05-04'),
    ('第三天改为十点出发','十点出发','start_time','10:00',2,'2030-05-03'),
    ('第二天少走点路','第二天少走点路','objective','less_walking',1,'2030-05-02'),
    ('第二天和第三天都十点出发','第二天和第三天都十点出发','start_time','10:00',None,'2030-05-04'),
    ('第二天第三天都改为十点出发','十点出发','start_time','10:00',None,'2030-05-03'),
])
def test_future_agent_recovers_iso_day_ref_or_drops_ambiguous_item(multi_engine,monkeypatch,text,quote,field,value,index,ref):
    from app import language
    from app.city.agent import provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    e,req,old=committed(multi_engine)
    moment=datetime.fromisoformat('2030-04-30T08:00:00+08:00')
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    agent.TASKS.clear()
    raw={'planning':{'operation':'update','quote':text,'fields':[
        {'field':field,'value':value,'quote':quote,'day_ref':ref}]}}
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()
    task=run({'client':e[0],'trip':e[1]},text)
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before
    if index is None:
        assert task['status']=='unavailable',task
        assert task['interpretation']['dropped_fields']==[{'field':field,'check':'day_ref_not_in_quote'}]
        assert not task['result'].get('planning') and not task['result'].get('confirmation')
        assert current(e).json()['itinerary']==old['itinerary']
    else:
        assert task['status']=='needs_confirmation',task
        p=task['result']['planning'];assert p['scope']=='selected_future_day'
        assert not task['interpretation'].get('dropped_fields')
        assert task['interpretation']['recovered_fields']==[{'field':field,'source':'day_ref_from_quote','quote':text[:3]}]
        assert '没采用' not in task['result']['reply']
        for plan in p['result']['itineraries']:
            for i,day in enumerate(plan['days']):
                if i!=index:assert day==old['itinerary']['days'][i]
            requirements=plan['days'][index]['requirements']
            if field=='start_time':assert requirements['starts_at'].endswith('10:00:00+08:00')
            else:assert requirements['objective']==value
    agent.TASKS.clear()


@pytest.mark.parametrize('text,fields,expected',[
    ('改成十点出发',[{'field':'start_time','value':'10:00','quote':'十点出发'}],'用于哪一天'),
    ('6月1日改为十点出发',[{'field':'start_time','value':'10:00','quote':'十点出发','day_ref':'6月1日'}],'尚未到来的日期'),
])
def test_future_scope_refusal_is_a_visitor_question_not_a_stop(multi_engine,monkeypatch,text,fields,expected):
    from app import language
    from app.city.agent import provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    e,req,old=committed(multi_engine)
    moment=datetime.fromisoformat('2030-04-30T08:00:00+08:00')
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    agent.TASKS.clear()
    raw={'planning':{'operation':'update','quote':text,'fields':fields}}
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    city={'client':e[0],'trip':e[1]};before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json()
    task=run(city,text)
    assert task['status']=='needs_clarification',json.dumps(task,ensure_ascii=False)
    assert expected in task['result']['reply'] and '已停止' not in task['result']['reply']
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before
    agent.TASKS.clear()


def test_unstarted_confirmed_trip_recovers_unsupported_daily_scope_and_previews_whole_trip(multi_engine,monkeypatch):
    from app import language
    from app.city.agent import future_planning,provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    e,req,old=committed(multi_engine);moment=datetime.fromisoformat('2030-04-30T08:00:00+08:00')
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    agent.TASKS.clear();text='少走点路'
    raw={'planning':{'operation':'update','quote':text,'fields':[
        {'field':'objective','value':'less_walking','quote':text,'scope':'every_day'}]}}
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    monkeypatch.setattr(future_planning,'preview',lambda *a,**k:pytest.fail('undated DRAFT edits must use whole-trip planning'))
    before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json();route=current(e).json()
    task=run({'client':e[0],'trip':e[1]},text)
    assert task['status']=='needs_confirmation',task
    assert all(word not in task['result']['reply'] for word in ('请明确要调整哪一天','没采用','旧单日入口'))
    result=task['result']['planning_workflow']['result']
    assert result['status']=='preview'
    for plan in result['result']['itineraries']:
        assert len(plan['days'])==len(req['days'])
        assert all(day['requirements']['objective']=='less_walking' for day in plan['days'])
    assert not task['interpretation'].get('dropped_fields')
    assert task['interpretation']['recovered_fields']==[{'field':'objective','source':'unsupported_every_day','quote':text}]
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before and current(e).json()==route
    agent.TASKS.clear()


def test_unstarted_confirmed_trip_with_undated_choice_does_not_preview_future_day(multi_engine,monkeypatch):
    from app import language
    from app.city.agent import future_planning,orchestration,provider,service as agent
    from app.city.assistant import context,tools
    from test_city_agent import run
    e,req,old=committed(multi_engine);moment=datetime.fromisoformat('2030-04-30T08:00:00+08:00')
    for module in (context,tools):monkeypatch.setattr(module,'now',lambda:moment)
    agent.TASKS.clear();text='再加一个包公祠'
    raw={'planning':{'operation':'update','quote':text,'choices':[
        {'name':'包公祠','quote':text,'priority':'want'}]}}
    monkeypatch.setattr(language,'available',lambda:True)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(copy.deepcopy(raw),{'source':'mock_model'}))
    monkeypatch.setattr(future_planning,'preview',lambda *a,**k:pytest.fail('undated DRAFT choices must use whole-trip planning'))
    calls=[]
    def whole_trip(session,t,intent,proposed=None):
        calls.append(intent.planning)
        return {'status':'needs_clarification','reply':'请核对整套行程的地点。'}
    monkeypatch.setattr(orchestration,'_execute',whole_trip)
    before=e[0].get('/api/city/v1/trips/'+e[1]['id']).json();route=current(e).json()
    task=run({'client':e[0],'trip':e[1]},text)
    assert task['status']=='needs_clarification',task
    assert not task['interpretation'].get('dropped_fields')
    assert len(calls)==1 and calls[0].choices[0].name=='包公祠'
    assert '请明确要调整哪一天' not in task['result']['reply']
    assert e[0].get('/api/city/v1/trips/'+e[1]['id']).json()==before and current(e).json()==route
    agent.TASKS.clear()
