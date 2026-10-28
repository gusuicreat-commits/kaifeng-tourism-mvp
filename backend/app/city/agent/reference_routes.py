"""Read-only AMap reference routes. Never an OR-Tools input or formal route write."""
import os
import re
from datetime import datetime
from types import SimpleNamespace

from fastapi import HTTPException
from .. import location_resolution as locations
from .. import facts
from ...db import connect
from ..engine import amap
from ..schema import TZ
from .models import ReferenceRouteIntent
from . import entrance_navigation as navigation


def explicit(text):
    match=re.fullmatch(r'\s*(?:请问[，,]?\s*)?(?:从)?(.{1,80}?)\s*(?:到|去|→|->)\s*(.{1,80}?)\s*(?:怎么走|怎么去|如何走|如何去|怎样走|路线怎么走)[？?。！!]?\s*',text)
    if not match:return None
    origin,destination=(x.strip() for x in match.groups())
    mode='walking'
    for token,value in (('坐公交','transit'),('乘公交','transit'),('公交','transit'),('开车','driving'),('驾车','driving'),('步行','walking'),('走路','walking')):
        if destination.endswith(token):destination=destination[:-len(token)].strip();mode=value;break
    if not origin or not destination or any(w in origin+destination for w in ('安排','一日游','取消','不要','不是','或者','还是')):return None
    current=origin in ('这里','这儿','我这里','我这儿','当前位置','我的位置','我现在的位置')
    return ReferenceRouteIntent(quote=match[0].strip(),origin=None if current else origin,
                               destination=destination,use_current_position=current,mode=mode)


def grounded(intent,text):
    if intent.quote not in text or intent.destination not in intent.quote:raise ValueError('ungrounded route destination')
    if intent.origin and intent.origin not in intent.quote:raise ValueError('ungrounded route origin')
    if intent.use_current_position and not re.search(r'从(?:我)?(?:这里|这儿|当前位置|我的位置|我现在的位置)',intent.quote):raise ValueError('unrequested visitor position')
    return intent


def requested_mode(intent):
    """A mode the visitor did not ask for falls back to the walking default; the route itself stays."""
    if intent.mode=='driving' and not any(w in intent.quote for w in ('开车','驾车','自驾')) or \
       intent.mode=='transit' and not any(w in intent.quote for w in ('公交','公共交通','巴士')):
        return intent.model_copy(update={'mode':'walking'})
    return intent


def enabled():
    return os.getenv('AMAP_REFERENCE_ROUTES_ENABLED','').lower() in ('1','true','yes')


def capabilities():
    from ... import language,model_schedule
    from urllib.parse import urlsplit
    result={'model':{'available':language.available(),'model':os.getenv('TOURISM_LLM_MODEL') or None,
                     'endpoint_host':urlsplit(os.getenv('TOURISM_LLM_BASE_URL','')).hostname,
                     'code':None if language.available() else 'MODEL_NOT_CONFIGURED',
                     'scheduler':model_schedule.scheduler().describe()},
            'poi_search_enabled':os.getenv('AMAP_LOCATION_SEARCH_ENABLED','').lower() in ('1','true','yes'),
            'poi_terms_gate':os.getenv('AMAP_TERMS_REVIEWED','').lower() in ('1','true','yes'),
            'web_service_key_present':bool(os.getenv('AMAP_WEB_SERVICE_KEY','').strip()),
            'reference_routes_enabled':enabled(),'formal_amap_traffic_provider':'not_connected',
            'reference_tools':['search_locations','query_reference_route'],
            'travel_services':{'flyai_configured':bool(os.getenv('FLYAI_API_KEY','').strip()),
                               'flyai_enabled':os.getenv('FLYAI_ENABLED','').lower() in ('1','true','yes'),
                               'hotel_details':'external_link_only','room_attributes':'unknown',
                               'reviews':'not_available','selection_scope':'trip_candidate_intent',
                               'formal_planning_admission':'needs_verified_facts'},
            'reference_requires_formal_facts':False,'explicit_origin_requires_visitor_position':False}
    from .. import weather
    result['weather']=weather.capabilities()
    from ..providers import default_registry
    registry=default_registry()
    result['provider_registry']={'capabilities':registry.capabilities(),
                                 'statuses':registry.statuses(),
                                 'selection':'priority_then_fallback_within_shared_deadline',
                                 'secrets':'server_environment_only'}
    return result


def locate(session_id,intent,snapshot):
    found={};choices=[];diagnostics=[]
    if intent.use_current_position:
        pos=snapshot.envelope.position
        place=snapshot.planning.facts.get(('place',pos.get('place_id')))
        if pos.get('status')!='fresh' or not place or place.coordinate_system!='GCJ02' or place.longitude is None:
            return {'status':'needs_position','state':'current_position_required','code':'CURRENT_ORIGIN_REQUIRED',
                    'provider_http_attempts':0,'reply':'这次起点是“当前位置”，请告诉我出发地点；也可主动选择定位。终点已保留。'}
        found['current_origin']={'name':place.name,'longitude':place.longitude,'latitude':place.latitude}
    if not enabled():
        return {'status':'unavailable','state':'locations_explicit','code':'REFERENCE_ROUTES_DISABLED',
                'provider_http_attempts':0,'reply':'起点和终点已经明确，参考路线查询尚未启用；无需提供你的当前位置。'}
    for role,query in (('origin',intent.origin),('destination',intent.destination)):
        if role=='origin' and intent.use_current_position:continue
        result=locations.search(session_id,query)
        diagnostics.append({'role':role,'provider_status':result['provider_status'],
                            **result.get('provider_diagnostics',{'http_attempts':0,'events':[]})})
        if result['status']!='candidates':
            code='POI_SEARCH_DISABLED' if result['provider_status']=='disabled' else 'POI_LOOKUP_FAILED'
            return {'status':'unavailable','state':'locations_explicit','code':code,
                    'provider_status':result['provider_status'],'search_diagnostics':diagnostics,
                    'provider_http_attempts':sum(d['http_attempts'] for d in diagnostics),'reply':'地点查询暂不可用，请稍后重试；这次问路不依赖你的当前位置。'}
        exact=[c for c in result['candidates'] if c['name']==query]
        selected=exact[0]['candidate_id'] if len(exact)==1 else None
        candidates=[{k:c[k] for k in ('candidate_id','name','address','entrance_status')} for c in result['candidates']]
        value={'role':role,'query':query,'resolution_id':result['resolution_id'],
               'selected_candidate_id':selected,'candidates':candidates,
               'selection_basis':'unique_exact_name_for_reference_only' if selected else 'user_selection_required'}
        found[role]=value
        if selected is None:choices.append(value)
    return {'status':'ambiguous' if choices else 'resolved','state':'ambiguous_locations' if choices else 'locations_explicit',
            'locations':found,'location_choices':choices,'search_diagnostics':diagnostics,
            'provider_http_attempts':sum(d['http_attempts'] for d in diagnostics),
            'reply':'找到多个可能地点，请选择你所指的地点。' if choices else '已明确起点和终点，正在查询高德参考路线。'}


def remaining_choices(found):
    return [v for role,v in found.items() if role in ('origin','destination') and not v.get('selected_candidate_id')]


def query(session_id,intent,found):
    if not enabled():return {'status':'unavailable','state':'locations_explicit','code':'REFERENCE_ROUTES_DISABLED','reply':'参考路线查询暂未启用。'}
    def point(role):
        if role=='origin' and intent.use_current_position:
            p=found['current_origin'];return SimpleNamespace(**p,coordinate_system='GCJ02'),p['name']
        value=found[role]
        _,p=locations.STORE.confirm(session_id,value['resolution_id'],value['selected_candidate_id'])
        return amap.Poi(p.poi_id,p.name,p.longitude,p.latitude,citycode=p.citycode),p.name
    origin,oname=point('origin');destination,dname=point('destination')
    provider=locations.provider_from_environment()
    moment=datetime.now(TZ)
    entrance=None;notice=''
    try:
        with connect() as db:catalog=facts.records(db)
        scenic=navigation.scenic_for(intent.destination,catalog) or navigation.scenic_for(dname,catalog)
        if scenic:
            items=navigation.entrances(scenic,catalog)
            located=navigation.locate_entrances(scenic,items,provider,destination)
            gate,straight_m=navigation.nearest(origin,located)
            if gate:
                others=[i['name'] for i in items if i['name']!=gate['name']]
                entrance={k:gate[k] for k in ('name','basis','poi_name')}
                entrance.update(straight_m=round(straight_m),others=others)
                basis='按资料库位置' if gate['basis']=='catalog' else f'按高德“{gate["poi_name"]}”定位'
                notice=f'{scenic.name}有{len(items)}个入口资料，离起点最近的是{gate["name"]}（{basis}，直线约{round(straight_m)}米），已按它查参考路线。'
                if others:notice+='其他入口：'+'、'.join(others)+'。'
                dname=scenic.name+'·'+gate['name']
                destination=amap.Poi(gate['entrance_id'],dname,gate['longitude'],gate['latitude'],citycode=destination.citycode)
            elif items:
                notice=f'{scenic.name}有入口资料（'+ '、'.join(i['name'] for i in items)+'），但都还没有位置，终点按景区位置；到达后请按现场指引前往入口。'
            else:notice=f'暂无{scenic.name}的入口资料，终点按高德地点位置。'
        quote=provider.estimate(origin,destination,intent.mode,moment,moment,citycode=getattr(origin,'citycode',None) or getattr(destination,'citycode',None))
        events=[dict(e) for e in provider.events]
    finally:provider.close()
    if quote.status!='estimated':
        return {'status':'unavailable','state':'locations_explicit','code':'AMAP_REFERENCE_QUERY_FAILED',
                'provider_status':quote.status,'provider_events':events,'reply':'两端地点已明确，但高德参考路线暂未查得，请稍后重试；无需补充GPS或正式行程资料。'}
    steps=[]
    if quote.paths:
        path=quote.paths[0]
        raw=path.get('steps',[]) if isinstance(path,dict) else []
        if intent.mode=='transit' and isinstance(path,dict):
            raw=[]
            for seg in path.get('segments',[])[:12]:
                raw.extend(seg.get('walking',{}).get('steps',[]))
                for line in seg.get('bus',{}).get('buslines',[])[:1]:
                    if isinstance(line.get('name'),str):raw.append({'instruction':'乘坐'+line['name']})
        for step in raw[:12]:
            instruction=step.get('instruction') if isinstance(step,dict) else None
            if isinstance(instruction,str) and instruction.strip():steps.append(instruction.strip()[:240])
    mode_label={'walking':'步行','driving':'驾车','transit':'公交'}[intent.mode]
    return {'status':'answered','state':'locations_explicit','reply':notice+f'{oname} → {dname}，已查得{mode_label}参考路线。以下为高德当前估计，出入口尚未核验，不是已确认行程。',
            'history_reply':f'已查询从{oname}到{dname}的{mode_label}高德参考路线；估时与路段仅本次显示，可重新查询。',
            'reference_route':{'source':'AMap Web Service','evidence_status':'provider_estimate','origin':oname,'destination':dname,
                'mode':intent.mode,'mode_basis':'walking_default_for_reference' if intent.mode=='walking' else 'explicit_user_mode',
                'duration_seconds':quote.duration_seconds,'distance_m':quote.distance_m,'steps':steps,'queried_at':quote.queried_at,
                'coordinate_system':'GCJ02','entrances_verified':False,'navigation_certified':False,
                'destination_entrance':entrance,'destination_point':[destination.longitude,destination.latitude],
                'formal_admission':'needs_data','formal_state_changed':False,'persisted_as_itinerary':False},
            'provider_status':quote.status,'provider_events':events}
