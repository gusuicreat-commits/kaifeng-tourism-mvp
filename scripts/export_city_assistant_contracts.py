"""Generate V2 assistant contracts from source, with no application startup or data writes."""
import argparse
import json
import sys
from pathlib import Path
from pydantic import BaseModel

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.city.assistant.models import CityChat, CityContext, Intent, ConfirmAssistant, DiscardAssistant
from app.city.state_schema import CityDraft


class Contracts(BaseModel):
    chat: CityChat
    context: CityContext
    model_intent_not_a_write_permission: Intent
    confirm: ConfirmAssistant
    cancel: DiscardAssistant
    city_draft: CityDraft


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args()
    target=ROOT/'docs/contracts/city-assistant-v2.schema.json'
    text=json.dumps(Contracts.model_json_schema(),ensure_ascii=False,indent=2)+'\n'
    if args.check:
        if not target.exists() or target.read_text()!=text:raise SystemExit('City assistant contracts out of date')
        print('City assistant contracts match source')
    else:target.write_text(text);print(target)
