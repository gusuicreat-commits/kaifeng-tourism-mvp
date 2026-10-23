"""Install a clean checkout on Windows, macOS or Linux using locked dependencies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def run(args):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def main():
    os.environ.setdefault('PYTHONUTF8', '1')
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11+ required')
    node = shutil.which('node')
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not node or not npm:
        raise SystemExit('Install Node.js 22.12+ (including npm), then reopen the terminal.')
    version = json.loads(subprocess.check_output(
        [node, '-p', 'JSON.stringify(process.versions.node.split(".").map(Number))'], text=True))
    if tuple(version[:2]) < (22, 12):
        raise SystemExit('Node.js 22.12+ required')
    if not VENV_PYTHON.exists():
        run([sys.executable, '-m', 'venv', ROOT / '.venv'])
    run([VENV_PYTHON, '-m', 'pip', 'install', '-r', 'backend/requirements.lock',
         '-r', 'backend/requirements-data.lock'])
    run([npm, 'ci', '--prefix', 'frontend'])
    run([npm, 'run', 'build', '--prefix', 'frontend'])
    run([VENV_PYTHON, 'scripts/init_db.py'])
    print('Setup complete. Run: python scripts/start.py')


if __name__ == '__main__':
    main()
