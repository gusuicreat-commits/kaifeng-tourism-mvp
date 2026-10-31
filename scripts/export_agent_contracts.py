"""Export V3.1 interpretation and V3.2 task input contracts without running the app."""
import argparse
import json
import sys
from pathlib import Path
from pydantic import BaseModel
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.city.agent.models import StartTask,AdvanceTask,Understanding,Decision,Budget,ReferenceSelection,BasketProposal,BasketChoicePrompt,TaskResult


class Contracts(BaseModel):
    result: TaskResult
    start: StartTask
    advance: AdvanceTask
    reference_location_selection: ReferenceSelection
    basket_proposal: BasketProposal
    basket_choice: BasketChoicePrompt
    understanding: Understanding
    model_decision_not_write_permission: Decision
    limits: Budget


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args()
    target=ROOT/'docs/contracts/city-agent-v3.schema.json'
    content=json.dumps(Contracts.model_json_schema(),ensure_ascii=False,indent=2)+'\n'
    if args.check:
        if not target.exists() or target.read_text()!=content:raise SystemExit('V3 contracts out of date')
        print('V3 contracts match source')
    else:target.write_text(content);print(target)
