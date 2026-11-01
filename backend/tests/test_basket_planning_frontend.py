"""Exercise basket resume with Vue's actual setup, without a browser or port."""
from pathlib import Path
import subprocess
import pytest
from test_full_flow_browser_fixture import ROOT
from test_multi_day_frontend import NODE


@pytest.mark.parametrize('case',['no-mode','no-mode-party','quick','deep','quick-stored','deep-stored','quick-missing','manual'])
def test_basket_resume_requires_trip_mode(case):
    assert NODE and (ROOT/'frontend/node_modules/typescript').exists(),'Offline component checks need installed frontend dependencies'
    result=subprocess.run([NODE,str(Path(__file__).with_name('basket_planning_frontend.cjs')),case],
        cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
