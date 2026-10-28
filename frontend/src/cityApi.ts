import {mutate,request} from './api'
import type {Overnight} from './explorationApi'
export {isMultiDayDraft,draftDays} from './days'
import {isMultiDayDraft} from './days'

export interface CityStop {
 stop_id:string;place_id:string;kind:'visit'|'show'|'dining'|'rest'|'return';session_id:string|null;scenic_id?:string|null
 locked:boolean;duration_seconds:number|null;progress:'pending'|'completed'|'skipped'
 actual_start:string|null;actual_end:string|null;window_start?:string|null;window_end?:string|null;evidence_refs?:string[]
}
export interface SingleDayDraft {
 schema_version?:never;
 pace?:'relaxed'|'standard'|'compact';objective?:'balanced'|'less_walking'|'less_waiting'|'lower_cost'
 business_date:string|null;starts_at:string|null;ends_at:string|null;origin_place_id:string|null;destination_place_id:string|null
 interests:('show'|'photo'|'food'|'culture')[];dish_interest_ids:string[];modes:('walking'|'driving'|'transit')[];stops:CityStop[]
}
export interface CityDraftDay extends SingleDayDraft {
 day_id:string;business_date:string;status:'pending'|'active'|'closed'
}
export interface MultiDayDraft {
 schema_version:'city-draft/2';start_date:string;end_date:string;days:CityDraftDay[];overnights?:Overnight[]
}
export type CityDraft=SingleDayDraft|MultiDayDraft
export interface DraftDay extends CityDraftDay {day_id:string;business_date:string;is_overnight?:boolean}
interface CityStateBase {
 id:string;status:'DRAFT'|'ACTIVE'|'PAUSED'|'COMPLETED'|'CANCELLED'|'ARCHIVED'
 anchor:{place_id:string;confirmed_at:string;source:'manual_known_place'}|null
 version:number;route_version:number;updated_at:string
 readiness:{missing_fields?:string[];issues?:{code:string;stop_id?:string}[];days?:{day_id:string;missing_fields:string[];frozen:boolean;future_locked:boolean}[];planning_available:false;notice:string}
}
export type CityTripState=CityStateBase&({schema_version:'city-state/1';draft:SingleDayDraft}|{schema_version:'city-state/2';draft:MultiDayDraft})
export interface CatalogItem {
 record:{kind:string;fact_id:string;name?:string;place_type?:string;stage_place_id?:string;program_id?:string;starts_at?:string;ends_at?:string|null;
  entrance_place_id?:string;direction?:string;parent_place_id?:string|null;address?:string|null;longitude?:number|null;latitude?:number|null;coordinate_system?:string;business_date?:string;
  food_id?:string;vendor_id?:string;place_id?:string;opens_at?:string|null;closes_at?:string|null;source_ids?:string[];url?:string;
  superseded_by?:string|null;description?:string|null;aliases?:string[];external_poi_id?:string|null;suggested_stay_min?:number|null;business_hours?:string|null;price_band?:string|null;
  category?:string|null;sort_order?:number;schedule_text?:string|null;stage_name?:string|null;
  validation_status?:string;planning_status?:string;planning_note?:string;business_status?:string;is_demo?:boolean;effective_to?:string|null;verified_at?:string|null}
 ready:boolean;issues:string[]
}
export type CityAction = {operation:'save_draft';draft:CityDraft}|{operation:'set_anchor';anchor:{place_id:string;confirmed_at:string};day_id?:string}
 |{operation:'mark_completed';stop_id:string;actual_start:string;actual_end:string;day_id?:string}|{operation:'skip_stop';stop_id:string;day_id?:string}
 |{operation:'transition';status:CityTripState['status']}
export type CityProposal = {status:'needs_confirmation';proposal_id:string;important_change:boolean;expires_at:string;impacts:string[]}
 |{status:'proposal_cleared';message:string}
export const emptyCityDraft=():SingleDayDraft=>({business_date:null,starts_at:null,ends_at:null,origin_place_id:null,
 destination_place_id:null,interests:[],dish_interest_ids:[],modes:['walking'],stops:[]})
export const cityApi={
 facts:()=>request<{items:CatalogItem[]}>('/city/v1/facts',{},5000),
 current:()=>request<{trip:CityTripState|null}>('/city/v1/trips/current',{},5000),
 list:()=>request<{items:CityTripState[]}>('/city/v1/trips'),
 get:(id:string)=>request<CityTripState>('/city/v1/trips/'+encodeURIComponent(id)),
 activate:(id:string)=>mutate<CityTripState>('/city/v1/trips/'+encodeURIComponent(id)+'/activate'),
 create:(draft:CityDraft)=>mutate<CityTripState>('/city/v1/trips',{draft}),
 update:(t:CityTripState,action:CityAction)=>mutate<CityTripState>(`/city/v1/trips/${t.id}`,{expected_version:t.version,...(isMultiDayDraft(t.draft)?{schema_version:'city-state/2'}:{}),action},'PATCH'),
 propose:(t:CityTripState,action:CityAction)=>mutate<CityProposal>(`/city/v1/trips/${t.id}/changes`,{expected_version:t.version,...(isMultiDayDraft(t.draft)?{schema_version:'city-state/2'}:{}),action}),
 confirm:(t:CityTripState,proposal_id:string)=>mutate<CityTripState>(`/city/v1/trips/${t.id}/confirm`,{expected_version:t.version,proposal_id})
}
