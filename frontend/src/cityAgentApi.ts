import {mutate,request} from './api'
import type {ObjectRef} from './basketApi'
import type {PlanningConstraints} from './explorationApi'

export interface PlanningAction {
 trip_id:string;operation:'update'|'continue';constraints:PlanningConstraints;planning_version:number;basket_version:number;trip_version:number
 date_changed:boolean;choice_text:string|null
}

export interface CityContext {
 identity:{trip_id:string;version:number;route_version:number;status:string}
 progress:{next:{name:string}|null}
 official_plan:{status:string}
 recent_messages?:{role:string;text:string}[]
}
export interface CandidateChange {
 retained:string[];removed:string[];added:string[];reordered:string[]
 start_changes:{goal_id:string;before:string;after:string}[]
 walking_distance_m_delta:number;travel_seconds_delta:number
}
export interface ReferenceRoute {
 source:string;evidence_status:string;origin:string;destination:string;mode:'walking'|'driving'|'transit'
 duration_seconds:number;distance_m:number;steps:string[];queried_at:string
 entrances_verified:false;navigation_certified:false;formal_admission:'needs_data'
}
export interface RouteLocationChoice {
 role:'origin'|'destination';query:string;resolution_id:string
 candidates:{candidate_id:string;name:string;address:string|null}[]
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
 result:{reply:string;state?:'locations_explicit'|'ambiguous_locations'|'current_position_required'|'model_unavailable';code?:string;reference_route?:ReferenceRoute;weather?:WeatherReport;location_choices?:RouteLocationChoice[];planning_action?:PlanningAction;questions?:string[];answers?:{status:string;reply:string}[];compound?:{partial:boolean;items:{kind:string;status:string;reply?:string;questions?:string[];result?:unknown;answers?:{status:string;reply:string}[]}[]};activity_names?:Record<string,string>;changes?:CandidateChange;confirmation?:{target_kind:'route'|'state';candidate_id:string;expected_version:number};
  planning?:{result:{itineraries:{activities:{goal_id:string;place_id:string;session_id:string|null;starts_at:string;departs_at:string}[];walking_distance_m:number;travel_seconds:number}[]}}}|null
}
const prefix=(id:string)=>`/city/v1/trips/${encodeURIComponent(id)}`
export const cityAgentApi={
 messages:(id:string,before?:number)=>request<{messages:{id:string;role:string;text:string}[];before:number|null}>(prefix(id)+'/assistant/messages'+(before?'?before='+before:'')),
 context:(id:string)=>request<CityContext>(prefix(id)+'/assistant/context?purpose=trip'),
 weather:(id:string)=>request<{status:string;reply:string;weather:WeatherReport|null}>(prefix(id)+'/weather',{},30000),
 start:(ctx:CityContext,text:string,page_focus?:ObjectRef|null)=>mutate<AgentTask>(prefix(ctx.identity.trip_id)+'/agent/tasks',{
  text,expected_version:ctx.identity.version,expected_route_version:ctx.identity.route_version,...(page_focus?{page_focus}:{})}),
 selectLocation:(t:AgentTask,choice:RouteLocationChoice,candidate_id:string)=>mutate<AgentTask>(prefix(t.trip_id)+`/agent/tasks/${t.task_id}/locations/confirm`,{expected_step:t.step,role:choice.role,resolution_id:choice.resolution_id,candidate_id}),
 step:(t:AgentTask)=>mutate<AgentTask>(prefix(t.trip_id)+`/agent/tasks/${t.task_id}/step`,{expected_step:t.step},'POST',16000),
 get:(t:AgentTask)=>request<AgentTask>(prefix(t.trip_id)+`/agent/tasks/${t.task_id}`),
 cancel:(t:AgentTask)=>request<AgentTask>(prefix(t.trip_id)+`/agent/tasks/${t.task_id}/cancel`,{method:'POST'}),
 confirm:(t:AgentTask)=>mutate(prefix(t.trip_id)+'/assistant/confirm',{...t.result?.confirmation,confirmed:true}),
}
