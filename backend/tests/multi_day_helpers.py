"""Synthetic V2 shapes only; these are not solver or real-route acceptance fixtures."""
from datetime import date, datetime, timedelta


def requirements(count=2):
    start=date(2030,5,1);dates=[(start+timedelta(days=i)).isoformat() for i in range(count)]
    days=[{'business_date':d,'starts_at':d+'T09:00:00+08:00','ends_at':d+'T10:00:00+08:00',
           'origin_place_id':'origin' if i==0 else 'hotel','destination_place_id':'hotel'} for i,d in enumerate(dates)]
    return {'schema_version':'city-engine/2','start_date':dates[0],'end_date':dates[-1],'days':days,
            'goals':[{'goal_id':'flexible','target_kind':'place','target_id':'museum','required':True,'allowed_dates':dates},
                     {'goal_id':'fixed','target_kind':'place','target_id':'restaurant','allowed_dates':[dates[-1]],'fixed_date':dates[-1]}],
            'overnights':[{'overnight_id':'night:'+d,'from_day_id':'day:'+d,'to_day_id':'day:'+dates[i+1],
                'night_date':d,'place_id':'hotel','source_kind':'user_place','place_evidence':'synthetic','stay_evidence':'unknown'}
                for i,d in enumerate(dates[:-1])]}


def draft(count=2):
    req=requirements(count)
    return {'schema_version':'city-draft/2','start_date':req['start_date'],'end_date':req['end_date'],
            'days':[{**d,'stops':[]} for d in req['days']],'overnights':req['overnights']}


def itinerary(count=2):
    req=requirements(count);days=[]
    for d in req['days']:
        days.append({'business_date':d['business_date'],'original_requirements':d,
            'requirements':{**d,'goals':[]},'itinerary':{'activities':[],'legs':[],'returns_at':d['ends_at'],
                'total_seconds':3600,'travel_seconds':0,'walking_distance_m':0,'known_cost_upper_fen':0,
                'cost_unknown':True,'score':{},'evidence_mode':'synthetic'}})
    elapsed=int((datetime.fromisoformat(req['days'][-1]['ends_at'])-datetime.fromisoformat(req['days'][0]['starts_at'])).total_seconds())
    return {'schema_version':'city-itinerary/2','start_date':req['start_date'],'end_date':req['end_date'],
            'days':days,'overnights':req['overnights'],'active_seconds':count*3600,'elapsed_seconds':elapsed,
            'travel_seconds':0,'walking_distance_m':0,'known_cost_upper_fen':0,'cost_unknown':True,'evidence_mode':'unknown'}


def result(count=2):
    return {'schema_version':'city-engine-result/2','status':'feasible','itineraries':[itinerary(count)],
            'computed_at':'2030-05-01T08:00:00+08:00'}
