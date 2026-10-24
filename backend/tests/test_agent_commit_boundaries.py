"""Candidate-intention and formal-route capabilities are separate namespaces."""
from test_travel_services import env, query, rid


def test_service_candidate_cannot_confirm_as_engine_route(env):
    c,p=env;before=c.get(p).json();w=query(env)
    preview=c.post(p+'/services/preview',json={'request_id':rid(),'expected_version':w['version'],'candidate_id':w['result']['items'][0]['id']}).json()
    for endpoint,payload in [('/engine/confirm',{'candidate_id':preview['preview_id']}),
                              ('/assistant/confirm',{'candidate_id':preview['preview_id'],'target_kind':'route','confirmed':True})]:
        r=c.post(p+endpoint,json={'request_id':rid(),'expected_version':before['version'],**payload})
        assert r.status_code in (404,409),r.text
    assert c.get(p).json()==before
    assert c.get(p+'/engine/current').json()['itinerary'] is None


def test_false_service_confirmation_and_stale_intention_cannot_write(env):
    c,p=env;w=query(env);before=c.get(p).json()
    preview=c.post(p+'/services/preview',json={'request_id':rid(),'expected_version':w['version'],'candidate_id':w['result']['items'][0]['id']}).json()
    body={'request_id':rid(),'expected_version':w['version'],'preview_id':preview['preview_id'],'confirmed':False}
    assert c.post(p+'/services/confirm',json=body).status_code==409
    newer=query(env,'换一个150元以内',w['version'])
    body.update(request_id=rid(),confirmed=True,expected_version=newer['version'])
    assert c.post(p+'/services/confirm',json=body).status_code==409
    assert c.get(p+'/services').json()['selection'] is None
    assert c.get(p).json()==before
