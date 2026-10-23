"""Local synthetic context-read benchmark; never uses the development DB or a paid model."""
import argparse,json,os,platform,sys,tempfile,time
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='m7-benchmark-') as folder:
        os.environ['DATABASE_PATH']=str(Path(folder)/'benchmark.sqlite3')
        os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'deletions.sqlite3')
        os.environ['TOURISM_LLM_API_KEY']=''
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            client.post('/api/v1/sessions').raise_for_status()
            trip=client.post('/api/v1/trips',json={'request_id':str(uuid4())}).json()
            response=client.post(f"/api/v1/trips/{trip['id']}/replan",json={'request_id':str(uuid4()),'expected_version':1,'constraints':{
                'scenic_id':'wansui','available_minutes':180,'must_visit':['theatre'],'dataset':'reported','visit_start':'2026-11-01T13:00:00+08:00'}})
            response.raise_for_status()
            for i in range(10):
                client.post('/api/v1/chat',json={'request_id':str(uuid4()),'trip_id':trip['id'],'text':f'第{i}轮：为什么这样安排？'}).raise_for_status()
            values=[]
            for _ in range(100):
                started=time.perf_counter()
                result=client.get(f"/api/v1/trips/{trip['id']}/context")
                result.raise_for_status();values.append((time.perf_counter()-started)*1000)
            values.sort()
            report={'date':time.strftime('%Y-%m-%d'),'synthetic':True,'environment':f'{platform.system()} {platform.machine()} / Python {platform.python_version()} / temporary SQLite / FastAPI TestClient',
                    'operation':'authenticated context read + fresh fact lookup, excluding external LLM','samples':len(values),
                    'p50_ms':round(values[49],3),'p95_ms':round(values[94],3),'max_ms':round(max(values),3),
                    'engineering_target_ms':150,'target_met_locally':values[94]<=150,
                    'production_claim':False,'recent_messages':len(result.json()['recent_messages']),'summary_characters':len(result.json()['summary']['text'])}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
