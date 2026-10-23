<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {loadAmap,plugin,gcjPosition,locationUsable,navigationUrl,validPair,type MapPoint,type MapLocation,type Pair,type TravelMode} from '../amap'
import {memoryEpoch} from '../voiceDraft'
const props=withDefaults(defineProps<{points:MapPoint[];selected?:string;view?:string;revision?:string;owner?:string;savedLocation?:MapLocation|null}>(),{view:'city'})
const emit=defineEmits<{select:[id:string];location:[value:MapLocation|null]}>()
const el=ref<HTMLDivElement>(),status=ref('正在加载高德地图…'),loaded=ref(false),busy=ref(false),routeBusy=ref(false),locationValue=ref<MapLocation|null>(null),candidate=ref<MapLocation|null>(null),routeText=ref(''),mode=ref<TravelMode>('walking'),tick=ref(Date.now()),target=ref<{point:MapPoint;position:Pair}|null>(null)
const usable=computed(()=>locationUsable(locationValue.value,tick.value)),navigation=computed(()=>target.value?navigationUrl(target.value.position,target.value.point.name,mode.value,usable.value?locationValue.value:null):'')
let initializing=false
let A:any,map:any,markers:any[]=[],userMarker:any,routeService:any,dead=false,generation=0,routeGeneration=0,locationGeneration=0,timer:ReturnType<typeof setInterval>|undefined
const converted=new Map<string,Pair>()
function clearRoute(){routeGeneration++;routeService?.setMap?.(null);routeService?.clear();routeService=undefined;routeBusy.value=false;routeText.value=''}
function setLocation(value:MapLocation|null){locationGeneration++;busy.value=false;locationValue.value=value;candidate.value=null;clearRoute();userMarker?.setMap(null);userMarker=undefined;if(value&&map){userMarker=new A.Marker({position:value.position,title:'当前界面位置',label:{content:'我的位置',direction:'top'}});map.add(userMarker);map.setCenter(value.position)}emit('location',value)}
function confirmPoint(){if(candidate.value)setLocation(candidate.value)}
async function locate(){
 if(!loaded.value||busy.value)return
 const epoch=++locationGeneration;busy.value=true;status.value='正在请求一次定位…'
 try{
  if(!window.isSecureContext)throw new Error('当前页面不是安全环境，请使用 HTTPS 或手动选点')
  if(!navigator.geolocation)throw new Error('此浏览器不支持定位，请手动选点')
  await plugin(A,'AMap.Geolocation');if(dead||epoch!==locationGeneration)return
  const result:any=await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('定位超时，请重试或手动选点')),11000);new A.Geolocation({enableHighAccuracy:true,timeout:10000,convert:true,needAddress:false,showMarker:false,showCircle:false,panToLocation:false,zoomToAccuracy:false}).getCurrentPosition((s:string,r:any)=>{clearTimeout(timeout);s==='complete'?resolve(r):reject(new Error('定位失败或权限被拒绝，请选择已知地点或点击地图'))})})
  if(dead||epoch!==locationGeneration)return
  const p=result.position,position:Pair=[p?.getLng?.()??p?.lng,p?.getLat?.()??p?.lat];if(!validPair(position))throw new Error('定位结果无有效坐标，请手动选点')
  const accuracy=typeof result.accuracy==='number'&&Number.isFinite(result.accuracy)&&result.accuracy>=0?result.accuracy:null
  setLocation({position,coordinate_system:'GCJ02',source:'amap_geolocation',method:String(result.location_type||result.type||'unknown'),obtained_at:new Date().toISOString(),accuracy_m:accuracy})
  status.value=locationUsable(locationValue.value)?'已获取位置，仅用于当前页面':'仅取得粗略或精度未知的位置，请手动确认出发位置'
 }catch(e){if(!dead&&epoch===locationGeneration)status.value=(e as Error).message}finally{if(epoch===locationGeneration)busy.value=false}
}
function knownPoint(){if(!target.value)return;candidate.value={position:target.value.position,coordinate_system:'GCJ02',source:'manual_known_place',method:'user_selected',place_id:target.value.point.id,obtained_at:new Date().toISOString(),accuracy_m:null}}
async function preview(){
 if(!target.value||!usable.value||routeBusy.value)return
 clearRoute();const generation=routeGeneration;routeBusy.value=true;routeText.value='正在查询地图路线…'
 try{const name={walking:'Walking',driving:'Driving',transit:'Transfer'}[mode.value];await plugin(A,'AMap.'+name);if(dead||generation!==routeGeneration)return
  routeService=new A[name]({map,city:'开封',hideMarkers:true,autoFitView:true})
  const result:any=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('路线查询超时')),12000);routeService.search(locationValue.value!.position,target.value!.position,(s:string,r:any)=>{clearTimeout(timer);s==='complete'?resolve(r):reject(new Error('没有可用路线或服务暂不可用'))})})
  if(dead||generation!==routeGeneration)return
  const r=mode.value==='transit'?result.plans?.[0]:result.routes?.[0];if(!r)throw new Error('服务未返回可用路线')
  const distance=Number(r.distance),seconds=Number(r.time)
  routeText.value=`地图查询预览 · ${Number.isFinite(distance)?Math.round(distance)+' 米':'距离未知'} · ${Number.isFinite(seconds)?Math.ceil(seconds/60)+' 分钟':'耗时未知'}。不代表正式行程或园内通行已核验。`
 }catch(e){if(!dead&&generation===routeGeneration){routeService?.clear();routeText.value=(e as Error).message}}finally{if(generation===routeGeneration)routeBusy.value=false}
}
function focusView(){if(!map)return;if(props.view==='city'){map.setZoomAndCenter(12,[114.307,34.799]);return}const name=props.view==='wansui'?'万岁山':'清明上河园',p=props.points.find(p=>p.name.includes(name));const pos=p&&converted.get(p.id);if(pos)map.setZoomAndCenter(16,pos);else{map.setZoomAndCenter(12,[114.307,34.799]);status.value='该景区尚无核验坐标，保留城市视角'}}
function selectTarget(){const p=props.points.find(p=>p.id===props.selected),position=p&&converted.get(p.id);target.value=p&&position?{point:p,position}:null;clearRoute();if(position&&map)map.setCenter(position)}
async function renderPoints(){if(!map)return;const epoch=++generation;markers.forEach(m=>m.setMap(null));markers=[];converted.clear();let failed=0
 for(const point of props.points){try{const position=await gcjPosition(A,point);if(dead||epoch!==generation)return;converted.set(point.id,position);const label=document.createElement('span');label.className='amap-place-label';label.textContent=point.state==='已有地点'?point.name:point.state+' · '+point.name;const marker=new A.Marker({position,title:point.name,label:{content:label,direction:'top'}});marker.on('click',()=>emit('select',point.id));map.add(marker);markers.push(marker)}catch{failed++}}
 if(dead||epoch!==generation)return;selectTarget();focusView();if(failed)status.value=`${failed} 个地点坐标转换失败，未显示`
}
async function init(){if(initializing||loaded.value)return;initializing=true;status.value='正在加载高德地图…';try{A=await loadAmap();if(dead)return;map=new A.Map(el.value,{zoom:12,center:[114.307,34.799],resizeEnable:true,viewMode:'2D'});map.on('click',(event:any)=>{const p=event.lnglat,position:Pair=[p?.getLng?.()??p?.lng,p?.getLat?.()??p?.lat];if(validPair(position))candidate.value={position,coordinate_system:'GCJ02',source:'manual_map',method:'user_selected',obtained_at:new Date().toISOString(),accuracy_m:null}})
 await new Promise<void>((resolve,reject)=>{const t=setTimeout(()=>reject(new Error('地图底图加载超时，请重试')),12000);map.on('complete',()=>{clearTimeout(t);resolve()})});if(dead)return;loaded.value=true;status.value='高德地图已加载 · 可拖动缩放，点击地图可手动选点';await renderPoints()
 }catch(e){if(!dead){status.value=(e as Error).message;map?.destroy();map=undefined}}finally{initializing=false}}
watch(()=>props.points,()=>{clearRoute();void renderPoints()},{deep:true});watch(()=>props.selected,selectTarget);watch(()=>props.view,()=>{clearRoute();focusView()});watch(()=>props.revision,()=>{clearRoute();candidate.value=null});watch(mode,clearRoute)
watch(()=>props.savedLocation,v=>{if(v&&locationUsable(v))setLocation(v)})
watch(()=>props.owner,()=>{locationGeneration++;busy.value=false;candidate.value=null;setLocation(null)})
watch(memoryEpoch,()=>{locationGeneration++;busy.value=false;candidate.value=null;setLocation(null)})
onMounted(()=>{void init();timer=setInterval(()=>{tick.value=Date.now();if(locationValue.value&&!locationUsable(locationValue.value))clearRoute()},1000)})
onBeforeUnmount(()=>{dead=true;generation++;locationGeneration++;clearRoute();if(timer)clearInterval(timer);map?.destroy();converted.clear();emit('location',null)})
defineExpose({locate})
</script>
<template><section class="amap-workspace" aria-label="高德城市地图"><div ref="el" class="amap-live-canvas" data-testid="amap-canvas" :data-loaded="loaded"/><div class="amap-toolbar"><button class="button secondary" :disabled="!loaded||busy" @click="locate">{{busy?'定位中…':'我在这里'}}</button><button v-if="!loaded" class="button secondary" @click="init">重试地图</button></div><p role="status" class="amap-status">{{status}}</p><p v-if="!loaded" class="amap-help">可继续搜索下方已知地点；未配置地图时不请求定位。</p><section v-if="candidate" class="amap-location-card"><p>手动选择：{{candidate.position.map(n=>n.toFixed(5)).join(', ')}} · GCJ-02</p><p>尚未使用此位置；不代表 GPS 验证。</p><button class="button primary" @click="confirmPoint">确认作为临时出发位置</button><button class="button secondary" @click="candidate=null">取消选点</button></section><section v-if="locationValue" class="amap-location-card" data-testid="location-card"><strong>{{locationValue.source==='amap_geolocation'?'高德浏览器融合定位':locationValue.source==='manual_map'?'手动地图选点':'手动已知地点'}}</strong><p>{{locationValue.position.map(n=>n.toFixed(5)).join(', ')}} · {{locationValue.coordinate_system}} · {{locationValue.method}}</p><p>精度：{{locationValue.accuracy_m===null?'未知':locationValue.accuracy_m+' 米'}} · {{new Date(locationValue.obtained_at).toLocaleTimeString()}} · {{usable?'当前可用':'精度不足或已过期，请重新确认'}}</p><small>仅用于当前页面；未自动写入旅行记忆。</small><button class="quiet-link" @click="setLocation(null)">清除临时位置</button></section><section v-if="target" class="amap-preview"><p>目的地：{{target.point.name}} · {{target.point.entrance?'已核验入口':'地点位置，非核验入口'}}</p><button class="button secondary" :disabled="!loaded" @click="knownPoint">选择此地点为出发位置</button><label>交通方式<select v-model="mode" aria-label="交通方式"><option value="walking">步行</option><option value="driving">驾车</option><option value="transit">公交</option></select></label><div class="amap-route-actions"><button class="button primary" :disabled="!usable||routeBusy" @click="preview">{{routeBusy?'查询中…':'预览前往路线'}}</button><a class="button secondary" :href="navigation" target="_blank" rel="noopener noreferrer">{{target.point.entrance?'在高德查看入口路线':'在高德查看路线'}}</a></div><p v-if="!usable">请先定位或确认手动出发位置；外部高德可自行选择起点。</p><p v-if="routeText" role="status">{{routeText}}</p><button v-if="routeText" class="quiet-link" @click="clearRoute">清除路线</button></section></section></template>
<style scoped>
.amap-workspace{position:relative;border:1px solid #d8d0bb;border-radius:16px;overflow:hidden;background:#f8f5ec}.amap-live-canvas{height: min(55vh,520px);min-height:320px;width:100%}.amap-toolbar{position:absolute;left:10px;top:12px;display:flex;gap:8px;max-width:calc(100% - 20px)}.amap-status,.amap-help{padding:8px 12px;margin:0;font-size:13px;color:#4c6157}.amap-location-card,.amap-preview{padding:12px;border-top:1px solid #dfd7c3;overflow-wrap:anywhere}.amap-location-card p,.amap-preview p{font-size:13px;margin:6px 0}.amap-location-card small{display:block}.amap-preview label{display:flex;align-items:center;gap:12px;margin:10px 0}.amap-preview select{max-width:150px;padding:8px}.amap-route-actions{display:flex;gap:8px;flex-wrap:wrap}.amap-location-card .button{margin:6px 4px 0 0}.amap-workspace .button{white-space:normal}.amap-workspace :deep(.amap-place-label){color:#294c42;background:#fffdf6;padding:4px 7px;border:1px solid #c6b886;border-radius:6px;white-space:nowrap}
</style>
