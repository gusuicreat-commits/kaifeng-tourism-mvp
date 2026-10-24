#!/usr/bin/env python3
"""One-shot scheduled sync entry; called by system cron or task runner.

Only runs when SCENIC_QMSY_SYNC_ENABLED=1 and SCENIC_SOURCE_POLICY_REVIEWED=1.
No retries, no bypass, no private API calls.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "qmsy":
        print("Usage: scenic_sync_cron.py qmsy", file=sys.stderr)
        return 2
    if os.getenv("SCENIC_QMSY_SYNC_ENABLED") != "1" or os.getenv("SCENIC_SOURCE_POLICY_REVIEWED") != "1":
        print("scenic sync disabled until site policy and authorization are confirmed", file=sys.stderr)
        return 0
    url = os.environ.get("SCENIC_SCHEDULE_DATABASE_URL", "").strip()
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        print("SCENIC_SCHEDULE_DATABASE_URL not configured", file=sys.stderr)
        return 2
    from app.scenic_sync import service
    from app.scenic_sync.models import SyncSettings
    from app.scenic_sync.provider import QmsyunProvider
    settings = SyncSettings(database_url=url.replace("postgresql+psycopg://", "postgresql://", 1),
                            minimum_interval_minutes=int(os.getenv("SCENIC_MIN_INTERVAL_MINUTES", "60")))
    result = service.sync_qmsy(settings.database_url, QmsyunProvider(settings))
    import json
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") != "unavailable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
