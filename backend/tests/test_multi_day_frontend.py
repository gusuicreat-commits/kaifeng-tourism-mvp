"""Run B2.5's actual TS display projections offline, independent of browser availability."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[2]
NODE=shutil.which('node')
FRONTEND=ROOT/'frontend'


@pytest.mark.parametrize('case',['adapters','route_days','map_today_and_future','map_versions','journey_focus'])
def test_multi_day_frontend_projection(case):
    if not NODE or not (FRONTEND/'node_modules/typescript').exists():
        pytest.skip('Offline frontend projection checks need node and installed frontend dependencies')
    result=subprocess.run([NODE,str(Path(__file__).with_name('multi_day_frontend.cjs')),case],
        cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
