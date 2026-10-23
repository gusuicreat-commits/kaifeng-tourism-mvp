"""Opt-in real Kimi smoke test; synthetic text only, no database or visitor context."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Allow one potentially billed generation')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    from app.language import available, _call
    from app.model_schedule import scheduler
    if not args.live:
        print(json.dumps({'configured': bool(available()), 'live_test': 'not_requested'}))
        return 0
    if not available():
        print(json.dumps({'status': 'not_configured'}))
        return 1
    # Never send this key to a URL supplied by a caller or follow redirects.
    base = os.environ['TOURISM_LLM_BASE_URL'].rstrip('/')
    if base != 'https://api.moonshot.cn/v1':
        print(json.dumps({'status': 'unexpected_endpoint'}))
        return 1
    report = {'provider': 'Kimi', 'model': os.environ['TOURISM_LLM_MODEL'],
              'data': 'synthetic text only; no visitor state', 'generation_requests': 0}
    before=scheduler().attempts
    try:
        started = time.monotonic()
        result = _call('只提取游玩时长，返回 JSON 对象 {"duration_minutes":整数}。',
                       {'text': '我计划游玩两小时。', 'test_only': True})
        report['generation_requests'] = 1
        report['elapsed_ms'] = round((time.monotonic() - started) * 1000, 1)
        report['status'] = 'passed' if result == {'duration_minutes': 120} else 'semantic_check_failed'
    except Exception as exc:
        # Upstream response bodies, URLs, headers and exception strings may contain secrets.
        report['status'] = 'failed'
        report['error_type'] = type(exc).__name__
        if hasattr(exc, 'status_code'): report['application_http_status'] = exc.status_code
        if isinstance(getattr(exc,'detail',None),dict) and exc.detail.get('code')=='MODEL_WAITING':
            report.update(status='waiting',waiting=exc.detail)
    report['generation_requests']=scheduler().attempts-before
    report['scheduler']=scheduler().describe()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get('status') == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
