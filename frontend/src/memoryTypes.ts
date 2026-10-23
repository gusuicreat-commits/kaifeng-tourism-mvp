import type {Interview} from './planningWorkflow'
import type {Plan,PlanInput} from './types'
export interface MemorySession {session_id:string;active_trip_id:string|null;active_city_trip_id:string|null;expires_at:string;history_enabled:boolean;long_term_memory_enabled:false;authenticated:false}
export interface MemoryTrip {id:string;status:'DRAFT'|'PLANNED'|'ACTIVE'|'PAUSED'|'COMPLETED'|'CANCELLED'|'ARCHIVED';interview:Interview;constraints:PlanInput|null;excluded_ids:string[];completed_ids:string[];route_id:string|null;version:number;route_version:number;updated_at:string}
export interface ChatResponse {reply:string;status:'answered'|'needs_confirmation';proposal_id:string|null;candidate:{operation:'update_constraints'|'remove_stop';patch:Partial<PlanInput>;poi_id?:string}|null;trip_version:number;route_version:number;memory_written:boolean;long_term_written:false}
export interface MemoryContext {trip:MemoryTrip;recent_messages:{id:string;role:'user'|'assistant';text:string}[];summary:{text:string;authoritative:false};limits:{recent_messages:number;summary_characters:number}}
export interface MemoryCommit {trip:MemoryTrip;plan:Plan|null;status:string}
