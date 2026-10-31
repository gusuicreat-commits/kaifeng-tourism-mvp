"""Export backend tool and maintenance contracts without starting the app or touching a database."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from pydantic import BaseModel
from app.city.engine.models import PreviewRequest,ReplanRequest,ConfirmRequest,SolveResult,OperationalBundle

from app.city.basket import BasketMutation
from app.city.exploration import Plan,Answer,ConflictAdjustments
from app.city.engine.models import PreviewRequestV2,ReplanRequestV2,SolveResultV2,MultiDayConstraints
from app.city.exploration import PlanV2
from app.city.versions import PreviewBody,ReplanBody

class Contracts(BaseModel):
    basket_mutation: BasketMutation
    exploration_preview: Plan
    exploration_answer: Answer
    exploration_conflict_adjustments: ConflictAdjustments
    preview: PreviewRequest
    replan: ReplanRequest
    confirm: ConfirmRequest
    result: SolveResult
    maintenance_only_operations: OperationalBundle


class ContractsV2(BaseModel):
    preview: PreviewRequestV2
    replan: ReplanRequestV2
    result: SolveResultV2
    constraints: MultiDayConstraints
    exploration_preview: PlanV2
    compatible_preview: PreviewBody
    compatible_replan: ReplanBody

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for name,model in (('city-engine-v1.schema.json',Contracts),('city-engine-v2.schema.json',ContractsV2)):
        path=ROOT/'docs/contracts'/name;content=json.dumps(model.model_json_schema(),ensure_ascii=False,indent=2)+'\n'
        if args.check:
            if not path.exists() or path.read_text()!=content:raise SystemExit(name+' out of date')
            print(name+' matches source')
        else:path.write_text(content);print(path)
