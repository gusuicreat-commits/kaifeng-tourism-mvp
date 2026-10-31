"""Read-only dialogue context. Dialogue is never a source of business state or facts."""
import json
import copy
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from . import provider


def model_payload(text, snapshot, system):
    from ..providers import default_registry
    env=snapshot.envelope
    from .requirement_context import read
    view=read(snapshot)
    data_missing=view['planning_readiness']['missing_data_fields']
    saved_constraints=view['planning_constraints'].get('constraints') or {}
    awaiting_preferences=bool(saved_constraints.get('preferences_pending'))
    result=snapshot.workspace.get('result') or {}
    ctx={
        'current_date':env.identity.generated_at.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),
        # The saved planning workspace supersedes the original blank draft during requirement collection.
        **view,
        'progress':env.progress,'references':env.references,
        **({'trip_scope':{k:env.requirements[k] for k in ('schema_version','start_date','end_date','day_id','scope','days')}} if getattr(snapshot,'multi_draft',None) else {}),
        'page_focus':env.page_focus,'basket':env.basket,
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
        'planning_readiness':{
            **view['planning_readiness'],
            **({'awaiting':'preferences'} if awaiting_preferences else {}),
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
    # Work on a projection, never trim or mutate the authoritative snapshot.
    ctx=copy.deepcopy(ctx)
    payload={'text':text,'context':ctx}
    # Reserve the final count's digits so metadata cannot grow past the target.
    budget={'limit_characters':provider.MODEL_INPUT_CHARACTERS,'system_characters':len(system),
            'characters':provider.MODEL_INPUT_CHARACTERS,'trimmed':[]}
    ctx['input_budget']=budget
    def size():return len(system)+len(json.dumps(payload,ensure_ascii=False))
    def record(section):
        if section not in budget['trimmed']:budget['trimmed'].append(section)
    # Keep compatibility with the historical 12k understanding fixture while the
    # provider budget remains MODEL_INPUT_CHARACTERS. Authoritative requirements stay.
    if size()>provider.COMPACT_TARGET_CHARACTERS:
        ctx['planning_readiness'].update(assessment={},issues=[]);record('previous_assessment')
    while size()>provider.COMPACT_TARGET_CHARACTERS and ctx.get('recent_messages'):
        ctx['recent_messages'].pop(0);record('oldest_messages')
    if size()>provider.COMPACT_TARGET_CHARACTERS:
        ctx['references']=[];ctx['page_focus']=None;record('reference_details')
    if size()>provider.COMPACT_TARGET_CHARACTERS:
        ctx['official_plan']={};record('official_plan')
    if size()>provider.COMPACT_TARGET_CHARACTERS:
        ctx['progress']={};record('progress')
    while size()>provider.MODEL_INPUT_CHARACTERS and ctx['recent_messages']:
        ctx['recent_messages'].pop(0);record('oldest_messages')
    if size()>provider.MODEL_INPUT_CHARACTERS:
        ctx['planning_readiness'].update(assessment={},issues=[]);record('previous_assessment')
    if size()>provider.COMPACT_TARGET_CHARACTERS:
        # Exact duplicate values can be read from requirements. No authoritative
        # requirement (including hard limits) is discarded or shortened.
        planning=ctx['planning_constraints']
        values=planning.get('constraints')
        if values is not None:
            planning['constraints']={k:v for k,v in values.items() if k not in ctx['requirements'] or ctx['requirements'][k]!=v}
            planning['shared_values_from']='requirements';record('duplicate_planning_values')
    if size()>provider.MODEL_INPUT_CHARACTERS:
        progress=ctx['progress']
        for key in ('completed','skipped','remaining'):
            progress[key]=[{k:v for k,v in stop.items() if k not in ('progress_source','actual_start','actual_end','planned')}
                           for stop in progress.get(key,[])]
        if progress.get('next'):progress['next']={k:progress['next'][k] for k in ('stop_id','name','place_id','session_id','day_id','business_date') if k in progress['next']}
        record('progress_timing_details')
    if size()>provider.COMPACT_TARGET_CHARACTERS:
        # Provider status and field inventory are useful for ordinary turns but
        # expendable after the authoritative requirements and user text remain.
        capability=ctx.get('travel_service_capability',{})
        ctx['travel_service_capability']={k:capability[k] for k in ('scope','search_request') if k in capability}
        record('travel_service_details')
    services=ctx['travel_service_workspace']
    if size()>provider.COMPACT_TARGET_CHARACTERS and not any(services['requirements'].values()) and not services['selection']:
        ctx['travel_service_workspace']={'version':services['version']};record('empty_service_workspace')
    # Include budget metadata in its own measurement; no raw text in errors.
    for _ in range(3):budget['characters']=size()
    if size()>provider.MODEL_INPUT_CHARACTERS:
        raise HTTPException(422,detail={'code':'AGENT_CONTEXT_BUDGET','source':'context_assembly',
            'http_attempts':0,'limit_characters':provider.MODEL_INPUT_CHARACTERS,'system_characters':len(system),
            'required_characters':size(),'trimmed':budget['trimmed'],
            'message':'当前条件与输入合计超出理解预算，请分段处理；已有条件未丢弃。'})
    return payload
