"""Standalone synthetic acceptance demo; no DB, credentials, HTTP or LLM."""
import sys,json
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.city.facts import FactBundle
from app.city.engine.context import PlanningContext
from app.city.engine.models import Requirements, OperationalBundle
from app.city.engine.tools import plan_trip

if __name__=='__main__':
    data=json.loads((ROOT/'backend/tests/fixtures/engine_synthetic.json').read_text())
    records=FactBundle.model_validate(data['facts']).records
    ctx=PlanningContext({(r.kind,r.fact_id):r for r in records},OperationalBundle.model_validate(data['operations']),
                        datetime.fromisoformat(data['assessed_at']),synthetic=True)
    result=plan_trip(Requirements.model_validate(data['requirements']),ctx)
    print(result.model_dump_json(indent=2))
    sys.exit(0 if result.status=='feasible' else 1)
