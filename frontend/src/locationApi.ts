import {request} from './api'
export interface LocationCandidate {
 candidate_id:string;poi_id:string;name:string;address:string|null
 longitude:number;latitude:number;category:string|null
 entrance_status:'needs_entrance_verification';fact_status:'provider_candidate_only'
}
export interface LocationSearch {
 status:'candidates'|'needs_data';resolution_id:string|null;query:string
 provider_status:string;candidates:LocationCandidate[];expires_in_seconds?:number
}
export interface LocationConfirmation {status:'needs_data'|'matched_fact';selected:LocationCandidate;formal_fact_status:string;formal_match?:{fact_id:string;name:string;basis:string}|null}
function post<T>(path:string,body:unknown,signal?:AbortSignal){
 return request<T>('/city/v1'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal})
}
export const locationApi={
 search:(query:string,signal?:AbortSignal)=>post<LocationSearch>('/locations/search',{query},signal),
 confirm:(id:string,candidate_id:string,signal?:AbortSignal)=>post<LocationConfirmation>('/locations/'+encodeURIComponent(id)+'/confirm',{candidate_id},signal)
}
