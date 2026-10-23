import {mutate,request} from './api'
export type ObjectKind='scenic'|'food'|'place'|'poi'|'show'|'vendor'|'program'
export interface ObjectRef{kind:ObjectKind;object_id:string}
export interface BasketChoice{key:string;kind:ObjectKind;id:string;name:string;fact_kind?:string|null;fact_id?:string|null;parent_place_id?:string|null;priority?:'must'|'want'|'optional';status?:'pending'|'standby'|'arranged';note?:string;source?:'home'|'detail'|'basket'|'assistant'|'legacy';added_at?:string;updated_at?:string;stop_ids?:string[]}
export interface Basket{trip_id:string;version:number;items:BasketChoice[]}
const prefix=(id:string)=>'/city/v1/trips/'+encodeURIComponent(id)
export const basketApi={
 get:(id:string)=>request<Basket>(prefix(id)+'/basket'),
 change:(id:string,version:number,item:BasketChoice,operation:'put'|'remove'='put')=>mutate<Basket>(prefix(id)+'/basket',{expected_version:version,operation,item:{kind:item.kind,object_id:item.id,priority:item.priority||'want',status:item.status==='standby'?'standby':'pending',note:item.note||'',source:item.source||'detail'}},'PATCH')
}
