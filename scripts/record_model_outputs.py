#!/usr/bin/env python3
"""Record real model replies for the replay regression set.

Calls the configured model (TOURISM_LLM_* in .env) once per model step for every
sentence in backend/tests/fixtures/live_model_outputs.json, on a temporary database
with the reference bundle, and stores the raw replies next to each sentence. The
replay test then runs those replies through the full pipeline without network.

  .venv/bin/python scripts/record_model_outputs.py            # record all sentences
  .venv/bin/python scripts/record_model_outputs.py --missing  # only sentences without a recording

Costs one or two model calls per sentence. Review the diff before committing.
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
from dotenv import load_dotenv

load_dotenv(ROOT / '.env')
tmp = tempfile.mkdtemp(prefix='model-recording-')
os.environ['DATABASE_PATH'] = tmp + '/recording.sqlite3'
os.environ['M7_DELETION_LEDGER_PATH'] = tmp + '/deletions.sqlite3'
for name in ('AMAP_PLANNING_ENABLED', 'AMAP_LOCATION_SEARCH_ENABLED', 'AMAP_REFERENCE_ROUTES_ENABLED', 'QWEATHER_ENABLED', 'FLYAI_ENABLED'):
    os.environ.pop(name, None)  # Only the model is live; replay runs without these providers too.

from fastapi.testclient import TestClient
from app.main import app
from app import language
from app.city.agent import provider
from app.city.schema import TZ
import model_replay


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--missing', action='store_true', help='只录制还没有录音的句子')
    args = parser.parse_args()
    if not language.available():
        raise SystemExit('模型未配置：请在 .env 设置 TOURISM_LLM_BASE_URL / TOURISM_LLM_API_KEY / TOURISM_LLM_MODEL')
    data = model_replay.load()
    real, calls = provider.generate, []

    def capture(system, payload, **kw):
        raw, usage = real(system, payload, **kw)
        calls.append(raw)
        return raw, usage

    provider.generate = capture
    with TestClient(app, base_url='http://127.0.0.1') as client:
        model_replay.import_reference()
        recorded_at = datetime.now(TZ).replace(microsecond=0)
        for case in data['cases']:
            if args.missing and case.get('replies'):
                continue
            calls.clear()
            trip = model_replay.new_trip(client)
            task = model_replay.run(client, trip, case['text'])
            case['replies'] = list(calls)
            if args.missing:
                case['recorded_at'] = recorded_at.isoformat()  # the file-level time stays valid for the others
            problems = model_replay.check(case, task, model_replay.saved(client, trip))
            print(('ok   ' if not problems else 'FAIL ') + case['text'] + ('' if not problems else '  ' + '; '.join(problems)))
    if not args.missing:
        for case in data['cases']:
            case.pop('recorded_at', None)
        data.update(recorded_at=recorded_at.isoformat(), model=os.getenv('TOURISM_LLM_MODEL'))
    model_replay.RECORDINGS.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'\n已写入 {model_replay.RECORDINGS.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
