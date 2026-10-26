"""Shared setup for recording real model replies and replaying them in tests.

Both sides use the same reference bundle and a fresh trip per sentence, so the
context the model saw when recording is the context the replay runs in.
"""
import json
from pathlib import Path
from uuid import uuid4

from app.db import connect
from app.city import facts

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / 'data/mvp/city-facts-reference-2026-10-25.json'
RECORDINGS = Path(__file__).parent / 'fixtures/live_model_outputs.json'


def load():
    return json.loads(RECORDINGS.read_text(encoding='utf-8'))


def import_reference():
    with connect() as db:
        facts.import_bundle(facts.FactBundle.model_validate(json.loads(BUNDLE.read_text(encoding='utf-8'))), db, apply=True)


def new_trip(client):
    client.post('/api/v1/sessions')
    return client.post('/api/city/v1/trips', json={'request_id': str(uuid4()), 'draft': {}}).json()


def run(client, trip, text):
    path = f"/api/city/v1/trips/{trip['id']}"
    current = client.get(path).json()
    task = client.post(path + '/agent/tasks', json={'request_id': str(uuid4()), 'text': text,
        'expected_version': current['version'], 'expected_route_version': current['route_version']}).json()
    for _ in range(12):
        if task.get('status') != 'running':
            break
        task = client.post(path + f"/agent/tasks/{task['task_id']}/step",
                           json={'request_id': str(uuid4()), 'expected_step': task['step']}).json()
    return task


def saved(client, trip):
    return client.get(f"/api/city/v1/trips/{trip['id']}/exploration/constraints").json()['constraints'] or {}


def check(case, task, constraints):
    """Return a list of unmet expectations for one replayed sentence."""
    expect, problems = case['expect'], []
    meta = task.get('interpretation') or {}
    if meta.get('basis') != 'kimi_validated':
        problems.append(f"basis={meta.get('basis')} failure={meta.get('validation_failure')}")
    if task.get('mode') not in expect['mode']:
        problems.append(f"mode={task.get('mode')} expected {expect['mode']}")
    for key, value in expect.get('saved', {}).items():
        got = constraints.get(key)
        if key in ('starts_at', 'ends_at'):
            got = got[11:16] if isinstance(got, str) else got
        if got != value:
            problems.append(f'{key}={got} expected {value}')
    return problems
