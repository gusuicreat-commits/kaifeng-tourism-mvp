"""Repeatable V2 multi-turn acceptance in a disposable DB. --live allows <=6 model calls."""
import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    if not args.live:os.environ['TOURISM_LLM_API_KEY']=''
    report={'data':'SYNTHETIC TEST ONLY: verified flags emulated only inside disposable database',
            'real_kaifeng_data_validated':False,'live_llm_requested':args.live,'frontend_touched':False,'runtime_restarted':False}
    with tempfile.TemporaryDirectory(prefix='bianliang-city-v2-') as temporary:
        os.environ['DATABASE_PATH']=str(Path(temporary)/'tourism.sqlite3')
        os.environ['M7_DELETION_LEDGER_PATH']=str(Path(temporary)/'deletions.sqlite3')
        from fastapi.testclient import TestClient
        from app.main import app
        from app.db import connect
        from app import language
        from app.city import state,facts
        from app.city.engine import service as engine
        from app.city.engine.models import OperationalBundle
        from app.city.engine.storage import import_operations
        from app.city.assistant import context,service,tools
        from app.memory import session,storage
        if args.live and not language.available():raise SystemExit('No model configured; secret not printed.')
        clock=[datetime.fromisoformat('2030-05-01T08:00:00+08:00')]
        patches=[patch.object(module,'now',lambda:clock[0]) for module in (state,engine,context,service,tools,session,storage)]
        for handle in patches:handle.start()
        real_call=language._call;model_calls=[]
        def bounded_call(system,payload):
            if len(model_calls)>=6:raise RuntimeError('Live evaluation call cap reached')
            model_calls.append({'input_characters':len(system)+len(json.dumps(payload,ensure_ascii=False))})
            return real_call(system,payload)
        try:
            with patch.object(language,'_call',bounded_call),TestClient(app) as client:
                data=json.loads((ROOT/'backend/tests/fixtures/engine_synthetic.json').read_text())
                for record in data['facts']['records']:record['is_demo']=False
                for journey in data['operations']['journeys']:
                    journey['travel']['evidence']['kind']='verified';journey['travel']['path_status']='verified';journey['time_basis']='survey_envelope'
                for cost in data['operations']['costs']:cost['evidence']['kind']='verified'
                data['requirements']['goals'][1]['target_id']='s2'
                with connect() as db:
                    facts.import_bundle(facts.FactBundle.model_validate(data['facts']),db,apply=True)
                    import_operations(db,OperationalBundle.model_validate(data['operations']),facts.records(db),apply=True)
                uid=lambda:str(uuid4())
                client.post('/api/v1/sessions')
                trip=client.post('/api/city/v1/trips',json={'request_id':uid(),'draft':{}}).json()
                prefix='/api/city/v1/trips/'+trip['id']
                first=client.post(prefix+'/engine/preview',json={'request_id':uid(),'expected_version':1,'requirements':data['requirements']})
                assert first.status_code==200 and first.json()['status']=='preview','Initial synthetic solve failed'
                confirmed=client.post(prefix+'/engine/confirm',json={'request_id':uid(),'expected_version':1,'candidate_id':first.json()['candidate_id']})
                assert confirmed.status_code==200,'Initial confirmation failed'
                clock[0]=datetime.fromisoformat('2030-05-01T09:40:00+08:00')
                actions=[{'operation':'transition','status':'ACTIVE'},
                         {'operation':'mark_completed','stop_id':'visit','actual_start':'2030-05-01T09:00:00+08:00','actual_end':'2030-05-01T09:30:00+08:00'},
                         {'operation':'set_anchor','anchor':{'place_id':'museum','confirmed_at':clock[0].isoformat()}}]
                for action in actions:
                    current=client.get(prefix).json()
                    changed=client.patch(prefix,json={'request_id':uid(),'expected_version':current['version'],'action':action})
                    assert changed.status_code==200,'Synthetic progress preparation failed'
                before=client.get(prefix).json()
                script=[('我下一站去哪？','answered',[]),('我想先吃饭。','intent_only',[]),
                        ('下午的演出别动。','intent_only',[]),('那你帮我调整一下。','needs_confirmation',['replan_trip']),
                        ('我还是不改了。','discarded',['discard_candidate'])]
                turns=[];state_errors=wrong_tools=unconfirmed_changes=0
                candidate=None
                for text,expected_status,expected_tools in script:
                    started=time.perf_counter()
                    response=client.post(prefix+'/assistant/chat',json={'request_id':uid(),'text':text})
                    result=response.json()
                    current=client.get(prefix).json()
                    state_errors+=int(response.status_code!=200 or result.get('trip_id')!=trip['id'] or result.get('route_version')!=before['route_version'])
                    wrong_tools+=int(result.get('metrics',{}).get('tool_calls')!=expected_tools)
                    unconfirmed_changes+=int(current!=before)
                    passed=response.status_code==200 and result.get('status')==expected_status
                    if expected_status=='answered':passed=passed and result.get('next_stop',{}).get('stop_id')=='lunch'
                    if expected_status=='needs_confirmation':candidate=result.get('confirmation')
                    turns.append({'input':text,'http_status':response.status_code,'status':result.get('status'),
                        'expected_status':expected_status,'passed':passed,'reply':result.get('reply'),
                        'basis':result.get('interpretation_basis'),'metrics':result.get('metrics'),
                        'wall_ms':round((time.perf_counter()-started)*1000,2)})
                if candidate:
                    rejected=client.post(prefix+'/assistant/confirm',json={'request_id':uid(),'confirmed':True,**candidate})
                    report['cancelled_candidate_confirmation_http_status']=rejected.status_code
                timings=[];sizes=[]
                for i in range(50):
                    started=time.perf_counter()
                    envelope=client.get(prefix+'/assistant/context?purpose=next').json()
                    timings.append((time.perf_counter()-started)*1000)
                    sizes.append(len(json.dumps(envelope,ensure_ascii=False,separators=(',',':'))))
                    state_errors+=int(envelope.get('identity',{}).get('version')!=before['version'])
                dining=client.get(prefix+'/assistant/context?purpose=dining').json()['facts']
                unrelated=sum(1 for r in dining if r.get('id')!='restaurant' and r.get('place_id')!='restaurant')
                report.update(turns=turns,model_calls=model_calls,live_model_call_count=len(model_calls),
                    state_read_checks=len(script)+len(timings),state_read_errors=state_errors,
                    state_read_error_rate=state_errors/(len(script)+len(timings)),tool_checks=len(script),
                    wrong_tool_calls=wrong_tools,wrong_tool_call_rate=wrong_tools/len(script),
                    formal_mutations_without_confirmation=unconfirmed_changes,
                    repeated_confirmed_requirement_questions=sum(1 for t in turns if t['status']=='needs_clarification'),
                    context={'next_read_samples':50,'http_testclient_p50_ms':round(statistics.median(timings),3),
                             'http_testclient_p95_ms':round(sorted(timings)[47],3),'max_characters':max(sizes),
                             'dining_fact_records':len(dining),'unrelated_dining_fact_records':unrelated,
                             'unrelated_dining_fact_ratio':unrelated/len(dining) if dining else None},
                    limits='One fixed synthetic conversation; 50 local TestClient reads. Not user research, real navigation, model accuracy or production SLA.')
                basis_ok=not args.live or len(model_calls)>0 and all(t['basis']!='rule_fallback' for t in turns)
                report['status']='passed' if all(t['passed'] for t in turns) and not state_errors and not wrong_tools and not unconfirmed_changes and basis_ok else 'failed'
        finally:
            for handle in reversed(patches):handle.stop()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report.get('status')=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
