"""Project a trip's confirmed hotel intention onto adjacent daily endpoints.

Selections never write formal routes. Only maintenance bindings to existing
city facts supply endpoint IDs; provider names and starting prices cannot.
"""
import copy
import json
from datetime import date, datetime, timedelta

from fastapi import HTTPException
from pydantic import AwareDatetime, Field

from . import facts
from .schema import EntityId, Strict, TZ
from ..memory.storage import digest, encoded


class LocationBinding(Strict):
    candidate_id: str = Field(pattern=r'^flyai:.+', max_length=120)
    place_id: EntityId
    revision: int = Field(ge=1)
    source: str = Field(min_length=1, max_length=500)
    checked_at: AwareDatetime
    valid_from: AwareDatetime
    valid_until: AwareDatetime


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS city_hotel_binding_metadata (
        candidate_id TEXT PRIMARY KEY, data_json TEXT NOT NULL)''')


def import_binding(db, binding):
    """Maintenance-only entry point; no visitor HTTP route calls this function."""
    binding=LocationBinding.model_validate(binding)
    if not binding.checked_at < binding.valid_until or binding.valid_until <= binding.valid_from:
        raise ValueError('酒店地点绑定有效期无效')
    facts.require(facts.records(db),'place',binding.place_id)
    old=db.execute('SELECT data_json FROM city_hotel_binding_metadata WHERE candidate_id=?',
                   (binding.candidate_id,)).fetchone()
    if old:
        previous=LocationBinding.model_validate_json(old[0])
        if previous==binding:return
        if binding.revision<=previous.revision:raise ValueError('酒店地点绑定修订号必须递增')
    db.execute("INSERT OR REPLACE INTO city_object_bindings VALUES ('flyai_hotel',?,'place',?)",
               (binding.candidate_id,binding.place_id))
    db.execute('INSERT OR REPLACE INTO city_hotel_binding_metadata VALUES (?,?)',
               (binding.candidate_id,binding.model_dump_json()))


def location(db, selected):
    if not selected:return None
    row=db.execute('''SELECT m.data_json,b.fact_id FROM city_hotel_binding_metadata m
        JOIN city_object_bindings b ON b.kind='flyai_hotel' AND b.object_id=m.candidate_id
        AND b.fact_kind='place' WHERE m.candidate_id=?''',(selected['id'],)).fetchone()
    if not row:return None
    binding=LocationBinding.model_validate_json(row['data_json'])
    return binding if binding.place_id==row['fact_id'] else None


def baseline(db, workspace):
    selected=(workspace.get('selection') or {}).get('hotels')
    mapping=location(db,selected)
    return {'service_version':workspace['version'],
            'hotel_binding':digest(encoded(mapping.model_dump(mode='json'))) if mapping else None}


def guard(db, trip_id, expected):
    from .travel_services import read
    if baseline(db,read(db,trip_id))!=expected:
        raise HTTPException(409,detail={'code':'HOTEL_BINDING_CHANGED',
            'message':'酒店选择、服务条件或酒店地点绑定已变化，请重新预览整套行程。'})


def project(db, values, workspace, *, moment=None):
    """Return independent planning values and blockers; preserve the first/last endpoints."""
    values=copy.deepcopy(values)
    if values.get('schema_version') not in ('city-constraints/2','city-engine/2'):
        return values,[]
    days=values.get('days') or []
    if len(days)<2:return values,[]
    selected=(workspace.get('selection') or {}).get('hotels')
    incoming=[o for o in values.get('overnights',[]) if o.get('source_kind')=='selected_hotel']
    if not selected:
        if incoming:
            if values['schema_version']=='city-constraints/2':
                removed={(o['from_day_id'],o['to_day_id']) for o in incoming}
                for a,b in zip(days,days[1:]):
                    if ('day:'+a['business_date'],'day:'+b['business_date']) in removed:
                        a['destination_place_id']=b['origin_place_id']=None
                        a['destination_text']=b['origin_text']=None
                values['overnights']=[o for o in values['overnights'] if o['source_kind']!='selected_hotel']
                return values,[]
            raise HTTPException(503,detail={'status':'unavailable','code':'MULTI_DAY_UNAVAILABLE',
                'message':'酒店过夜引用没有当前已确认的酒店选择，请重新选择酒店或手选已有过夜地点。'})
        return values,[]
    stay=selected.get('stay')
    # Legacy candidate intentions did not snapshot their stay dates. A later
    # search must not silently change the dates of that old choice.
    if not stay:
        return values,[issue('HOTEL_STAY_PENDING','请重新确认酒店候选，以保存本次入住和退房日期。')]
    start=stay.get('check_in');end=stay.get('check_out')
    if start and not end and stay.get('nights'):
        end=(date.fromisoformat(start)+timedelta(days=stay['nights'])).isoformat()
    if not start or not end or start>days[0]['business_date'] or end<days[-1]['business_date']:
        return values,[issue('HOTEL_STAY_RANGE_MISMATCH',
            f"酒店入住 {start or '待补'}、退房 {end or '待补'}，未覆盖行程全部过夜日期，请核对住宿区间。")]
    binding=location(db,selected);moment=moment or datetime.now(TZ)
    old={o['night_date']:o for o in values.get('overnights',[])}
    nights=[];issues=[]
    for a,b in zip(days,days[1:]):
        ready=False
        if binding and binding.checked_at<=moment:
            if a.get('ends_at') and b.get('starts_at'):
                ready=(binding.valid_from<=datetime.fromisoformat(a['ends_at']) and
                       datetime.fromisoformat(b['starts_at'])<binding.valid_until)
            else:
                ready=(binding.valid_from.astimezone(TZ).date()<=date.fromisoformat(a['business_date']) and
                       date.fromisoformat(b['business_date'])<binding.valid_until.astimezone(TZ).date())
        place_id=binding.place_id if ready else None
        if not place_id:
            # Degrade, never block: without a mapped place this night keeps its
            # original endpoints and overnight, and the visitor is told why.
            if old.get(a['business_date']) and old[a['business_date']].get('source_kind')!='selected_hotel':
                nights.append(old[a['business_date']])
            issues.append(issue('HOTEL_LOCATION_PENDING',
                a['business_date']+'已选择'+selected['name']+'，酒店位置尚未关联城市地点，这一晚暂按原起终点安排；入住条件仍待核实。',
                'day:'+a['business_date'],'day:'+b['business_date']))
            continue
        a['destination_place_id']=b['origin_place_id']=place_id
        a['destination_text']=b['origin_text']=None
        night={'overnight_id':old.get(a['business_date'],{}).get('overnight_id','night:'+a['business_date']),
            'from_day_id':'day:'+a['business_date'],'to_day_id':'day:'+b['business_date'],
            'night_date':a['business_date'],'place_id':place_id,'source_kind':'selected_hotel',
            'service_selection_id':selected['id'],'service_version':selected.get('selection_version',workspace['version']),
            'binding_revision':binding.revision if binding else None,
            # Search dates establish nights, not actual reception/checkout times.
            # Requested stay dates remain in selection.stay; do not invent 00:00.
            'check_in':None,'check_out':None,
            'place_evidence':'unknown','stay_evidence':'unknown','evidence_refs':[]}
        nights.append(night)
    values['overnights']=nights
    # Solver days have no display-name fields. Constraint days retain them so
    # missing hotel location is never misreported as a missing hotel choice.
    if values['schema_version']=='city-engine/2':
        for d in days:
            d.pop('origin_text',None);d.pop('destination_text',None)
    return values,issues


def blocking(issues):
    """Issues that stop planning; an unmapped hotel location only degrades to a note."""
    return [i for i in issues if i['code']!='HOTEL_LOCATION_PENDING']


def issue(code,message,*ids):
    return {'code':code,'message':message,'affected_ids':list(ids)}


def route_binding(values):
    return {'days':[(d['business_date'],d.get('origin_place_id'),d.get('destination_place_id')) for d in values['days']],
        'overnights':[{k:v for k,v in o.items() if k not in ('place_evidence','stay_evidence','evidence_refs')}
                      for o in values.get('overnights',[])]}


def describe(db,row,workspace,candidate=None):
    """A concise reply for the existing service card; no new UI is required."""
    from . import exploration_state
    raw=exploration_state.read(db,row['id']).get('constraints')
    if not raw or raw.get('schema_version')!='city-constraints/2':
        draft=json.loads(row['draft_json'])
        if draft.get('schema_version')!='city-draft/2':return None
        raw=draft|{'schema_version':'city-constraints/2'}
    if len(raw.get('days',[]))<2:return None
    w=copy.deepcopy(workspace)
    if candidate:
        candidate=dict(candidate,stay={k:workspace['requirements'].get(k) for k in ('check_in','check_out','nights')})
        w['selection']=(w.get('selection') or {})|{'hotels':candidate}
    values,issues=project(db,raw,w)
    if blocking(issues):return {'status':'needs_data','issues':issues,'overnights':values.get('overnights',[]),'message':blocking(issues)[0]['message']}
    if issues:return {'status':'location_pending','issues':issues,'overnights':values.get('overnights',[]),'message':issues[0]['message']}
    hotel=(w.get('selection') or {}).get('hotels')
    if not hotel:return None
    return {'status':'bound_for_preview','overnights':values['overnights'],
        'message':'已选择'+hotel['name']+'，下一次整套规划将自动作为前一天终点和第二天起点。请重新预览并确认行程；入住条件仍待核实。'}
