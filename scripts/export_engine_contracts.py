"""Export backend tool and maintenance contracts without starting the app or touching a database."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from pydantic import BaseModel
from app.city.engine.models import PreviewRequest,ReplanRequest,ConfirmRequest,SolveResult,OperationalBundle

from app.city.basket import BasketMutation
from app.city.exploration import Plan,Answer

class Contracts(BaseModel):
    basket_mutation: BasketMutation
    exploration_preview: Plan
    exploration_answer: Answer
    preview: PreviewRequest
    replan: ReplanRequest
    confirm: ConfirmRequest
    result: SolveResult
    maintenance_only_operations: OperationalBundle

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    path=ROOT/'docs/contracts/city-engine-v1.schema.json';content=json.dumps(Contracts.model_json_schema(),ensure_ascii=False,indent=2)+'\n'
    if args.check:
        if not path.exists() or path.read_text()!=content:raise SystemExit('Engine contracts out of date')
        print('Engine contracts match source')
    else:path.write_text(content);print(path)
