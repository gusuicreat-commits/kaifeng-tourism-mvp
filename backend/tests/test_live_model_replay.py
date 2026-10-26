"""Replay recorded real model replies through the full agent pipeline.

Mocked outputs written by hand only prove the validators accept what their author
expected. These replies were produced by the configured model (see
scripts/record_model_outputs.py), so the validators are tested against how the
model actually quotes, labels and normalizes. The clock is frozen at recording time
so relative dates resolve exactly as they did then.
"""
import copy
from datetime import datetime

import pytest

from app import language
from app.city import state
from app.city.agent import provider
from app.city.assistant import context, service, tools
from app.city.engine import service as engine
from app.memory import session as memory_session
from app.memory import storage as memory_storage
import model_replay
from test_city_agent import empty_tasks

DATA = model_replay.load()
CASES = [c for c in DATA['cases'] if c.get('replies')]


@pytest.fixture
def kaifeng(client, monkeypatch):
    model_replay.import_reference()
    monkeypatch.setattr(language, 'available', lambda: True)
    return client


def freeze(monkeypatch, case):
    moment = datetime.fromisoformat(case.get('recorded_at', DATA['recorded_at']))
    for module in (memory_storage, memory_session, engine, state, context, service, tools):
        monkeypatch.setattr(module, 'now', lambda: moment)


@pytest.mark.skipif(not CASES, reason='no recorded model replies')
@pytest.mark.parametrize('case', CASES, ids=[c['text'][:16] for c in CASES])
def test_recorded_model_reply_passes_the_pipeline(kaifeng, monkeypatch, case):
    freeze(monkeypatch, case)
    replies = iter(copy.deepcopy(case['replies']))
    monkeypatch.setattr(provider, 'generate', lambda system, payload, **kw: (next(replies), {'source': 'recorded_model'}))
    trip = model_replay.new_trip(kaifeng)
    task = model_replay.run(kaifeng, trip, case['text'])
    problems = model_replay.check(case, task, model_replay.saved(kaifeng, trip))
    assert not problems, f"{case['text']}: {problems}"


def test_pitch_reply_lists_recorded_dish_suppliers_and_single_day_scope(kaifeng,monkeypatch):
    from datetime import timedelta
    from app.db import connect
    from app.city import facts
    case=next(c for c in CASES if '预算每人800元' in c['text'])
    freeze(monkeypatch,case)
    replies=iter(copy.deepcopy(case['replies']))
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(next(replies),{'source':'recorded_model'}))
    trip=model_replay.new_trip(kaifeng);before=kaifeng.get('/api/city/v1/trips/'+trip['id']).json()
    task=model_replay.run(kaifeng,trip,case['text']);reply=task['result']['reply']
    proposal=task['result']['basket_proposal']
    assert [(i['name'],i['kind'],i['priority']) for i in proposal['items']]==[
        ('万岁山武侠城','place','want'),('清明上河园','place','want')]
    assert kaifeng.get('/api/city/v1/trips/'+trip['id']+'/basket').json()['items']==[]
    recorded=datetime.fromisoformat(case['recorded_at']).date()
    saturday=(recorded+timedelta(days=5-recorded.weekday())).isoformat()
    saved=model_replay.saved(kaifeng,trip)
    assert saved['business_date']==saturday and saved.get('budget_fen') is None
    assert f'只能先排第一天（{saturday}）' in reply and '按人预算暂时不能用于规划，先不按预算筛选' in reply
    assert [line.split('：',1)[0] for line in reply.splitlines()]==['住宿','美食','行程']
    assert '。；' not in reply and '你想问哪一场演出' not in reply
    dining=next(item for item in task['result']['compound']['items'] if item['kind']=='direct_query')['answers'][0]
    assert dining['items'] and len({i['food_id'] for i in dining['items']})<=5
    with connect() as db:catalog=facts.records(db)
    food_reply=next(line for line in reply.splitlines() if line.startswith('美食：'))
    for item in dining['items']:
        supply=catalog['supply',item['supply_id']];vendor=catalog['vendor',item['vendor_id']]
        assert supply.food_id==item['food_id'] and supply.vendor_id==vendor.fact_id
        assert catalog['food',item['food_id']].name in food_reply and item['name'] in food_reply
        assert item['source_ids']==list(supply.source_ids)
    assert kaifeng.get('/api/city/v1/trips/'+trip['id']).json()==before
