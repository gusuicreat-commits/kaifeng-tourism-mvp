"""Read-only dialogue context. Dialogue is never a source of business state or facts."""
import json
from zoneinfo import ZoneInfo
from fastapi import HTTPException


def model_payload(text, snapshot, system):
    from ..providers import default_registry
    env=snapshot.envelope
    constraints=snapshot.workspace['constraints'] or env.requirements
    fields=('business_date','starts_at','ends_at','origin_place_id','destination_place_id')
    missing=[key for key in fields if constraints.get(key) is None]
    places=[p for (kind,_),p in snapshot.planning.facts.items()
            if kind=='place' and p.rights_status=='permitted']
    endpoints=('origin_place_id','destination_place_id')
    data_missing=[key for key in missing if key in endpoints and (not places or constraints.get(key.replace('_place_id','_text')))]
    user_missing=[key.replace('_place_id','_text') if key in endpoints and not places else key for key in missing
                  if key not in endpoints or not constraints.get(key.replace('_place_id','_text'))]
    result=snapshot.workspace.get('result') or {}
    saved_services=snapshot.services.get('requirements',{})
    service_fields=('category','check_in','check_out','nights','guests','rooms','max_price','stay_budget_fen','room_type','location_text')
    service_requirements={k:saved_services[k] for k in service_fields if k in saved_services}
    service_requirements['attributes']={k:{f:v.get(f) for f in ('value','strength')}
        for k,v in list(saved_services.get('attributes',{}).items())[:12]}
    ctx={
        'current_date':env.identity.generated_at.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),
        # The saved planning workspace supersedes the original blank draft during requirement collection.
        'requirements':env.requirements | (snapshot.workspace['constraints'] or {}),
        'progress':env.progress,'references':env.references,
        'page_focus':env.page_focus,'basket':env.basket,'planning_constraints':env.planning_constraints,
        'recent_messages':[{'role':m['role'],'text':m['text']} for m in env.recent_messages] if snapshot.history_enabled else [],
        'official_plan':{k:env.official_plan[k] for k in ('status','route_version','stale_reasons')},
        'reference_route_capability':{'scope':'named_origin_to_named_destination_reference_only','explicit_origin_requires_visitor_position':False,'requires_formal_itinerary_data':False,'entry_points_verified':False},
        'travel_service_capability':{
            'provider_statuses':default_registry().statuses(),
            'hotel_search_fields':['name','address','provider_display_starting_price'],
            'hotel_unknown_fields':['room_type','window','breakfast','reviews','selected_rate_plan','occupancy_total_price'],
            'scope':'query_candidates_only_not_booking_or_formal_route',
            'capability_question':'answer_available_fields_first_without_running_search',
            'search_request':'search_services_after_semantic_interpretation',
        },
        'travel_service_workspace':{
            'version':snapshot.services.get('version',0),
            'requirements':service_requirements,
            'status':(snapshot.services.get('result') or {}).get('status'),
            'missing_fields':(snapshot.services.get('result') or {}).get('questions',[]),
            'scope':'service_conditions_not_formal_itinerary_requirements',
        },
        'planning_readiness':{
            'scope':'formal_itinerary_only_not_reference_route',
            'missing_user_fields':user_missing,
            'missing_data_fields':data_missing,
            'place_catalog_available':bool(places),
            'blocked_by_missing_place_data':bool(data_missing),
            # Raw generic preview messages can incorrectly suggest asking the user to fix missing catalog data.
            'next_step':('可以先询问missing_user_fields中尚未提供的出发和返回地点名称，保存为未核验意向；已有名称不重复问。正式行程的位置资料仍需核验，暂不能生成正式行程；独立高德参考问路可按明确起终点查询，不受本项阻断。'
                         if data_missing else '只补问missing_user_fields中尚未提供的内容；已有条件不重复问。'),
            'assessment':{k:result[k] for k in ('status','reply') if k in result} if not data_missing else {},
            'issues':[{'code':v.get('code'),'message':str(v.get('message',''))[:200]} for v in result.get('issues',[])[:8]]
                      if not data_missing else [],
        },
        # Labels express the visitor's choices; they do not establish merchant/schedule facts.
        'material_labels':[{'name':m['name'][:120],'priority':m['priority'],'status':m['status']}
                           for m in snapshot.materials[:12]],
    }
    payload={'text':text,'context':ctx}
    while len(system)+len(json.dumps(payload,ensure_ascii=False))>12000:
        if ctx['recent_messages']:ctx['recent_messages'].pop(0)
        else:raise HTTPException(422,detail='当前行程信息过多，请缩小问题范围。')
    return payload
