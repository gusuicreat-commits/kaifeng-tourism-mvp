"""Explicit live smoke: temporary DB, real providers, no payment or runtime fact import."""
import argparse
from datetime import datetime,timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    if not args.live:parser.error('Real provider requests require --live')
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    from fastapi.testclient import TestClient
    from app.city.schema import TZ
    from app.main import app
    report={'checked_at':datetime.now(TZ).isoformat(),'mode':'real_providers_temporary_database','scenarios':{}}
    with tempfile.TemporaryDirectory(prefix='flyai-live-') as folder:
        os.environ['DATABASE_PATH']=str(Path(folder)/'tourism.sqlite3')
        os.environ['M7_DELETION_LEDGER_PATH']=str(Path(folder)/'deletions.sqlite3')
        with TestClient(app) as c:
            c.post('/api/v1/sessions');trip=c.post('/api/city/v1/trips',json={'request_id':str(uuid4()),'draft':{}}).json();path='/api/city/v1/trips/'+trip['id'];version=0
            tomorrow=(datetime.now(TZ).date()+timedelta(days=1)).isoformat()
            for key,text in [('hotel',f'找开封酒店 {tomorrow} 入住，住1晚，两个人，1间房，200元以内，必须有窗户和干湿分离，早餐也要有，最好免费停车'),('parking','搜索清明上河园停车场，自驾，少走路'),('merchants','搜索开封灌汤包餐厅')]:
                response=c.post(path+'/services/query',json={'text':text,'expected_version':version});response.raise_for_status()
                w=response.json()['services'];version=w['version'];r=w['result']
                report['scenarios'][key]={'status':r['status'],'count':len(r['items']),'sample':r['items'][:2],'budget':r['budget']}
                if key=='hotel' and r['items']:
                    pre=c.post(path+'/services/preview',json={'request_id':str(uuid4()),'expected_version':version,'candidate_id':r['items'][0]['id']});pre.raise_for_status();p=pre.json()
                    result=c.post(path+'/services/confirm',json={'request_id':str(uuid4()),'expected_version':version,'preview_id':p['preview_id'],'confirmed':True});result.raise_for_status();version=result.json()['workspace']['version']
                    report['hotel_selection']={'saved':True,'planning_status':result.json()['planning_status'],'formal_route_version':c.get(path).json()['route_version']}
            # Existing reference adapter obtains real walking estimate, still not certified entrance navigation.
            task=c.post(path+'/agent/tasks',json={'request_id':str(uuid4()),'text':'从开封站到清明上河园步行怎么走','expected_version':1,'expected_route_version':0}).json()
            for _ in range(5):
                if task['status']!='running':break
                task=c.post(path+'/agent/tasks/'+task['task_id']+'/step',json={'request_id':str(uuid4()),'expected_step':task['step']}).json()
            report['reference_route']={'status':task['status'],'result':task.get('result')}
    target=ROOT/'docs/reports/flyai-live-20261024.json';target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'report':str(target),'scenarios':{k:{f:v[f] for f in ('status','count')} for k,v in report['scenarios'].items()},'selection':report.get('hotel_selection'),'reference_route':report['reference_route']['status']},ensure_ascii=False))

if __name__=='__main__':main()
