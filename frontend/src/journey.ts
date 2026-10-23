import {computed,reactive,watch} from 'vue'
import {api,ApiError} from './api'
import {cityApi,emptyCityDraft} from './cityApi'
import type {CatalogItem,CityAction,CityDraft,CityProposal,CityTripState} from './cityApi'
import {memoryEpoch} from './voiceDraft'
import {scenicCatalog} from './data/scenicCatalog'
import {foodCatalog} from './data/foods'
import {explorationApi,type RouteSnapshot} from './explorationApi'
import {basketApi,type BasketChoice} from './basketApi'

export interface Choice extends BasketChoice {}
const storageKey='bianyou:exploration:v2'
function restore():{choices:Choice[];favorites:Choice[];legacyChoices:Choice[]}{try{const raw=JSON.parse(sessionStorage.getItem(storageKey)||'null');if(raw?.expires>Date.now()){const safe=(a:unknown)=>Array.isArray(a)?a.filter((x:Choice)=>x&&['scenic','food','place','show','poi','vendor','program'].includes(x.kind)&&typeof x.id==='string'&&typeof x.name==='string'&&typeof x.key==='string').slice(0,100):[];return {choices:[],favorites:safe(raw.favorites),legacyChoices:safe(raw.serverBacked?raw.legacyChoices:raw.choices)}}}catch{}return {choices:[],favorites:[],legacyChoices:[]}}
const restored=restore()
export const journey=reactive({route:null as RouteSnapshot|null,routeError:'',trip:null as CityTripState|null,catalog:[] as CatalogItem[],choices:restored.choices,favorites:restored.favorites,legacyChoices:restored.legacyChoices,basketVersion:0,basketBusy:false,basketError:'',loading:false,loaded:false,error:'',toast:'',offline:false})
let pending:Promise<void>|undefined,toastTimer:ReturnType<typeof setTimeout>|undefined
export function notify(text:string){journey.toast=text;if(toastTimer)clearTimeout(toastTimer);toastTimer=setTimeout(()=>journey.toast='',3500)}
watch(()=>[journey.choices,journey.favorites,journey.legacyChoices],()=>{try{sessionStorage.setItem(storageKey,JSON.stringify({serverBacked:true,legacyChoices:journey.legacyChoices,favorites:journey.favorites,expires:Date.now()+24*3600000}))}catch{}},{deep:true})
watch(memoryEpoch,()=>{journey.route=null;journey.routeError='';journey.trip=null;journey.catalog=[];journey.choices=[];journey.favorites=[];journey.legacyChoices=[];journey.basketVersion=0;journey.basketError='';journey.loaded=false;try{sessionStorage.removeItem(storageKey)}catch{}})
export async function loadJourney(force=false){if(pending)return pending;if(journey.loaded&&!force)return;journey.loading=true;journey.error='';const epoch=memoryEpoch.value
 pending=(async()=>{try{await api.memorySession();const [facts,current]=await Promise.all([cityApi.facts(),cityApi.current()]);if(epoch!==memoryEpoch.value)return;journey.catalog=facts.items;journey.trip=current.trip;journey.offline=false;journey.loaded=true;await loadBasket()}catch(e){if(epoch===memoryEpoch.value){journey.error=(e as Error).message;journey.offline=true}}finally{journey.loading=false;pending=undefined}})();return pending}
export const clone=<T,>(v:T):T=>JSON.parse(JSON.stringify(v)) as T
export const nameOf=(id:string|null|undefined)=>journey.catalog.find(x=>x.record.fact_id===id)?.record.name||'待核对地点'
export const cityStatus:Record<CityTripState['status'],string>={DRAFT:'尚未开始',ACTIVE:'进行中',PAUSED:'已暂停',COMPLETED:'已结束',CANCELLED:'已取消',ARCHIVED:'已归档'}
export const nextCityStop=computed(()=>journey.trip?.draft.stops.find(x=>x.progress==='pending'))
export function choice(kind:Choice['kind'],id:string,name:string):Choice{return {key:kind+':'+id,kind,id,name}}
export function isFavorite(item:Choice){return journey.favorites.some(x=>x.key===item.key)}
export function toggleFavorite(item:Choice){const i=journey.favorites.findIndex(x=>x.key===item.key);if(i>=0)journey.favorites.splice(i,1);else journey.favorites.push(item);notify(i>=0?'已取消收藏':'已收藏')}
let creating:Promise<CityTripState>|null=null
export async function ensureJourney(){
 if(!journey.loaded||journey.offline)await loadJourney(true)
 if(!journey.loaded||journey.offline)throw new Error(journey.error||'无法确认当前旅程，请重试后再加入')
 if(journey.trip&&!['COMPLETED','CANCELLED','ARCHIVED'].includes(journey.trip.status))return journey.trip
 if(creating)return creating
 const epoch=memoryEpoch.value
 creating=(async()=>{await api.memorySession();const t=await cityApi.create(emptyCityDraft());if(epoch!==memoryEpoch.value)throw new Error('记录已清除');journey.trip=t;journey.choices=[];journey.basketVersion=0;return t})()
 try{return await creating}finally{creating=null}
}
export async function loadBasket(){const id=journey.trip?.id,epoch=memoryEpoch.value;if(!id){journey.choices=[];journey.basketVersion=0;return}try{const b=await basketApi.get(id);if(epoch===memoryEpoch.value&&id===journey.trip?.id){journey.choices=b.items;journey.basketVersion=b.version;journey.basketError=''}}catch(e){if(epoch===memoryEpoch.value)journey.basketError=(e as Error).message}}
export async function saveChoice(item:Choice,operation:'put'|'remove'='put'){
 if(journey.basketBusy)return false
 journey.basketBusy=true;journey.basketError='';const epoch=memoryEpoch.value
 try{const t=await ensureJourney(),b=await basketApi.change(t.id,journey.basketVersion,item,operation);if(epoch!==memoryEpoch.value||journey.trip?.id!==t.id)return false;journey.choices=b.items;journey.basketVersion=b.version;basketChannel?.postMessage({trip_id:t.id});return true}
 catch(e){if(epoch===memoryEpoch.value){journey.basketError=(e as Error).message;notify(journey.basketError);if(e instanceof ApiError&&e.status===409)await loadBasket()}return false}
 finally{journey.basketBusy=false}
}
export async function addChoice(item:Choice){if(journey.choices.some(x=>x.key===item.key)){notify('已在旅行素材篮中');return}if(await saveChoice(item))notify('已加入素材篮')}
export async function removeChoice(key:string){const item=journey.choices.find(x=>x.key===key);if(item&&await saveChoice(item,'remove'))notify('已移出素材篮，正式行程保持不变')}
const normalize=(s:string)=>s.replace(/大宋|景区|（.*?）|\s/g,'')
export function matchingFact(item:Choice){if(item.fact_id)return journey.catalog.find(x=>x.record.fact_id===item.fact_id&&x.record.kind===item.fact_kind);if(['place','poi','show','vendor','program'].includes(item.kind))return journey.catalog.find(x=>x.record.fact_id===item.id&&x.record.kind===({show:'show_session',poi:'place'}[item.kind as 'show'|'poi']||item.kind));return undefined}
export function scenicIdFor(name:string){return scenicCatalog.find(s=>normalize(s.name)===normalize(name))?.id}
export function detailFor(item:Choice){if(item.kind==='scenic')return '/scenic/'+item.id;if(item.kind==='food')return '/food/'+item.id;if(item.kind==='vendor')return '/vendor/'+encodeURIComponent(item.id);return '/place/'+encodeURIComponent(item.id)}
export function vendorsFor(foodName:string,foodId?:string|null){const food=journey.catalog.find(x=>x.record.kind==='food'&&(foodId?x.record.fact_id===foodId:x.record.name===foodName));if(!food)return []
 return journey.catalog.filter(x=>x.record.kind==='supply'&&x.record.food_id===food.record.fact_id&&x.ready&&!x.record.is_demo).flatMap(supply=>{const vendor=journey.catalog.find(x=>x.record.kind==='vendor'&&x.record.fact_id===supply.record.vendor_id&&x.ready&&!x.record.is_demo);const place=journey.catalog.find(x=>x.record.kind==='place'&&x.record.fact_id===vendor?.record.place_id&&x.ready&&!x.record.is_demo);return place?[place]:[]})}
export function foodForName(name:string){return foodCatalog.find(f=>f.name===name)}
export function placeLink(id:string){return '/place/'+encodeURIComponent(id)}
export function amapLink(name:string){return 'https://uri.amap.com/search?keyword='+encodeURIComponent(name)+'&city='+encodeURIComponent('开封')+'&view=map'}
export const localDate=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
export const clock=(iso:string|null|undefined)=>iso?new Date(iso).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}):'待核算'
export async function updateCity(action:CityAction){if(!journey.trip)throw new Error('请先保存行程');const epoch=memoryEpoch.value,trip=journey.trip;try{const t=await cityApi.update(trip,action);if(epoch!==memoryEpoch.value)throw new Error('记录已清除，请重新开始');journey.trip=t;return {trip:t}}catch(e){if(!(e instanceof ApiError)||e.code!=='CONFIRMATION_REQUIRED'||epoch!==memoryEpoch.value)throw e;const proposal=await cityApi.propose(trip,action);if(epoch!==memoryEpoch.value)throw new Error('记录已清除，请重新开始');return {proposal}}}
export async function confirmCity(proposal:Extract<CityProposal,{status:'needs_confirmation'}>){if(!journey.trip)throw new Error('行程不存在');const epoch=memoryEpoch.value;const result=await cityApi.confirm(journey.trip,proposal.proposal_id);if(epoch!==memoryEpoch.value)throw new Error('记录已清除，请重新开始');journey.trip=result;return result}

export async function importLegacyChoices(){for(const item of [...journey.legacyChoices]){if(!await saveChoice({...item,source:'legacy'}))return;journey.legacyChoices=journey.legacyChoices.filter(x=>x.key!==item.key)}notify('旧清单已导入当前素材篮')}
const basketChannel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('bianyou:basket:version'):null
basketChannel?.addEventListener('message',event=>{if(event.data?.trip_id===journey.trip?.id)void loadBasket()})
window.addEventListener('focus',()=>{if(journey.trip)void loadBasket()})

let routeSequence=0
export async function loadConfirmedRoute(){const trip=journey.trip,epoch=memoryEpoch.value,sequence=++routeSequence;journey.route=null;journey.routeError=''
 if(!trip?.route_version)return
 try{const saved=await explorationApi.current(trip.id);if(sequence===routeSequence&&epoch===memoryEpoch.value&&journey.trip?.id===trip.id&&journey.trip.version===saved.version&&journey.trip.route_version===saved.route_version)journey.route=saved}
 catch(e){if(sequence===routeSequence&&epoch===memoryEpoch.value)journey.routeError=(e as Error).message}}
watch(()=>[journey.trip?.id,journey.trip?.version,journey.trip?.route_version],()=>{void loadConfirmedRoute()})
export function usableItinerary(){const saved=journey.route;return saved&&saved.trip_id===journey.trip?.id&&saved.version===journey.trip.version&&!saved.stale&&saved.itinerary&&Date.parse(saved.itinerary.returns_at)>Date.now()?saved.itinerary:null}
export function plannedActivity(stopId:string){return usableItinerary()?.activities.find(a=>a.goal_id===stopId)}
export function plannedLeg(stopId:string){const route=usableItinerary(),index=route?.activities.findIndex(a=>a.goal_id===stopId)??-1;return index>=0?route?.legs[index]:undefined}
export function arrivalSummary(stopId:string){const leg=plannedLeg(stopId);if(!leg)return '交通和到达时间待核算';return '预计转场 '+Math.ceil((Date.parse(leg.arrives_at)-Date.parse(leg.departs_at))/60000)+' 分钟 · '+clock(leg.arrives_at)+' 到达'}
