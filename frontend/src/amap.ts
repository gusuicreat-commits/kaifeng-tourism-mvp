import type {CatalogItem,CityTripState} from './cityApi'
import type {RouteSnapshot} from './explorationApi'
export type Pair=[number,number]
export type TravelMode='walking'|'driving'|'transit'
export interface MapPoint {id:string;name:string;position:Pair;coordinateSystem:'GCJ02'|'WGS84';type:string;state:string;locked:boolean;entrance:boolean}
export interface MapLocation {position:Pair;coordinate_system:'GCJ02';source:'amap_geolocation'|'manual_map'|'manual_known_place';method:string;obtained_at:string;accuracy_m:number|null;place_id?:string}
export const validPair=(x:unknown):x is Pair=>Array.isArray(x)&&x.length===2&&x.every(n=>typeof n==='number'&&Number.isFinite(n))&&Math.abs(x[0])<=180&&Math.abs(x[1])<=90
export function locationUsable(p:MapLocation|null,now=Date.now()) {if(!p||!validPair(p.position))return false;const age=now-Date.parse(p.obtained_at);return age>=0&&age<900000&&(p.source!=='amap_geolocation'||(p.accuracy_m!==null&&p.accuracy_m<=200&&!/ip/i.test(p.method)))}
export function mapPoints(catalog:CatalogItem[],trip:CityTripState|null,route:RouteSnapshot|null):MapPoint[]{
 const current=route&&route.trip_id===trip?.id&&route.version===trip.version&&route.route_version===trip.route_version&&!route.stale&&route.itinerary&&Date.parse(route.itinerary.returns_at)>Date.now()?route.itinerary:null
 const activities=[...(current?.activities||[])]
 if(current?.ongoing?.original_activity&&!activities.some(a=>a.goal_id===current.ongoing?.stop_id))activities.unshift(current.ongoing.original_activity)
 const remaining=activities.filter(a=>!['completed','skipped'].includes(trip?.draft.stops.find(s=>s.stop_id===a.goal_id)?.progress||''))
 const ongoing=current?.ongoing?.stop_id
 return catalog.filter(x=>x.record.kind==='place'&&x.ready&&!x.record.is_demo&&x.record.validation_status==='verified'&&x.record.business_status==='active'&&x.record.source_ids?.length&&(!x.record.effective_to||Date.parse(x.record.effective_to)>Date.now())).flatMap(x=>{
  const r=x.record,p=[r.longitude,r.latitude];if(!validPair(p)||!['GCJ02','WGS84'].includes(r.coordinate_system||''))return []
  const related=activities.filter(a=>a.place_id===r.fact_id),next=remaining.find(a=>a.goal_id!==ongoing)
  const progress=(a:typeof activities[number])=>trip?.draft.stops.find(s=>s.stop_id===a.goal_id)?.progress
  const state=related.some(a=>a.goal_id===ongoing)?'正在游览':related.some(a=>a.goal_id===next?.goal_id)?'下一站':related.length&&related.every(a=>progress(a)==='completed')?'已完成':related.length&&related.every(a=>progress(a)==='skipped')?'已跳过':related.length?'已确认':'已有地点'
  const entrance=r.place_type==='entrance'&&catalog.some(e=>e.record.kind==='entrance'&&e.ready&&!e.record.is_demo&&e.record.validation_status==='verified'&&e.record.business_status==='active'&&e.record.entrance_place_id===r.fact_id)
  return [{id:r.fact_id,name:r.name||'未命名地点',position:p,coordinateSystem:r.coordinate_system as 'GCJ02'|'WGS84',type:r.place_type||'poi',state,entrance,locked:related.some(a=>a.required||trip?.draft.stops.find(s=>s.stop_id===a.goal_id)?.locked)}]
 })
}
export function navigationUrl(to:Pair,name:string,mode:TravelMode,from?:MapLocation|null){
 if(!validPair(to))return ''
 const q=new URLSearchParams({to:`${to[0]},${to[1]},${name.replace(/,/g,'，')}`,mode:{walking:'walk',driving:'car',transit:'bus'}[mode],coordinate:'gaode',src:'bianliangyouji',callnative:'1'})
 if(locationUsable(from||null))q.set('from',`${from!.position[0]},${from!.position[1]},我的位置`)
 return 'https://uri.amap.com/navigation?'+q
}
let sdk:Promise<any>|undefined
export async function loadAmap():Promise<any>{
 if(sdk)return sdk
 sdk=(async()=>{
  const response=await fetch('/api/maps/config',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(5000)})
  if(!response.ok)throw new Error('地图配置暂不可用，请重试')
  const config=await response.json()
  if(!config.configured||!config.key)throw new Error('地图尚未配置，仍可查看地点资料和行程')
  const w=window as any
  // No securityJsCode in frontend. Must precede the official script.
  w._AMapSecurityConfig={serviceHost:location.origin+config.service_host}
  if(w.AMap)return w.AMap
  await new Promise<void>((resolve,reject)=>{
   const script=document.createElement('script');script.dataset.bianliangAmap='1';script.referrerPolicy='strict-origin-when-cross-origin'
   const timer=setTimeout(()=>{script.remove();reject(new Error('高德地图加载超时，请重试'))},12000)
   script.src='https://webapi.amap.com/maps?v=2.0&key='+encodeURIComponent(config.key)
   script.onload=()=>{clearTimeout(timer);w.AMap?resolve():reject(new Error('地图 SDK 未能初始化'))}
   script.onerror=()=>{clearTimeout(timer);script.remove();reject(new Error('高德地图加载失败，请检查网络'))};document.head.append(script)
  });return w.AMap
 })().catch(e=>{sdk=undefined;throw e})
 return sdk
}
export function plugin(A:any,name:string):Promise<void>{return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('地图插件加载超时')),10000);A.plugin(name,()=>{clearTimeout(timer);resolve()})})}
export function gcjPosition(A:any,point:MapPoint):Promise<Pair>{
 if(point.coordinateSystem==='GCJ02')return Promise.resolve(point.position)
 return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('坐标转换超时')),8000);A.convertFrom(point.position,'gps',(status:string,result:any)=>{clearTimeout(timer);const p=result?.locations?.[0],pair:Pair=[p?.getLng?.()??p?.lng,p?.getLat?.()??p?.lat];status==='complete'&&validPair(pair)?resolve(pair):reject(new Error('坐标转换失败'))})})
}
