"""A/B/C regression in a disposable synthetic city; --live caps ALL Kimi requests at 12.

Corpus labels are developer curated, not independent human annotation or real logs.
A=V2, B=V3.1+fixed workflow, C=V3.1+bounded exploration. Understanding and
task completion use separate denominators; zero writes is verified for every task.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from dotenv import load_dotenv
from agent_live_diagnostics import ModelCallBudget

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--live',action='store_true');p.add_argument('--output',type=Path)
    p.add_argument('--skip-parsing',action='store_true');p.add_argument('--request-cap',type=int,default=12,choices=range(1,13))
    args=p.parse_args();load_dotenv(ROOT/'.env')
    if not args.live:os.environ['TOURISM_LLM_API_KEY']=''
    report={'date':'2026-10-21','data':'SYNTHETIC ONLY, disposable DB; developer-curated corpus; not independent human labels',
        'real_kaifeng_business_validated':False,'runtime_restarted':False,'live_requested':args.live,
        'native_function_calling_tested':False,'protocol':'bounded JSON action selection; strict server validation',
        'max_live_calls':args.request_cap,'cost_cny':None,'cost_basis':'no provider price snapshot; token usage reported when available'}
    with tempfile.TemporaryDirectory(prefix='bianliang-agent-v3-') as folder:
        os.environ['DATABASE_PATH']=str(Path(folder)/'tourism.sqlite3');os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'ledger.sqlite3')
        from fastapi.testclient import TestClient
        from app.main import app
        from app.db import connect
        from app import language
        from app.city import facts,state
        from app.city.assistant import context,service,tools,interpret
        from app.city.agent import service as agent,understand,provider
        from app.city.engine import service as engine
        from app.city.engine.models import OperationalBundle
        from app.city.engine.storage import import_operations
        from app.memory import session,storage
        if args.live and not language.available():raise SystemExit('Model not configured; secret not printed.')
        clock=[datetime.fromisoformat('2030-05-01T08:00:00+08:00')]
        patches=[patch.object(m,'now',lambda:clock[0]) for m in (state,engine,context,service,tools,session,storage)]
        for h in patches:h.start()
        bounded=ModelCallBudget(provider.generate,args.request_cap,2.0 if args.live else 0)
        calls=bounded.calls
        try:
            with patch.object(provider,'generate',bounded),TestClient(app) as client:
                data=json.loads((ROOT/'backend/tests/fixtures/engine_synthetic.json').read_text())
                for r in data['facts']['records']:r['is_demo']=False
                for j in data['operations']['journeys']:
                    j['travel']['evidence']['kind']='verified';j['travel']['path_status']='verified';j['time_basis']='survey_envelope'
                for c in data['operations']['costs']:c['evidence']['kind']='verified'
                data['requirements']['goals'][1]['target_id']='s2'
                with connect() as db:
                    facts.import_bundle(facts.FactBundle.model_validate(data['facts']),db,apply=True)
                    import_operations(db,OperationalBundle.model_validate(data['operations']),facts.records(db),apply=True)
                uid=lambda:str(uuid4());client.post('/api/v1/sessions')
                trip=client.post('/api/city/v1/trips',json={'request_id':uid(),'draft':{}}).json();base='/api/city/v1/trips/'+trip['id']
                candidate=client.post(base+'/engine/preview',json={'request_id':uid(),'expected_version':1,'requirements':data['requirements']}).json()
                assert candidate['status']=='preview'
                assert client.post(base+'/engine/confirm',json={'request_id':uid(),'expected_version':1,'candidate_id':candidate['candidate_id']}).status_code==200
                clock[0]=datetime.fromisoformat('2030-05-01T09:40:00+08:00')
                for action in [{'operation':'transition','status':'ACTIVE'},
                    {'operation':'mark_completed','stop_id':'visit','actual_start':'2030-05-01T09:00:00+08:00','actual_end':'2030-05-01T09:30:00+08:00'},
                    {'operation':'set_anchor','anchor':{'place_id':'museum','confirmed_at':clock[0].isoformat()}}]:
                    t=client.get(base).json();assert client.patch(base,json={'request_id':uid(),'expected_version':t['version'],'action':action}).status_code==200
                before=client.get(base).json()
                with connect() as db:
                    owner=db.execute('SELECT * FROM conversation_sessions LIMIT 1').fetchone();s=session.Session(owner['id'],owner['guest_token_hash'])
                snap=context.load(s,trip['id'],'modify')
                corpus=json.loads((ROOT/'backend/tests/fixtures/agent_v3_corpus.json').read_text())['cases']
                rows=[]
                for case in corpus:
                    text=case['text'];a=interpret.rules(text,snap)
                    fields=[]
                    if a.preserve_shows:fields.append('preserve_shows')
                    if a.objective:fields.append('objective')
                    if a.pace:fields.append('pace')
                    if a.ends_at:fields.append('return_by')
                    b,_=understand.parse(text,snap,allow_model=False)
                    mode,_=understand.route(b,snap);found={c.field for c in b.constraints}
                    rows.append({'id':case['id'],'A_fields_complete':set(case['expected_fields'])<=set(fields),
                                 'B_fields_complete':set(case['expected_fields'])<=found,'B_route_expected':mode==case['expected_mode'],
                                 'C_understanding_same_as_B':True})
                report['corpus']={'count':len(rows),'A_expected_field_coverage_cases':sum(r['A_fields_complete'] for r in rows),
                    'B_expected_field_coverage_cases':sum(r['B_fields_complete'] for r in rows),
                    'B_route_expected_cases':sum(r['B_route_expected'] for r in rows),'rows':rows,
                    'interpretation':'Regression coverage only; does not estimate general language accuracy. C reuses B understanding.'}
                report['live_parsing']=[]
                if args.live and not args.skip_parsing:
                    for text in ['附近找个吃饭的地方，下午演出必须看，步行不能超过十分钟。',
                                 '午饭下午四点吃，算了，还是五点吧。',
                                 '附近找个餐厅，有灌汤包最好，没有也行，晚上别太晚回酒店。']:
                        started=time.monotonic();u,meta=understand.parse(text,snap)
                        mode,questions=understand.route(u,snap)
                        report['live_parsing'].append({'input':text,'intent':u.model_dump(mode='json'),'mode':mode,'questions':questions,
                            'basis':meta['basis'],'elapsed_ms':round((time.monotonic()-started)*1000,2)})
                tasks=[]
                for name,text in [('dining_show','附近找个吃饭的地方，下午演出必须保留，最好少走一点。'),
                                  ('hard_walk','附近找个吃饭的地方，演出必须看，步行不能超过1分钟。')]:
                    t=client.post(base+'/agent/tasks',json={'request_id':uid(),'text':text,'expected_version':before['version'],'expected_route_version':before['route_version']}).json()
                    for _ in range(12):
                        if t['status']!='running':break
                        r=client.post(base+'/agent/tasks/'+t['task_id']+'/step',json={'request_id':uid(),'expected_step':t['step']})
                        assert r.status_code==200,'Task step HTTP failure';t=r.json()
                    tasks.append({'case':name,'status':t['status'],'mode':t['mode'],'metrics':t['metrics'],'issues':(t.get('result') or {}).get('issues',[]),
                        'events':t['events'],'formal_state_unchanged':client.get(base).json()==before})
                    client.post(base+'/agent/tasks/'+t['task_id']+'/cancel')
                    if args.live:break  # live suite <=3 parsing +1 task understanding +4 decisions
                report.update(tasks=tasks,model_calls=bounded.records(),live_call_count=bounded.http_attempt_count,formal_mutations_without_confirmation=sum(not t['formal_state_unchanged'] for t in tasks))
                # Paired fixed-workflow controls use identical known facts and input for the first task.
                control='附近找个吃饭的地方，下午演出必须保留，最好少走一点。'
                a=interpret.rules(control,snap)
                result,_=service._dispatch(s,trip['id'],__import__('app.city.assistant.models',fromlist=['CityChat']).CityChat(request_id=uid(),text=control),snap,a)
                b,_=understand.parse(control,snap,allow_model=False)
                fixed=agent.Task(uid(),s.id,trip['id'],__import__('app.city.agent.models',fromlist=['StartTask']).StartTask(
                    request_id=uid(),text=control,expected_version=before['version'],expected_route_version=before['route_version']),control,snap)
                fixed.intent=b;fixed.mode='workflow'
                b_result,_=agent._workflow(s,fixed)
                report['paired_exploration_case']={'input':control,'same_fact_fingerprint':snap.planning.fingerprint(),
                    'A_status':result['status'],'B_status':b_result['status'],'B_reason':'fixed workflow requires an explicit restaurant; no dynamic search',
                    'C_status':tasks[0]['status'],'scope':'one synthetic task; not statistical evidence of ReAct benefit'}
                successful_live=not args.live or bool(calls) and all(x['basis']=='kimi_validated' for x in report['live_parsing']) and tasks[0]['status']=='needs_confirmation'
                report['status']='passed' if all(r['B_fields_complete'] and r['B_route_expected'] for r in rows) and not report['formal_mutations_without_confirmation'] and successful_live else 'failed'
        finally:
            agent.TASKS.clear()
            for h in reversed(patches):h.stop()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('corpus',)},ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report['corpus'].items() if k!='rows'},ensure_ascii=False))
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
