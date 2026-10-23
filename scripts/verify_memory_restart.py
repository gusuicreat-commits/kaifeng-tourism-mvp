"""Actual two-process restart verification on a disposable database; never calls a cloud model."""
import argparse,json,os,socket,subprocess,sys,tempfile,time
from pathlib import Path
from uuid import uuid4
import httpx
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='m7-restart-') as directory:
        env=os.environ.copy();env.update(DATABASE_PATH=str(Path(directory)/'test.sqlite3'),M7_DELETION_LEDGER_PATH=str(Path(directory)/'ledger.sqlite3'),PYTHONPATH=str(ROOT/'backend'),TOURISM_LLM_API_KEY='')
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        process=None
        with (Path(directory)/'server.log').open('w') as log,httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=5,trust_env=False) as client:
            def launch():
                proc=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
                for _ in range(100):
                    if proc.poll() is not None:raise RuntimeError('temporary server exited')
                    try:
                        if client.get('/api/health').status_code==200:return proc
                    except httpx.TransportError:pass
                    time.sleep(.1)
                proc.terminate();proc.wait(timeout=5);raise RuntimeError('temporary server startup timed out')
            try:
                process=launch();first_pid=process.pid
                client.post('/api/v1/sessions').raise_for_status()
                trip=client.post('/api/v1/trips',json={'request_id':str(uuid4())}).json()
                result=client.post(f"/api/v1/trips/{trip['id']}/replan",json={'request_id':str(uuid4()),'expected_version':1,'constraints':{
                    'scenic_id':'wansui','dataset':'reported','must_visit':['theatre'],'available_minutes':180,'visit_start':'2026-11-01T13:00:00+08:00'}})
                result.raise_for_status();before=result.json()
                client.post('/api/v1/chat',json={'request_id':str(uuid4()),'trip_id':trip['id'],'text':'为什么这样安排？'}).raise_for_status()
                process.terminate();process.wait(timeout=5)
                process=launch()
                response=client.get(f"/api/v1/trips/{trip['id']}");response.raise_for_status();after=response.json()
                context=client.get(f"/api/v1/trips/{trip['id']}/context");context.raise_for_status()
                assert before['trip']==after
                assert client.get('/api/plans/'+after['route_id']).status_code==200
                assert len(context.json()['recent_messages'])==2
                report={'temporary_database':True,'paid_provider_calls':False,'distinct_os_processes':first_pid!=process.pid,
                        'same_cookie_authorized_after_restart':True,'same_trip_state':True,'same_route_version':True,
                        'same_route_id':True,'conversation_recovered':True,'process_restart_verified':True}
            finally:
                if process and process.poll() is None:process.terminate();process.wait(timeout=5)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
