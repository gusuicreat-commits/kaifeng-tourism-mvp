"""Startup identity for local release checks; never includes settings or secrets."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def source_fingerprint(directory):
    directory = Path(directory)
    digest = hashlib.sha256()
    for path in sorted(directory.rglob('*.py')):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def disk_identity(root=ROOT):
    root = Path(root).resolve()
    try:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root,
                                capture_output=True, text=True, timeout=2, check=True)
        head = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = None  # Source packages may have no Git metadata.
    return {'checkout_id': hashlib.sha256(str(root).encode()).hexdigest()[:16],
            'backend_sha256': source_fingerprint(root / 'backend' / 'app'),
            'git_head': head}


def compare(loaded, disk):
    if not isinstance(loaded, dict):
        return 'unknown', ['startup_identity_missing']
    mismatches = [key for key in ('checkout_id', 'backend_sha256', 'git_head')
                  if loaded.get(key) != disk.get(key)]
    return ('restart_required' if mismatches else 'current'), mismatches


# Captured once, before main imports the business modules. Health must not
# recalculate this from disk and falsely describe an old process as current.
STARTUP = {**disk_identity(), 'pid': os.getpid(),
           'started_at': datetime.now(timezone.utc).isoformat()}
