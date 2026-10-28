"""B2.5 browser-only synthetic inputs; never imported into a running database."""
import copy
from datetime import datetime,timedelta
from multi_day_helpers import requirements


def browser_requirements():
    req=requirements(2)
    for day in req['days']:day['ends_at']=day['business_date']+'T20:00:00+08:00'
    req['goals']=[{'goal_id':'museum-today','target_kind':'place','target_id':'museum','duration_seconds':1800,
        'required':True,'allowed_dates':['2030-05-01'],'fixed_date':'2030-05-01'},
        {'goal_id':'restaurant-tomorrow','target_kind':'place','target_id':'restaurant','duration_seconds':1200,
        'required':True,'allowed_dates':['2030-05-02'],'fixed_date':'2030-05-02'}]
    return req


def dated_records(data):
    records=[]
    for record in data['facts']['records']:
        if record['kind']!='open_rule':continue
        item=copy.deepcopy(record);item['fact_id']+=':day2'
        item['business_date']='2030-05-02'
        for key in ('opens_at','closes_at','last_entry_at'):
            if item.get(key):item[key]=(datetime.fromisoformat(item[key])+timedelta(days=1)).isoformat()
        records.append(item)
    return records
