"""Cross-platform entry point; starts only this checkout's local services."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ.setdefault('PYTHONUTF8', '1')
python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
if not python.exists() or not (ROOT / 'frontend/node_modules').is_dir() or not (ROOT / 'frontend/dist/index.html').exists():
    subprocess.run([sys.executable, 'scripts/setup.py'], check=True)
os.execv(str(python), [str(python), 'scripts/serve.py', *sys.argv[1:]])
