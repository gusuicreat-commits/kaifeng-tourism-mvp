"""Read-only localhost check: running backend versus this checkout on disk."""
import argparse
import json
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    from dotenv import load_dotenv
    import os
    from app.runtime import compare, disk_identity

    load_dotenv(ROOT / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=int(os.getenv('BACKEND_PORT', '8017')))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    url = f'http://127.0.0.1:{args.port}/api/health'
    disk = disk_identity()
    try:
        with build_opener(ProxyHandler({})).open(url, timeout=5) as response:
            health = json.load(response)
        if health.get('status') != 'ok':
            raise ValueError('unhealthy')
    except (URLError, OSError, ValueError):
        print(json.dumps({'status': 'unavailable', 'url': url}, ensure_ascii=False))
        return 2
    status, mismatches = compare(health.get('runtime'), disk)
    print(json.dumps({'status': status, 'mismatches': mismatches,
                      'running': health.get('runtime'), 'disk': disk},
                     ensure_ascii=False, indent=2))
    return 0 if status == 'current' else 1


if __name__ == '__main__':
    raise SystemExit(main())
