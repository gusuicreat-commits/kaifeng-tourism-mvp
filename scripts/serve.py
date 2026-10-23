"""Single-origin local server; optional --dev starts Vite as a managed child."""
import os
import signal
import socket
import shutil
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/'.env')
port=int(os.getenv('BACKEND_PORT','8017'))
frontend_port=int(os.getenv('FRONTEND_PORT','5177'))
dev='--dev' in sys.argv


def check_port(p):
    sock=socket.socket()
    try:
        sock.bind(('127.0.0.1',p))
    except OSError:
        sys.exit(f'端口 {p} 不可用。请在 .env 修改端口；启动器不会终止其他服务。')
    finally:
        sock.close()


check_port(port)
if dev: check_port(frontend_port)
env=os.environ.copy()
env['PYTHONPATH']=str(ROOT/'backend')
children=[]
try:
    children.append(subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)]+(['--reload'] if dev else []),cwd=ROOT,env=env))
    if dev:
        npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
        if not npm:
            raise RuntimeError('npm not found; install Node.js and reopen the terminal')
        children.append(subprocess.Popen([npm,'run','dev','--','--port',str(frontend_port)],cwd=ROOT/'frontend',env=env))
    print(f'\n汴游： http://127.0.0.1:{frontend_port if dev else port}\nAPI 文档： http://127.0.0.1:{port}/docs\n按 Ctrl+C 停止本次启动的服务。\n',flush=True)
    def stop(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    # Poll only child processes; no background process is orphaned on exit.
    import time
    while all(child.poll() is None for child in children): time.sleep(.5)
    code=next((c.returncode for c in children if c.returncode is not None),0)
except KeyboardInterrupt:
    code=0
finally:
    for child in children:
        if child.poll() is None: child.terminate()
    for child in children:
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired: child.kill();child.wait()
sys.exit(code)
