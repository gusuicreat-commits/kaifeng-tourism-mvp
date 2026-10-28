// Pure, read-only projections of single- and multi-day drafts and itineraries.
// Kept free of api.ts so node-side tests (amap) can import them.
import type {CityDraft,DraftDay,MultiDayDraft} from './cityApi'
import type {Itinerary,MultiDayItinerary,RouteDay,RouteSnapshot} from './explorationApi'
export const isMultiDayDraft=(draft:CityDraft|undefined|null):draft is MultiDayDraft=>draft?.schema_version==='city-draft/2'&&Array.isArray((draft as MultiDayDraft).days)
export const draftDays=(draft:CityDraft|undefined|null):DraftDay[]=>isMultiDayDraft(draft)?draft.days.map(d=>({...d,is_overnight:!!draft.overnights?.some(o=>o.from_day_id===d.day_id)})):(draft?[{...draft,day_id:'day:'+(draft.business_date||'legacy'),business_date:draft.business_date||'',status:'pending'} as DraftDay]:[])
export const isMultiDayItinerary=(itinerary:Itinerary|undefined|null):itinerary is MultiDayItinerary=>!!itinerary&&'days' in itinerary&&itinerary.schema_version==='city-itinerary/2'
export const routeDays=(route:RouteSnapshot|null|undefined,draft?:CityDraft|null):RouteDay[]=>{
 const itinerary=route?.itinerary
 if(isMultiDayItinerary(itinerary))return itinerary.days.map(d=>({...d.itinerary,day_id:d.day_id,business_date:d.business_date,status:d.status,original_requirements:d.original_requirements,overnight:itinerary.overnights?.find(o=>o.from_day_id===d.day_id),...route?.days?.find(x=>x.day_id===d.day_id)})) as RouteDay[]
 if(!itinerary)return []
 const date=draft&&!('days' in draft)&&draft.business_date||new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(itinerary.returns_at))
 return [{...itinerary,day_id:'day:'+date,business_date:date,status:'active'}]
}
