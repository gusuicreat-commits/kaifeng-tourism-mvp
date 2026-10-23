import type {RouteLocationNormalizedLoaded} from 'vue-router'
import type {ObjectRef} from './basketApi'
import {watch} from 'vue'
import {memoryEpoch} from './voiceDraft'
const key='bianyou:assistant-question:v1'
watch(memoryEpoch,()=>{try{sessionStorage.removeItem(key)}catch{}})
export function pageFocus(route:RouteLocationNormalizedLoaded):ObjectRef|null{
 const segment=route.path.split('/')[1],id=route.params.id
 if(!id||!['scenic','food','place','vendor'].includes(segment))return null
 return {kind:segment as ObjectRef['kind'],object_id:String(id)}
}
export function assistantLink(focus:ObjectRef|null,tripId?:string){return {path:'/assistant',query:{context:'city',...(tripId?{city_trip:tripId}:{}),...(focus?{focus_kind:focus.kind,focus_id:focus.object_id}:{})}}}
export function handoffQuestion(focus:ObjectRef,text:string){try{sessionStorage.setItem(key,JSON.stringify({focus,text:text.slice(0,2000),expires:Date.now()+15*60000}))}catch{}}
export function takeQuestion(focus:ObjectRef|null){try{const v=JSON.parse(sessionStorage.getItem(key)||'null');sessionStorage.removeItem(key);return focus&&v?.expires>Date.now()&&v.focus.kind===focus.kind&&v.focus.object_id===focus.object_id?String(v.text):''}catch{return ''}}
