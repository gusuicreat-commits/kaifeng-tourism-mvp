"""V3.3 live full-chain acceptance, synthetic fixtures in a disposable DB only.
Default is deterministic offline verification. --live is explicit, serial, <=12 requests;
provider failure opens a circuit breaker. No production DB, frontend or server changes.
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from dotenv import load_dotenv
from agent_live_diagnostics import ModelCallBudget

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
TEXT='我现在有点饿，想吃灌汤包，最好离我近一点，但下午那场演出千万别错过，你帮我看看怎么安排。'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live',action='store_true');p.add_argument('--request-cap',type=int,default=12,choices=range(1,13))
    p.add_argument('--interval',type=float,default=2.0);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not 2<=args.interval<=60:p.error('interval must be 2..60 seconds; task time budget remains 45s')
    load_dotenv(ROOT/'.env')
    if not args.live:os.environ['TOURISM_LLM_API_KEY']=''
    report={'date':'2026-10-21','data':'SYNTHETIC ONLY; not real Kaifeng business validation',
        'live_requested':args.live,'runtime_restarted':False,'production_db_used':False,'frontend_dist_replaced':False,
        'max_live_calls':args.request_cap,'min_request_interval_seconds':args.interval,'max_concurrency':1,
        'automatic_retries':0,'cost_cny':None,'cost_basis':'No verified price or billing snapshot',
        'cases':[],'acceptance':'PARTIAL','protocol':'bounded JSON, not native function calling'}
    uid=lambda:str(uuid4())
    with tempfile.TemporaryDirectory(prefix='agent-v33-') as folder:
        os.environ['DATABASE_PATH']=str(Path(folder)/'synthetic.sqlite3')
        os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'ledger.sqlite3')
        from fastapi.testclient import TestClient
        from app.main import app
        from app.db import connect
        from app import language
        from app.city import facts,state
        from app.city.assistant import context,service,tools
        from app.city.agent import provider,service as agent
        from app.city.engine import service as engine
        from app.city.engine.models import OperationalBundle
        from app.city.engine.storage import import_operations
        from app.memory import session,storage
        if args.live and not language.available():raise SystemExit('Model not configured; secret not printed')
        clock=[datetime.fromisoformat('2030-05-01T08:00:00+08:00')]
        patches=[patch.object(m,'now',lambda:clock[0]) for m in (state,engine,context,service,tools,session,storage)]
        for h in patches:h.start()
        bounded=ModelCallBudget(provider.generate,args.request_cap,args.interval)
        try:
            with patch.object(provider,'generate',bounded),TestClient(app) as client:
                data=json.loads((ROOT/'backend/tests/fixtures/engine_synthetic.json').read_text())
                for r in data['facts']['records']:
                    r['is_demo']=False
                    if r['kind']=='food':r['name']='灌汤包'  # still synthetic, isolated source and report labels retained
                for j in data['operations']['journeys']:
                    j['travel']['evidence']['kind']='verified';j['travel']['path_status']='verified';j['time_basis']='survey_envelope'
                for c in data['operations']['costs']:c['evidence']['kind']='verified'
                data['requirements']['goals'][1]['target_id']='s2'
                with connect() as db:
                    facts.import_bundle(facts.FactBundle.model_validate(data['facts']),db,apply=True)
                    import_operations(db,OperationalBundle.model_validate(data['operations']),facts.records(db),apply=True)
                client.post('/api/v1/sessions')
                if args.live:
                    try:
                        result,meta=provider.generate('只输出JSON对象 {"ok":true}。',{'synthetic_healthcheck':True},max_tokens=32)
                        report['preflight']={'status':'passed' if result.get('ok') is True else 'invalid_output','provider':meta}
                    except Exception:
                        report['preflight']={'status':'failed','failure':bounded.blocked}
                else:report['preflight']={'status':'not_run_offline'}
                for case in ('A_success','B_missing_supply'):
                    if bounded.blocked:break
                    if args.live and report['preflight']['status']!='passed':break
                    clock[0]=datetime.fromisoformat('2030-05-01T08:00:00+08:00')
                    trip=client.post('/api/city/v1/trips',json={'request_id':uid(),'draft':{}}).json()
                    base='/api/city/v1/trips/'+trip['id']
                    preview=client.post(base+'/engine/preview',json={'request_id':uid(),'expected_version':1,'requirements':data['requirements']}).json()
                    assert preview['status']=='preview',preview
                    r=client.post(base+'/engine/confirm',json={'request_id':uid(),'expected_version':1,'candidate_id':preview['candidate_id']})
                    assert r.status_code==200,r.text
                    clock[0]=datetime.fromisoformat('2030-05-01T09:40:00+08:00')
                    for action in [{'operation':'transition','status':'ACTIVE'},
                        {'operation':'mark_completed','stop_id':'visit','actual_start':'2030-05-01T09:00:00+08:00','actual_end':'2030-05-01T09:30:00+08:00'},
                        {'operation':'set_anchor','anchor':{'place_id':'museum','confirmed_at':clock[0].isoformat()}}]:
                        current=client.get(base).json()
                        assert client.patch(base,json={'request_id':uid(),'expected_version':current['version'],'action':action}).status_code==200
                    if case=='B_missing_supply':
                        with connect() as db:
                            supply=facts.records(db)['supply','supply'].model_dump(mode='json')|{'version':2,'validation_status':'unverified'}
                            facts.import_bundle(facts.FactBundle(records=[supply]),db,apply=True)
                    before=client.get(base).json();bounded.case=case
                    r=client.post(base+'/agent/tasks',json={'request_id':uid(),'text':TEXT,'expected_version':before['version'],'expected_route_version':before['route_version']})
                    assert r.status_code==200,r.text
                    task=r.json()
                    for _ in range(12):
                        if task['status']!='running' or bounded.blocked:break
                        r=client.post(base+'/agent/tasks/'+task['task_id']+'/step',json={'request_id':uid(),'expected_step':task['step']})
                        assert r.status_code==200,r.text
                        task=r.json()
                    unchanged=client.get(base).json()==before
                    decisions=task['decisions'];result=task.get('result') or {}
                    row={'case':case,'input':TEXT,'status':task['status'],'stop_reason':task['stop_reason'],
                         'mode':task['mode'],'understanding':task['understanding'],'interpretation':task['interpretation'],'events':task['events'],
                         'decisions':decisions,'metrics':task['metrics'],'formal_state_unchanged_before_confirmation':unchanged,
                         'result_reply':result.get('reply'),'issues':result.get('issues',[]),
                         'complete_live_chain':bool(args.live and task['status']=='needs_confirmation' and decisions
                             and task['interpretation'].get('source')=='live_model'
                             and all(d['source']=='live_model' and not d['fallback'] for d in decisions)),
                         'rejected_model_actions':sum(not d['legal'] for d in decisions),
                         'confirmation':{'status':'not_run'}}
                    if case=='A_success' and task['status']=='needs_confirmation' and not bounded.blocked:
                        candidate=result['planning']['result']['itineraries'][0]
                        row['candidate_checks']={'preserves_show_s2':any(a.get('session_id')=='s2' for a in candidate['activities']),
                            'preserves_completed_visit':any(s['stop_id']=='visit' for s in candidate['completed_prefix']),
                            'requested_constraints':task['understanding']['constraints'],
                            'activities':[{k:a.get(k) for k in ('goal_id','place_id','session_id','starts_at','ends_at')} for a in candidate['activities']]}
                        # Explicit test-user operation; model cannot call this endpoint/tool.
                        r=client.post(base+'/assistant/confirm',json={'request_id':uid(),'confirmed':True,**result['confirmation']})
                        after=client.get(base).json()
                        row['confirmation']={'status':'passed' if r.status_code==200 else 'failed','http_status':r.status_code,
                            'actor':'explicit_synthetic_test_user','version_before':before['version'],'version_after':after['version'],
                            'route_version_before':before['route_version'],'route_version_after':after['route_version'],
                            'completed_history_preserved':after['draft']['stops'][0]==before['draft']['stops'][0],
                            'objective_after':after['draft']['objective'],
                            'protected_show_retained':any(s.get('session_id')=='s2' and s['locked'] for s in after['draft']['stops'])}
                        assert r.status_code==200 and after['route_version']==before['route_version']+1
                        assert after['version']==before['version']+1 and row['confirmation']['completed_history_preserved'] and row['confirmation']['protected_show_retained']
                    else:
                        cancelled=client.post(base+'/agent/tasks/'+task['task_id']+'/cancel')
                        row['cancel_http_status']=cancelled.status_code
                        row['formal_state_unchanged_after_cancel']=client.get(base).json()==before
                    report['cases'].append(row)
                    assert unchanged,'Unauthorized formal state mutation'
                    if task['status']=='running' or (case=='A_success' and not row['complete_live_chain'] and args.live):break
                calls=bounded.records()
                report.update(model_calls=calls,live_call_count=bounded.http_attempt_count if args.live else 0,provider_invocations=len(calls),
                    provider_failure=bounded.blocked,known_usage={k:sum(c.get('usage',{}).get(k,0) for c in calls) for k in ('prompt_tokens','completion_tokens','total_tokens')},
                    usage_missing_calls=sum(not c.get('usage') for c in calls),
                    formal_mutations_without_confirmation=sum(not c['formal_state_unchanged_before_confirmation'] for c in report['cases']))
                a=next((c for c in report['cases'] if c['case']=='A_success'),{})
                b=next((c for c in report['cases'] if c['case']=='B_missing_supply'),{})
                external=bounded.blocked and (bounded.blocked.get('upstream_status') in (401,403,404,429) or bounded.blocked.get('code')=='MODEL_TRANSPORT_ERROR')
                passed=a.get('complete_live_chain') and a['confirmation']['status']=='passed' and a['confirmation']['objective_after']=='less_walking' and all(a['candidate_checks'][k] for k in ('preserves_show_s2','preserves_completed_visit'))
                report['offline_suite_status']='passed' if not args.live and a.get('status')=='needs_confirmation' and a['confirmation']['status']=='passed' and b.get('status')=='needs_data' else 'not_applicable' if args.live else 'failed'
                report['acceptance']='BLOCKED' if external else 'PASS' if passed else 'PARTIAL'
                report['observation_comparison']={'same_input':bool(b) and a.get('input')==b.get('input'),
                    'A_tools':[d.get('action',{}).get('tool') for d in a.get('decisions',[])],
                    'B_tools':[d.get('action',{}).get('tool') for d in b.get('decisions',[])],
                    'B_safe_no_candidate':bool(b) and b['status']=='needs_data' and b['formal_state_unchanged_before_confirmation'],
                    'scope':'one synthetic pair, not general model accuracy'}
        except Exception as exc:
            report.update(acceptance='FAIL',failure={'code':'EVALUATION_ASSERTION_OR_INTERNAL_ERROR','exception_type':type(exc).__name__},
                model_calls=bounded.records(),live_call_count=bounded.http_attempt_count if args.live else 0,provider_invocations=len(bounded.calls))
        finally:
            agent.TASKS.clear()
            for h in reversed(patches):h.stop()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','model_calls')},ensure_ascii=False,indent=2))
    print(json.dumps({'cases':[{'case':c['case'],'status':c['status'],'complete_live_chain':c['complete_live_chain'],'confirmation':c['confirmation']} for c in report['cases']]},ensure_ascii=False))
    return 0 if report['acceptance']=='PASS' or (not args.live and report.get('offline_suite_status')=='passed') else 1


if __name__=='__main__':raise SystemExit(main())
