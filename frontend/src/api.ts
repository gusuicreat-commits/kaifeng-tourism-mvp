import type { Scenic,Network,PlanInput,Plan,Crowds,Dataset,ScheduleFact } from './types'
import type {Interview} from './planningWorkflow'
import type {ChatResponse,MemoryCommit,MemoryContext,MemorySession,MemoryTrip} from './memoryTypes'
const base=import.meta.env.VITE_API_BASE || ''
// Agent 后端单步最多处理 45 秒，增加网络余量后由前端等待 55 秒。
export const AGENT_STEP_TIMEOUT_MS=55000
// 多天规划单日交通最多约 60 次请求，服务端交通时限约 15 秒，另留求解时间。
export const PLANNING_PREVIEW_TIMEOUT_MS=60000
export class ApiError extends Error {constructor(message:string,public status:number,public code?:string){super(message)}}
export async function request<T>(path:string,options:RequestInit={},timeoutMs=20000):Promise<T>{
 const controller=new AbortController()
 const cancel=()=>controller.abort()
 options.signal?.addEventListener('abort',cancel,{once:true})
 if(options.signal?.aborted)controller.abort()
 const timer=setTimeout(()=>controller.abort(),timeoutMs)
 try{
  const res=await fetch(base+'/api'+path,{...options,signal:controller.signal})
  if(!res.ok){
   const body=await res.json().catch(()=>({detail:'服务暂不可用'}))
   const d=body.detail
   throw new ApiError(typeof d==='string'?d:d?.errors?d.message+'：'+d.errors.map((e:{row:number;message:string})=>`第${e.row}行 ${e.message}`).join('；'):Array.isArray(d)?d.map((e:{msg:string})=>e.msg).join('；'):d?.message||'请求失败',res.status,d?.code)
  }
  return await res.json()
 }catch(e){
  if(e instanceof TypeError) throw new Error('暂时无法加载，请稍后重试。')
  if(e instanceof Error && e.name==='AbortError') throw new Error('请求超时，请重试。')
  throw e
 }finally{clearTimeout(timer);options.signal?.removeEventListener('abort',cancel)}
}
const pending=new Map<string,string>()
export function clearPendingMutations(){pending.clear()}
export async function mutate<T>(path:string,body:Record<string,unknown>={},method='POST',timeout=20000):Promise<T>{
 const key=path+JSON.stringify(body),id=pending.get(key)||crypto.randomUUID();pending.set(key,id)
 try{const result=await request<T>(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,request_id:id})},timeout);pending.delete(key);return result}
 catch(e){if(e instanceof ApiError&&e.status<500)pending.delete(key);throw e}
}
export const api={
 memorySession:()=>request<MemorySession>('/v1/sessions',{method:'POST'}),
 memoryTrip:(id:string)=>request<MemoryTrip>('/v1/trips/'+encodeURIComponent(id)),
 memoryTrips:()=>request<{items:MemoryTrip[]}>('/v1/trips'),
 createTrip:(interview:Interview)=>mutate<MemoryTrip>('/v1/trips',{interview}),
 activateTrip:(id:string)=>request<MemoryTrip>(`/v1/trips/${encodeURIComponent(id)}/activate`,{method:'POST'}),
 saveDraft:(trip:MemoryTrip,interview:Interview)=>mutate<MemoryTrip>(`/v1/trips/${trip.id}`,{expected_version:trip.version,operation:'save_draft',interview},'PATCH'),
 transitionTrip:(trip:MemoryTrip,status:MemoryTrip['status'])=>mutate<MemoryTrip>(`/v1/trips/${trip.id}`,{expected_version:trip.version,operation:'transition',status},'PATCH'),
 completePoi:(trip:MemoryTrip,poi_id:string)=>mutate<MemoryTrip>(`/v1/trips/${trip.id}`,{expected_version:trip.version,operation:'mark_completed',poi_id},'PATCH'),
 planTrip:(trip:MemoryTrip,constraints:PlanInput)=>mutate<MemoryCommit>(`/v1/trips/${trip.id}/replan`,{expected_version:trip.version,constraints}),
 memoryContext:(id:string)=>request<MemoryContext>(`/v1/trips/${id}/context`),
 chat:(trip:MemoryTrip,text:string,channel:'text'|'voice'='text')=>mutate<ChatResponse>('/v1/chat',{trip_id:trip.id,text,channel,route_version:trip.route_version}),
 confirm:(trip:MemoryTrip,proposal_id:string)=>mutate<MemoryCommit>(`/v1/trips/${trip.id}/confirm`,{expected_version:trip.version,proposal_id}),
 history:(enabled:boolean)=>request<{history_enabled:boolean}>('/v1/memory/history',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})}),
 deleteMemory:()=>mutate<{receipt_id:string;status:string;message:string}>('/v1/privacy/delete-request',{confirmed:true}),
 exportMemory:()=>request<Record<string,unknown>>('/v1/memory/export'),
 speechStatus:()=>request<{available:boolean;max_seconds:number}>('/speech/status'),
 transcribe:(audio:Blob,signal?:AbortSignal)=>{
  const body=new FormData();body.append('file',audio,'recording.wav')
  return request<{text:string}>('/speech/transcriptions',{method:'POST',body,signal},50000)
 },
 scenics:(q='',timeoutMs=20000)=>request<{items:Scenic[]}>('/scenics?q='+encodeURIComponent(q),{},timeoutMs),
 scenic:(id:string)=>request<Scenic>('/scenics/'+encodeURIComponent(id)),
 network:(id:string)=>request<Network>(`/scenics/${encodeURIComponent(id)}/network`),
 crowds:(id:string,dataset:Dataset,hour:number,targetTime?:string)=>request<Crowds>(`/scenics/${encodeURIComponent(id)}/crowds?dataset=${dataset}&hour=${hour}${targetTime?'&target_time='+encodeURIComponent(targetTime):''}`),
 schedules:(id:string,date:string)=>request<{items:ScheduleFact[]}>(`/scenics/${encodeURIComponent(id)}/schedules?date=${encodeURIComponent(date)}`),
 aiStatus:()=>request<{available:boolean}>('/ai/status'),
 interpret:(text:string,selected_site_id:string,signal?:AbortSignal)=>request<{patch:Partial<PlanInput>;unresolved_names:string[];message:string}>('/ai/interpret',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,selected_site_id}),signal}),
 plan:(body:PlanInput)=>request<Plan>('/plans',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),
 getPlan:(id:string)=>request<Plan>('/plans/'+encodeURIComponent(id))
}
