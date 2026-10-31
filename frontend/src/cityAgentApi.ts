import {interactionModes,readMode,type InteractionMode} from './interactionMode'
import {AGENT_STEP_TIMEOUT_MS,mutate,request} from './api'
import type {ObjectRef,BasketChoice} from './basketApi'
import type {Itinerary,PlanningReply} from './explorationApi'

export interface CityContext {
 identity:{trip_id:string;version:number;route_version:number;status:string}
 progress:{next:{name:string}|null}
 official_plan:{status:string}
 recent_messages?:{role:string;text:string}[]
}
export interface CandidateChange {
 days?:({day_id:string;business_date:string}&CandidateChange)[]
 retained:string[];removed:string[];added:string[];reordered:string[]
 start_changes:{goal_id:string;before:string;after:string}[]
 walking_distance_m_delta:number;travel_seconds_delta:number
}
export interface ReferenceRoute {
 source:string;evidence_status:string;origin:string;destination:string;mode:'walking'|'driving'|'transit'
 duration_seconds:number;distance_m:number;steps:string[];queried_at:string
 entrances_verified:false;navigation_certified:false;formal_admission:'needs_data'
 destination_entrance?:{name:string;basis:'catalog'|'amap_poi_proxy';poi_name:string|null;straight_m:number;others:string[]}|null
 destination_point?:[number,number]
 taxi_cost_fen?:number|null;tolls_fen?:number|null;parking?:{name:string;straight_m:number}[]
}
export interface RouteLocationChoice {
 role:'origin'|'destination';query:string;resolution_id:string
 candidates:{candidate_id:string;name:string;address:string|null}[]
}
export interface BasketProposal {
 basket_version:number
 items:(ObjectRef&{operation:'put'|'standby';name:string;priority:NonNullable<BasketChoice['priority']>;quote:string})[]
}
export interface BasketChoicePrompt {
 query:string;quote:string;priority:'must'|'want'|'optional'
 operation?:'put'|'standby';priority_stated?:boolean
 candidates:(ObjectRef&{name:string;address:string|null})[]
}
export interface WeatherReport {
 location_name:string;source:'QWeather';evidence_status:string;queried_at:string;attributions:string[]
 current:null|{condition:string;temperature_c:number;feels_like_c:number;precipitation_mm:number;precipitation_probability_percent:number|null;wind_direction:string;wind_speed_mps:number}
 hourly:{hours:{starts_at:string;condition:string;temperature_c:number;precipitation_mm:number;precipitation_probability_percent:number}[]}
 daily:{days:{date:string;temperature_min_c:number;temperature_max_c:number}[]}
 impacts:{activity_id:string;name:string;starts_at:string;departs_at:string;basis:string;conditions:string[];max_precipitation_probability_percent:number;precipitation_mm:number}[]
 coverage_unknown:{activity_id:string;name:string;reason:string}[]
 adjustment_offered:boolean;trip_version:number;route_version:number;formal_state_changed:false
}
export interface AgentTask {
 task_id:string;trip_id:string;step:number;status:string;mode:string|null;busy:boolean;progress:string|null
 waiting?:{retry_after_seconds:number}|null
 events:{tool:string;status:string}[]
 understanding:{constraints:{field:string;strength:string;value:string|number|null;quote:string}[]}|null
 result:{reply:string;interaction_mode?:InteractionMode;interaction_mode_changed?:boolean;state?:'locations_explicit'|'ambiguous_locations'|'current_position_required'|'model_unavailable';code?:string;reference_route?:ReferenceRoute;weather?:WeatherReport;location_choices?:RouteLocationChoice[];basket_proposal?:BasketProposal;basket_choices?:BasketChoicePrompt[];planning_workflow?:{trip_id:string;execution:'completed_backend';operation:string;date_changed:boolean;result:PlanningReply};questions?:string[];answers?:{status:string;reply:string}[];compound?:{partial:boolean;items:{kind:string;status:string;reply?:string;questions?:string[];result?:unknown;answers?:{status:string;reply:string}[]}[]};activity_names?:Record<string,string>;changes?:CandidateChange;alternative_changes?:CandidateChange[];confirmation?:{target_kind:'route'|'state';candidate_id:string;expected_version:number};
  planning?:{result:{itineraries:Itinerary[];warnings?:string[]}}}|null
}
const prefix=(id:string)=>`/city/v1/trips/${encodeURIComponent(id)}`
export const cityAgentApi={
 messages:(id:string,before?:number)=>request<{messages:{id:string;role:string;text:string}[];before:number|null}>(prefix(id)+'/assistant/messages'+(before?'?before='+before:'')),
 context:(id:string)=>request<CityContext>(prefix(id)+'/assistant/context?purpose=trip'),
 weather:(id:string)=>request<{status:string;reply:string;weather:WeatherReport|null}>(prefix(id)+'/weather',{},30000),
 start:(ctx:CityContext,text:string,page_focus?:ObjectRef|null,interaction_mode:InteractionMode=interactionModes.value[ctx.identity.trip_id]??readMode(ctx.identity.trip_id)??'deep')=>mutate<AgentTask>(prefix(ctx.identity.trip_id)+'/agent/tasks',{
  text,interaction_mode,expected_version:ctx.identity.version,expected_route_version:ctx.identity.route_version,...(page_focus?{page_focus}:{})}),
 selectLocation:(t:AgentTask,choice:RouteLocationChoice,candidate_id:string)=>mutate<AgentTask>(prefix(t.trip_id)+`/agent/tasks/${t.task_id}/locations/confirm`,{expected_step:t.step,role:choice.role,resolution_id:choice.resolution_id,candidate_id}),
 step:(t:AgentTask)=>mutate<AgentTask>(prefix(t.trip_id)+`/agent/tasks/${t.task_id}/step`,{expected_step:t.step},'POST',AGENT_STEP_TIMEOUT_MS),
 get:(t:AgentTask)=>request<AgentTask>(prefix(t.trip_id)+`/agent/tasks/${t.task_id}`),
 cancel:(t:AgentTask)=>request<AgentTask>(prefix(t.trip_id)+`/agent/tasks/${t.task_id}/cancel`,{method:'POST'}),
 confirm:(t:AgentTask,alternative_index=0)=>mutate(prefix(t.trip_id)+'/assistant/confirm',{...t.result?.confirmation,confirmed:true,alternative_index}),
}
