"""One explicitly requested live interpretation, isolated DB, no formal route commit."""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from dotenv import load_dotenv
from agent_live_diagnostics import ModelCallBudget

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.live==args.check_only:raise SystemExit('Choose --live or --check-only.')
    load_dotenv(ROOT/'.env')
    report={'input':'我明天想去开封玩','database':'temporary isolated DB','max_model_requests':1,
            'formal_route_committed':False,'real_place_or_route_validation':False}
    with tempfile.TemporaryDirectory(prefix='bianliang-planning-language-') as folder:
        os.environ['DATABASE_PATH']=str(Path(folder)/'test.sqlite3')
        os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'deletion.sqlite3')
        from fastapi.testclient import TestClient
        from app.main import app
        from app import language
        from app.city.agent import provider,service
        from app.city.schema import TZ
        if args.live and not language.available():raise SystemExit('Configured language service unavailable; no request sent.')
        real_generate=provider.generate
        def guarded_generate(system,payload,**kw):
            # Fail closed before network access unless ALL state is a generated blank test trip.
            ctx=payload['context'];req=ctx['requirements']
            assert payload['text']==report['input']
            assert req=={'business_date':None,'starts_at':None,'ends_at':None,'origin_place_id':None,'destination_place_id':None,
                'interests':[],'dish_interest_ids':[],'modes':['walking'],'pace':'standard','objective':'balanced',
                'budget_fen':None,'locked_stop_ids':[],'targets':[]}
            assert ctx['basket']=={'version':0,'items':[]}
            assert ctx['planning_constraints']=={'version':0,'constraints':None,'rounds':0}
            assert ctx['references']==[] and ctx['page_focus'] is None
            assert ctx['progress']=={'completed':[],'skipped':[],'ongoing':None,'remaining':[],'next':None,'ended':False}
            report['synthetic_payload_checked']=True
            report['outgoing_payload']=payload
            if args.check_only:
                return {'actions':[{'kind':'modify','quote':report['input']}],
                    'planning':{'quote':report['input'],'fields':[{'field':'date','value':'明天','quote':'明天'}]}}, {'source':'mock_model','http_attempts':0}
            result,meta=real_generate(system,payload,**kw)
            report['model_output']=result
            return result,meta
        budget=ModelCallBudget(guarded_generate,cap=1,interval=0)
        budget.case='initial_planning_date'
        uid=lambda:str(uuid4())
        with patch.object(provider,'generate',budget),patch.object(language,'available',lambda:True),TestClient(app) as client:
            client.post('/api/v1/sessions')
            before=client.post('/api/city/v1/trips',json={'request_id':uid(),'draft':{}}).json()
            base='/api/city/v1/trips/'+before['id']
            expected=(datetime.now(TZ).date()+timedelta(days=1)).isoformat()
            task=client.post(base+'/agent/tasks',json={'request_id':uid(),'text':report['input'],
                'expected_version':before['version'],'expected_route_version':before['route_version']}).json()
            for _ in range(3):
                if task['status']!='running':break
                response=client.post(base+'/agent/tasks/'+task['task_id']+'/step',json={'request_id':uid(),'expected_step':task['step']})
                if response.status_code!=200:break
                task=response.json()
            action=(task.get('result') or {}).get('planning_action')
            report.update(task_status=task['status'],mode=task.get('mode'),interpretation=task.get('interpretation'),
                          reply=(task.get('result') or {}).get('reply'),expected_date=expected,
                          model_requests=budget.http_attempt_count,provider_calls=budget.records())
            passed=bool(action and action['constraints']['business_date']==expected and task['interpretation'].get('source')==('mock_model' if args.check_only else 'live_model'))
            if passed:
                r=client.post(base+'/exploration/preview',json={'request_id':uid(),'expected_version':action['trip_version'],
                    'basket_version':action['basket_version'],'planning_version':action['planning_version'],'constraints':action['constraints']})
                workspace=client.get(base+'/exploration/constraints').json()
                passed=r.status_code==200 and workspace['constraints']['business_date']==expected
                report['saved_constraints']=workspace['constraints']
            report['formal_state_unchanged']=client.get(base).json()==before
            report['status']='passed' if passed and report['formal_state_unchanged'] else 'failed'
        service.TASKS.clear()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','task_status','mode','model_requests','formal_state_unchanged')},ensure_ascii=False))
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
