import {request,mutate} from './api'
import type {CityDraft} from './cityApi'
export {isMultiDayItinerary,routeDays} from './days'
import {isMultiDayItinerary} from './days'
export type PlanningObjective='balanced'|'less_walking'|'less_waiting'|'lower_cost'
export const objectiveLabel=(objectives?:PlanningObjective[])=> objectives?.length===0?'备选':(objectives||['balanced']).map(o=>({balanced:'均衡',less_walking:'少走路',less_waiting:'少等待',lower_cost:'省钱'})[o]).join(' · ')
export interface PlanningConstraints{business_date:string|null;starts_at:string|null;ends_at:string|null;origin_place_id:string|null;destination_place_id:string|null;origin_text?:string|null;destination_text?:string|null;pace:'relaxed'|'standard'|'compact';objective:'balanced'|'less_walking'|'less_waiting'|'lower_cost';modes:('walking'|'driving'|'transit')[];budget_fen:number|null}
export interface MultiDayPlanningConstraints{schema_version:'city-constraints/2';start_date:string|null;end_date:string|null;trip_days?:number|null;trip_nights?:number|null;days:unknown[];[key:string]:unknown}
export type PlanningInput=PlanningConstraints|MultiDayPlanningConstraints
export interface PlannedActivity{goal_id:string;place_id:string;session_id:string|null;kind:string;starts_at:string;departs_at:string;duration_seconds:number;required:boolean}
export interface ScheduledLeg{departs_at:string;arrives_at:string;journey:{estimated_seconds?:number|null;planning_buffer_seconds?:number;time_basis?:string;travel:{origin_place_id:string;destination_place_id:string;mode:string;evidence?:{checked_at?:string|null;source?:string|null}}}}
export interface SingleDayItinerary{schema_version?:never;objectives:PlanningObjective[];ongoing?:{stop_id:string;place_id?:string;starts_at?:string;ends_at?:string;original_activity?:PlannedActivity}|null;legs:ScheduledLeg[];activities:PlannedActivity[];total_seconds:number;travel_seconds:number;walking_distance_m:number;known_cost_upper_fen:number;cost_unknown:boolean;returns_at:string;evidence_mode:string}
export type Itinerary=SingleDayItinerary|MultiDayItinerary
export interface Overnight{from_day_id:string;to_day_id:string;place_id:string|null;place_evidence?:string;stay_evidence?:string}
export interface RouteDay extends SingleDayItinerary{day_id:string;business_date:string;status?:'pending'|'active'|'closed';overnight?:Overnight|null;frozen?:boolean;locked?:boolean;original_requirements?:{starts_at:string;ends_at:string;origin_place_id:string|null;destination_place_id:string|null}}
export interface MultiDayItinerary{schema_version:'city-itinerary/2';start_date:string;end_date:string;days:{day_id:string;business_date:string;status:'pending'|'active'|'closed';original_requirements:NonNullable<RouteDay['original_requirements']>;itinerary:SingleDayItinerary}[];overnights?:Overnight[];active_seconds:number;elapsed_seconds:number;objectives:PlanningObjective[];travel_seconds:number;walking_distance_m:number;known_cost_upper_fen:number;cost_unknown:boolean;evidence_mode:string}
export interface RouteSnapshot{trip_id:string;version:number;route_version:number;itinerary:Itinerary|null;days?:{day_id:string;status:string;frozen:boolean;locked:boolean}[];stale?:boolean;issues?:{message:string}[];notice?:string}
export const dayLabel=(day:Pick<RouteDay,'business_date'>)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'long',day:'numeric'}).format(new Date(day.business_date+'T12:00:00+08:00'))
export interface PlanChanges{added:string[];removed:string[];reordered:string[];start_changes?:{goal_id:string;before:string;after:string}[];target_changes?:{goal_id:string;before_place:string;after_place:string;before_session:string|null;after_session:string|null}[]}
export interface PlanningResult{planning_version:number;rounds:number;status:'feasible'|'infeasible'|'unknown';reply:string;issues:{code:string;message:string;affected_ids:string[]}[];options:{id:string;kind:'priority'|'constraints';object_key?:string;priority?:'want';label:string;requires_consent:boolean}[];constraints:PlanningInput;basket_version:number;trip_version:number;goal_keys?:Record<string,string>;activity_names?:Record<string,string>;planning:null|{candidate_id:string|null;base_version:number;result:{status:string;itineraries:(Itinerary|MultiDayItinerary)[];warnings:string[];metrics:Record<string,unknown>};changes?:PlanChanges;alternative_changes?:PlanChanges[]}}
export interface EnginePreview{status:string;candidate_id:string|null;base_version:number;result:NonNullable<PlanningResult['planning']>['result'];changes?:PlanChanges;alternative_changes?:PlanChanges[];planning_version?:number;constraints?:PlanningInput;rounds?:number;basket_version?:number;trip_version?:number;reply?:string;issues?:PlanningResult['issues'];options?:PlanningResult['options']}
export type PlanningReply=PlanningResult|EnginePreview
export interface PlanningWorkspace{version:number;constraints:PlanningInput|null;result:PlanningReply|null;rounds:number}
const shortTime=(value:string)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))
export const alternativeDifference=(plan:Itinerary|MultiDayItinerary,primary?:Itinerary|MultiDayItinerary)=>{
 if(!primary)return '与方案 1 的差别待比较'
 const parts:string[]=[]
 const activities=(item:Itinerary|MultiDayItinerary)=>isMultiDayItinerary(item)?item.days.flatMap(d=>d.itinerary.activities):item.activities
 const legs=(item:Itinerary|MultiDayItinerary)=>isMultiDayItinerary(item)?item.days.flatMap(d=>d.itinerary.legs):item.legs
 const count=activities(plan).length-activities(primary).length
 if(count)parts.push(`比方案 1 ${count<0?'少':'多'} ${Math.abs(count)} 站`)
 const order=(item:Itinerary|MultiDayItinerary)=>activities(item).map(a=>`${a.goal_id}:${a.place_id}:${a.session_id||''}`).join('|')
 if(!count&&order(plan)!==order(primary))parts.push('顺序不同')
 const modes=(item:Itinerary|MultiDayItinerary)=>legs(item).map(l=>l.journey.travel.mode).join('|')
 if(modes(plan)!==modes(primary))parts.push('交通方式不同')
 const walking=Math.round(plan.walking_distance_m-primary.walking_distance_m)
 if(walking)parts.push(`${walking<0?'少':'多'}走 ${Math.abs(walking)} 米`)
 const planReturn=isMultiDayItinerary(plan)?plan.days.at(-1)?.itinerary.returns_at:plan.returns_at
 const primaryReturn=isMultiDayItinerary(primary)?primary.days.at(-1)?.itinerary.returns_at:primary.returns_at
 if(planReturn!==primaryReturn&&planReturn)parts.push(`${shortTime(planReturn)} 返回`)
 return parts.slice(0,2).join(' · ')||'交通路径不同'
}
const prefix=(id:string)=>'/city/v1/trips/'+encodeURIComponent(id)
export const explorationApi={
 constraints:(id:string)=>request<PlanningWorkspace>(prefix(id)+'/exploration/constraints'),
 preview:(id:string,tripVersion:number,basketVersion:number,constraints:PlanningInput,planningVersion:number)=>mutate<PlanningReply>(prefix(id)+'/exploration/preview',{expected_version:tripVersion,basket_version:basketVersion,constraints,planning_version:planningVersion}),
 answer:(id:string,tripVersion:number,basketVersion:number,planningVersion:number,reply:{option_id?:string;text?:string})=>mutate<PlanningWorkspace>(prefix(id)+'/exploration/answer',{expected_version:tripVersion,basket_version:basketVersion,planning_version:planningVersion,...reply}),
 cancel:(id:string,tripVersion:number,basketVersion:number,planningVersion:number)=>mutate<PlanningWorkspace>(prefix(id)+'/exploration/cancel',{expected_version:tripVersion,basket_version:basketVersion,planning_version:planningVersion}),
 confirm:(id:string,version:number,candidate_id:string,alternative_index:number)=>mutate(prefix(id)+'/engine/confirm',{expected_version:version,candidate_id,alternative_index}),
 current:(id:string)=>request<RouteSnapshot>(prefix(id)+'/engine/current')
}
