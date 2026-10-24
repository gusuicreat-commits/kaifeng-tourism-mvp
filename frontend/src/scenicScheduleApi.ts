import {request} from './api'
export interface Performance {
 performance_id:number;program_name:string;venue_name:string;area:string|null
 start_time:string|null;duration_minutes:number|null;end_time:string|null;entry_deadline:string|null
 requires_booking:boolean|null;weather_sensitive:boolean|null;schedule_note:string
 verified_poi_id:string|null;eligible_for_solver:boolean;source_freshness:string
}
export interface ScheduleStatus {
 approved_schedule:boolean;status:'approved_schedule'|'stale_source'|'review_required'|'unavailable'
 display_status:'official_schedule_reviewed'|'stale_reference_only'|'today_unconfirmed'
 notice:string;source_updated_on:string|null;last_fetched_at:string|null
 latest_review_status?:string;stale_warning?:boolean
 source:{source_ref:string;source_updated_on:string|null;fetched_at:string}|null
}
export interface Schedule extends ScheduleStatus {performances:Performance[]}
const path=(park:string,kind:string,date:string)=>`/scenic/parks/${encodeURIComponent(park)}/${kind}?date=${date}`
export const scenicScheduleApi={
 performances:(park:string,date:string,signal?:AbortSignal)=>request<Schedule>(path(park,'performances',date),{signal}),
 status:(park:string,date:string,signal?:AbortSignal)=>request<ScheduleStatus>(path(park,'schedule-status',date),{signal})
}

