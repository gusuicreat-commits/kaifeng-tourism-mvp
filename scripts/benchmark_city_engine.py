"""Reproducible synthetic 10/20/30-candidate benchmark; no network or database."""
import argparse,json,platform,sys,time
from datetime import datetime
from pathlib import Path
from importlib.metadata import version
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.city import facts
from app.city.engine.context import PlanningContext
from app.city.engine.models import OperationalBundle, Requirements
from app.city.engine.tools import plan_trip


def case(n):
    d=json.loads((ROOT/'backend/tests/fixtures/engine_synthetic.json').read_text())
    d['requirements']['goals']=[];d['requirements']['solve_seconds']=1;d['requirements']['alternatives']=1
    d['requirements']['pace']='standard'
    records=d['facts']['records'];place=next(r for r in records if r['kind']=='place' and r['fact_id']=='museum')
    rule=next(r for r in records if r['kind']=='open_rule' and r['place_id']=='museum')
    cost=d['operations']['costs'][0];journey=d['operations']['journeys'][0]
    names=['origin','hotel']
    for i in range(n):
        pid=f'candidate:{i}';names.append(pid)
        records.append(place|{'fact_id':pid,'name':'[SYNTHETIC] '+pid})
        records.append(rule|{'fact_id':'open:'+pid,'place_id':pid})
        d['operations']['costs'].append(cost|{'place_id':pid})
        d['requirements']['goals'].append({'goal_id':f'goal:{i}','target_kind':'place','target_id':pid,'required':i<3,
                                          'duration_seconds':900,'importance':(i%10)+1})
    journeys=[]
    for i,a in enumerate(names):
        for j,b in enumerate(names):
            seconds=0 if a==b else 90+abs(i-j)*10
            journeys.append(journey|{'journey_id':f'j:{i}:{j}','travel':journey['travel']|{
                'origin_place_id':a,'destination_place_id':b,'lower_seconds':seconds,'upper_seconds':seconds},'distance_m':seconds})
    d['operations']['journeys']=journeys
    rs=facts.FactBundle.model_validate(d['facts']).records
    return Requirements.model_validate(d['requirements']),PlanningContext({(r.kind,r.fact_id):r for r in rs},
         OperationalBundle.model_validate(d['operations']),datetime.fromisoformat(d['assessed_at']),True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path);a=p.parse_args()
    rows=[]
    for n in [10,20,30]:
        req,ctx=case(n);start=time.perf_counter();r=plan_trip(req,ctx)
        rows.append({'input_candidates':n,'status':r.status,'wall_ms':round((time.perf_counter()-start)*1000,3),
                     'selected':len(r.itineraries[0].activities) if r.itineraries else 0,'metrics':r.metrics})
    report={'data':'synthetic only, not measured Kaifeng travel','python':platform.python_version(),
            'platform':platform.platform(),'ortools':version('ortools'),'results':rows,
            'limits':'single run per size on this machine; not a production latency or global optimality guarantee'}
    if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if all(r['status']=='feasible' for r in rows) else 1

if __name__=='__main__':raise SystemExit(main())
