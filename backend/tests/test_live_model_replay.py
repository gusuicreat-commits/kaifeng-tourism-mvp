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

DATA = model_replay.load()
CASES = [c for c in DATA['cases'] if c.get('replies')]


@pytest.fixture
def kaifeng(client, monkeypatch):
    model_replay.import_reference()
    moment = datetime.fromisoformat(DATA['recorded_at'])
    for module in (memory_storage, memory_session, engine, state, context, service, tools):
        monkeypatch.setattr(module, 'now', lambda: moment)
    monkeypatch.setattr(language, 'available', lambda: True)
    return client


@pytest.mark.skipif(not CASES, reason='no recorded model replies')
@pytest.mark.parametrize('case', CASES, ids=[c['text'][:16] for c in CASES])
def test_recorded_model_reply_passes_the_pipeline(kaifeng, monkeypatch, case):
    replies = iter(copy.deepcopy(case['replies']))
    monkeypatch.setattr(provider, 'generate', lambda system, payload, **kw: (next(replies), {'source': 'recorded_model'}))
    trip = model_replay.new_trip(kaifeng)
    task = model_replay.run(kaifeng, trip, case['text'])
    problems = model_replay.check(case, task, model_replay.saved(kaifeng, trip))
    assert not problems, f"{case['text']}: {problems}"
