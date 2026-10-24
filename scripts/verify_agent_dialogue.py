"""Bounded synthetic dialogue through the real task API; never opens the runtime database."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from dotenv import load_dotenv
from agent_live_diagnostics import ModelCallBudget

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
TEXTS=('我明天想去开封玩','你能干嘛','什么叫补齐候选要求？','那我接下来该做什么？')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.live==args.check_only:raise SystemExit('Choose --live or --check-only.')
    load_dotenv(ROOT/'.env')
    report={'database':'temporary empty isolated DB','max_model_requests':4,'real_user_data_sent':False,
            'real_place_or_route_validation':False,'turns':[]}
    with tempfile.TemporaryDirectory(prefix='bianliang-dialogue-') as folder:
        os.environ['DATABASE_PATH']=str(Path(folder)/'test.sqlite3')
        os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'deletion.sqlite3')
        from fastapi.testclient import TestClient
        from app.main import app
        from app import language
        from app.city.agent import provider,service
        if args.live and not language.available():raise SystemExit('No model configured; no request sent.')
        real_generate=provider.generate
        synthetic_history=[]
        def guarded_generate(system,payload,**kwargs):
            ctx=payload['context'];req=ctx['requirements']
            assert payload['text'] in TEXTS
            empty_requirements={'business_date':None,'starts_at':None,'ends_at':None,'origin_place_id':None,'destination_place_id':None,
                'interests':[],'dish_interest_ids':[],'modes':['walking'],'pace':'standard','objective':'balanced',
                'budget_fen':None,'locked_stop_ids':[],'targets':[]}
            assert req==empty_requirements | (ctx['planning_constraints']['constraints'] or {})
            assert ctx['basket']=={'version':0,'items':[]} and ctx['material_labels']==[]
            assert ctx['references']==[] and ctx['page_focus'] is None
            assert ctx['progress']=={'completed':[],'skipped':[],'ongoing':None,'remaining':[],'next':None,'ended':False}
            assert ctx['recent_messages']==synthetic_history[-8:]
            assert ctx['official_plan']=={'status':'not_created','route_version':0,'stale_reasons':[]}
            assert ctx['planning_readiness']['place_catalog_available'] is False
            saved=ctx['planning_constraints']['constraints']
            assert saved is None or (saved['origin_place_id'] is None and saved['destination_place_id'] is None)
            report['turns'][-1]['outgoing_payload']=payload
            if args.check_only:
                result=({'actions':[{'kind':'modify','quote':TEXTS[0]}],
                         'planning':{'quote':TEXTS[0],'fields':[{'field':'date','value':'明天','quote':'明天'}]}}
                        if payload['text']==TEXTS[0] else {'conversation':{'reply':'这是隔离检查用的合成回复。'}})
                return result,{'source':'mock_model','http_attempts':0}
            result,meta=real_generate(system,payload,**kwargs)
            report['turns'][-1]['model_output']=result
            return result,meta
        budget=ModelCallBudget(guarded_generate,cap=4,interval=0)
        uid=lambda:str(uuid4())
        passed=True
        with patch.object(provider,'generate',budget),patch.object(language,'available',lambda:True),TestClient(app) as client:
            client.post('/api/v1/sessions')
            before=client.post('/api/city/v1/trips',json={'request_id':uid(),'draft':{}}).json()
            base='/api/city/v1/trips/'+before['id']
            for index,text in enumerate(TEXTS):
                budget.case='dialogue_turn_'+str(index+1)
                turn={'text':text};report['turns'].append(turn)
                workspace_before=client.get(base+'/exploration/constraints').json()
                task=client.post(base+'/agent/tasks',json={'request_id':uid(),'text':text,'expected_version':1,'expected_route_version':0}).json()
                for _ in range(8):
                    if task['status']!='running':break
                    if task.get('waiting'):
                        delay=task['waiting']['retry_after_seconds']
                        assert 0<=delay<=40
                        time.sleep(delay+.05)
                    response=client.post(base+'/agent/tasks/'+task['task_id']+'/step',json={'request_id':uid(),'expected_step':task['step']})
                    assert response.status_code==200,response.text
                    task=response.json()
                reply=(task.get('result') or {}).get('reply','')
                turn.update(status=task['status'],mode=task.get('mode'),reply=reply,interpretation=task.get('interpretation'))
                synthetic_history.extend([{'role':'user','text':text[:500]},{'role':'assistant','text':reply[:500]}])
                if index==0:
                    workflow=(task.get('result') or {}).get('planning_workflow')
                    workspace=client.get(base+'/exploration/constraints').json()
                    passed=bool(workflow and workspace['version']==workflow['result']['planning_version'])
                else:
                    unchanged=client.get(base+'/exploration/constraints').json()==workspace_before
                    turn['planning_state_unchanged']=unchanged
                    passed=passed and task.get('mode')=='conversation' and task['status']=='answered' and unchanged
                    if index==3 and args.live:
                        # Route availability must agree with the empty catalog, not just yield fluent text.
                        grounded=('待添加' in reply or '资料' in reply and any(x in reply for x in ('缺','不足','未','还没','准备')))
                        turn['mentions_missing_product_data']=grounded
                        passed=passed and grounded
                passed=passed and client.get(base).json()==before
                if not passed:break
            report.update(status='passed' if passed and len(report['turns'])==4 else 'failed',
                          model_requests=budget.http_attempt_count,provider_calls=budget.records(),
                          formal_state_unchanged=client.get(base).json()==before)
        service.TASKS.clear()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','model_requests','formal_state_unchanged')},ensure_ascii=False))
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
