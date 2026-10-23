<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {api,ApiError} from '../api'
import {cityApi,emptyCityDraft} from '../cityApi'
import type {CatalogItem,CityAction,CityDraft,CityProposal,CityStop,CityTripState} from '../cityApi'
import {memoryEpoch} from '../voiceDraft'

const route=useRoute(),router=useRouter()
const trip=ref<CityTripState>(),draft=ref<CityDraft>(emptyCityDraft()),catalog=ref<CatalogItem[]>([])
const day=ref(''),start=ref(''),end=ref(''),busy=ref(false),error=ref(''),saved=ref('')
const proposal=ref<Extract<CityProposal,{status:'needs_confirmation'}>>()
const selectedPlace=ref(''),selectedSession=ref(''),kind=ref<CityStop['kind']>('visit'),anchorPlace=ref('')
const completedStop=ref(''),actualStart=ref(''),actualEnd=ref('')
let disposed=false
const places=computed(()=>catalog.value.filter(x=>x.record.kind==='place'))
const readyPlaces=computed(()=>places.value.filter(x=>x.ready))
const foods=computed(()=>catalog.value.filter(x=>x.record.kind==='food'))
const sessions=computed(()=>catalog.value.filter(x=>x.record.kind==='show_session'))
const stopPlaces=computed(()=>places.value.filter(x=>kind.value!=='dining'||x.record.place_type==='restaurant'))
const nextStop=computed(()=>trip.value?.draft.stops.find(s=>s.progress==='pending'))
const terminal=computed(()=>trip.value&&['COMPLETED','CANCELLED','ARCHIVED'].includes(trip.value.status))
const payload=():CityDraft=>({...draft.value,business_date:day.value||null,
 starts_at:day.value&&start.value?`${day.value}T${start.value}:00+08:00`:null,
 ends_at:day.value&&end.value?`${day.value}T${end.value}:00+08:00`:null})
const signature=(d:CityDraft)=>JSON.stringify({...d,starts_at:d.starts_at?Date.parse(d.starts_at):null,ends_at:d.ends_at?Date.parse(d.ends_at):null})
const dirty=computed(()=>!trip.value||signature(payload())!==signature(trip.value.draft))
const names=computed(()=>Object.fromEntries(catalog.value.filter(x=>x.record.name).map(x=>[x.record.fact_id,x.record.name!])))
const name=(id:string)=>names.value[id]||'待核对地点'
const local=(iso:string)=>new Date(iso).toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}).replace(' ','T').slice(0,16)
const time=(iso:string|null)=>iso?local(iso).slice(11):''
const statusLabels={DRAFT:'安排中',ACTIVE:'游玩记录中',PAUSED:'记录已暂停',COMPLETED:'已结束',CANCELLED:'已取消',ARCHIVED:'已归档'}
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value)) as T

function apply(value:CityTripState){trip.value=value;draft.value=clone(value.draft);day.value=value.draft.business_date||'';
 start.value=time(value.draft.starts_at);end.value=time(value.draft.ends_at);proposal.value=undefined;completedStop.value=''}
async function run(work:(epoch:number)=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';saved.value='';const epoch=memoryEpoch.value;
 try{await work(epoch)}catch(e){if(!disposed&&epoch===memoryEpoch.value)error.value=(e as Error).message}finally{if(!disposed)busy.value=false}}
const currentEpoch=(epoch:number)=>!disposed&&epoch===memoryEpoch.value
async function load(){await run(async epoch=>{await api.memorySession();const data=await cityApi.facts();
 const value=route.params.id?await cityApi.get(String(route.params.id)):route.query.new==='1'?null:(await cityApi.current()).trip;
 if(!currentEpoch(epoch))return;catalog.value=data.items;if(value)apply(value);else {trip.value=undefined;draft.value=emptyCityDraft();day.value='';start.value='';end.value=''}})}
async function act(action:CityAction){await run(async epoch=>{
 if(!trip.value)return;
 try{const value=await cityApi.update(trip.value,action);if(currentEpoch(epoch)){apply(value);saved.value='已保存当前安排。'}}
 catch(e){if(!(e instanceof ApiError)||e.code!=='CONFIRMATION_REQUIRED')throw e;
  const candidate=await cityApi.propose(trip.value,action);if(!currentEpoch(epoch))return;
  if(candidate.status==='needs_confirmation')proposal.value=candidate;else error.value=candidate.message}
 })}
async function save(){if(trip.value){await act({operation:'save_draft',draft:payload()});return}
 await run(async epoch=>{const value=await cityApi.create(payload());if(!currentEpoch(epoch))return;apply(value);saved.value='安排已保存，刷新后可以继续。';await router.replace('/city-trip/'+value.id)})}
async function accept(){await run(async epoch=>{if(!trip.value||!proposal.value)return;const value=await cityApi.confirm(trip.value,proposal.value.proposal_id);
 if(currentEpoch(epoch)){apply(value);saved.value='修改已确认并保存。'}})}
function add(){error.value='';saved.value='';let place=selectedPlace.value,session:string|null=null;
 if(kind.value==='show'){const item=sessions.value.find(x=>x.record.fact_id===selectedSession.value);if(!item?.record.stage_place_id){error.value='请选择已有的具体场次。';return}place=item.record.stage_place_id;session=item.record.fact_id}
 if(!place){error.value='请先选择已有地点。';return}
 draft.value.stops.push({stop_id:crypto.randomUUID(),place_id:place,kind:kind.value,session_id:session,locked:false,
  duration_seconds:kind.value==='show'?null:kind.value==='dining'?1800:900,progress:'pending',actual_start:null,actual_end:null});proposal.value=undefined}
function remove(index:number){draft.value.stops.splice(index,1);proposal.value=undefined}
function newDraft(){trip.value=undefined;draft.value=emptyCityDraft();day.value='';start.value='';end.value='';proposal.value=undefined;saved.value='';router.push({path:'/city-trip',query:{new:'1'}})}
async function anchor(){if(!anchorPlace.value)return;await act({operation:'set_anchor',anchor:{place_id:anchorPlace.value,confirmed_at:new Date().toISOString()}})}
async function complete(){if(!completedStop.value||!actualStart.value||!actualEnd.value){error.value='请填写实际开始与结束时间。';return}
 await act({operation:'mark_completed',stop_id:completedStop.value,actual_start:actualStart.value+':00+08:00',actual_end:actualEnd.value+':00+08:00'})}
watch(memoryEpoch,()=>{trip.value=undefined;draft.value=emptyCityDraft();proposal.value=undefined;catalog.value=[];day.value='';start.value='';end.value='';saved.value='';error.value='记录已删除，请重新开始。'})
watch(()=>[route.params.id,route.query.new],()=>{if(!busy.value&&String(route.params.id||'')!==String(trip.value?.id||''))void load()})
onMounted(load);onBeforeUnmount(()=>{disposed=true})
</script>

<template><div class="page city-trip-page">
 <RouterLink to="/memory" class="back-link">返回游览记录</RouterLink><h1>城市行程安排</h1>
 <p>先保存想去的地方和用餐安排，资料补齐后再计算路线。本次记录在24小时不活跃后失效，<RouterLink to="/memory">可导出或删除</RouterLink>。</p>
 <p v-if="busy" role="status">正在处理…</p><div v-if="error" class="error-state" role="alert">{{error}} <button :disabled="busy" @click="load">重新加载已保存安排</button></div>
 <p v-if="saved" role="status">{{saved}}</p>
 <p v-if="trip">{{statusLabels[trip.status]}} · 第 {{trip.version}} 版<span v-if="dirty"> · 有未保存的修改</span></p>
 <RouterLink v-if="trip&&!dirty&&!terminal" :to="{path:'/assistant',query:{city_trip:trip.id}}" class="back-link">让小汴梁帮我查询与调整</RouterLink>
 <p v-if="!places.length&&!busy" class="panel">地点与餐饮资料正在核验，当前可以先保存日期和时间。暂不提供城市路线。</p>
 <fieldset class="panel city-fields" :disabled="busy||Boolean(terminal)||Boolean(proposal)"><legend>基本安排</legend>
  <label>游玩日期<input v-model="day" type="date"></label><label>开始时间<input v-model="start" type="time"></label><label>结束时间<input v-model="end" type="time"></label>
  <label>出发地点<select v-model="draft.origin_place_id"><option :value="null">待选择</option><option v-for="p in places" :key="p.record.fact_id" :value="p.record.fact_id">{{p.record.name}}{{p.ready?'':'（待核验）'}}</option></select></label>
  <label>返程终点<select v-model="draft.destination_place_id"><option :value="null">待选择</option><option v-for="p in places" :key="p.record.fact_id" :value="p.record.fact_id">{{p.record.name}}{{p.ready?'':'（待核验）'}}</option></select></label>
  <label>交通方式<select v-model="draft.modes[0]"><option value="walking">步行</option><option value="driving">驾车</option><option value="transit">公共交通</option></select></label>
 </fieldset>
 <fieldset v-if="foods.length" class="panel" :disabled="busy||Boolean(terminal)||Boolean(proposal)"><legend>想吃什么</legend>
  <label v-for="f in foods" :key="f.record.fact_id" class="city-check"><input v-model="draft.dish_interest_ids" type="checkbox" :value="f.record.fact_id">{{f.record.name}}</label><p>菜品兴趣会保留；只有核验门店供应和营业信息后才能安排用餐。</p>
 </fieldset>
 <section class="panel"><h2>想去的活动</h2><p v-if="!draft.stops.length">还没有添加活动。</p>
  <ol class="city-stops"><li v-for="(s,index) in draft.stops" :key="s.stop_id"><strong>{{name(s.place_id)}}</strong><span>{{s.kind==='show'?'演出':s.kind==='dining'?'用餐':s.kind==='rest'?'休息':'游览'}} · {{s.progress==='completed'?'已完成':s.progress==='skipped'?'已跳过':'未完成'}}</span>
   <template v-if="s.progress==='pending'"><label class="city-check"><input v-model="s.locked" type="checkbox" :disabled="busy||Boolean(terminal)||Boolean(proposal)">必须保留</label>
   <label v-if="s.kind!=='show'">停留分钟<input :value="s.duration_seconds?Math.round(s.duration_seconds/60):''" type="number" min="1" max="1440" :disabled="busy||Boolean(terminal)||Boolean(proposal)" @change="s.duration_seconds=Number(($event.target as HTMLInputElement).value)*60||null"></label>
   <button class="button secondary" :disabled="busy||Boolean(terminal)||Boolean(proposal)" @click="remove(index)">移除活动</button></template>
  </li></ol>
  <fieldset v-if="places.length" class="city-fields" :disabled="busy||Boolean(terminal)||Boolean(proposal)"><legend>增加活动</legend>
   <label>活动类型<select v-model="kind"><option value="visit">游览</option><option value="show">演出</option><option value="dining">用餐</option><option value="rest">休息</option></select></label>
   <label v-if="kind==='show'">具体场次<select v-model="selectedSession"><option value="">选择场次</option><option v-for="s in sessions" :key="s.record.fact_id" :value="s.record.fact_id">{{name(s.record.program_id||'')}} {{s.record.starts_at?local(s.record.starts_at).replace('T',' '):''}}{{s.ready?'':'（待核验）'}}</option></select></label>
   <label v-else>活动地点<select v-model="selectedPlace"><option value="">选择地点</option><option v-for="p in stopPlaces" :key="p.record.fact_id" :value="p.record.fact_id">{{p.record.name}}{{p.ready?'':'（待核验）'}}</option></select></label>
   <button class="button secondary" type="button" @click="add">添加到草稿</button>
  </fieldset>
 </section>
 <div v-if="proposal" class="panel memory-proposal"><p>这次修改会影响锁定活动或重要安排。确认后保存修改，路线仍待计算。</p><ul><li v-for="impact in proposal.impacts" :key="impact">{{impact}}</li></ul><button class="button primary" :disabled="busy" @click="accept">确认保存修改</button><button class="button secondary" :disabled="busy" @click="proposal=undefined">取消候选</button></div>
 <div class="interview-actions"><button class="button primary" :disabled="busy||Boolean(terminal)||Boolean(proposal)" @click="save">保存城市安排</button><button v-if="trip" class="button secondary" :disabled="busy" @click="newDraft">另建一次游玩</button></div>
 <section v-if="trip" class="panel"><h2>当前位置与进度</h2><p>{{trip.readiness.notice}}</p>
  <p v-if="trip.readiness.missing_fields.length">基本安排还有信息待补齐。</p><p v-if="trip.readiness.issues.length">部分活动的日期、营业或来源待核对，请勿据此判断能否赶上。</p>
  <p v-if="trip.anchor">最后确认位置：{{name(trip.anchor.place_id)}} · {{local(trip.anchor.confirmed_at).replace('T',' ')}}</p>
  <div v-if="readyPlaces.length&&!terminal" class="city-fields"><label>我在这里<select v-model="anchorPlace" :disabled="busy||dirty"><option value="">选择已核验地点</option><option v-for="p in readyPlaces" :key="p.record.fact_id" :value="p.record.fact_id">{{p.record.name}}</option></select></label><button class="button secondary" :disabled="busy||dirty||!anchorPlace||Boolean(proposal)" @click="anchor">确认当前位置</button></div>
  <div class="interview-actions"><button v-if="trip.status==='DRAFT'" class="button secondary" :disabled="busy||dirty||Boolean(proposal)" @click="act({operation:'transition',status:'ACTIVE'})">开始记录游玩</button><button v-if="trip.status==='ACTIVE'" class="button secondary" :disabled="busy||dirty||Boolean(proposal)" @click="act({operation:'transition',status:'PAUSED'})">暂停记录</button><button v-if="trip.status==='PAUSED'" class="button secondary" :disabled="busy||dirty||Boolean(proposal)" @click="act({operation:'transition',status:'ACTIVE'})">继续记录</button><button v-if="['ACTIVE','PAUSED','DRAFT'].includes(trip.status)" class="button secondary" :disabled="busy||dirty||Boolean(proposal)" @click="act({operation:'transition',status:'CANCELLED'})">取消本次游玩</button><button v-if="trip.status==='ACTIVE'" class="button secondary" :disabled="busy||dirty||Boolean(proposal)" @click="act({operation:'transition',status:'COMPLETED'})">结束本次游玩</button></div>
  <template v-if="trip.status==='ACTIVE'&&nextStop"><p>下一活动：{{name(nextStop.place_id)}}</p><div class="interview-actions"><button class="button secondary" :disabled="busy||dirty||Boolean(proposal)" @click="completedStop=nextStop.stop_id;actualStart='';actualEnd=''">记录下一活动完成</button><button class="button secondary" :disabled="busy||dirty||Boolean(proposal)" @click="act({operation:'skip_stop',stop_id:nextStop.stop_id})">跳过下一活动</button></div>
   <div v-if="completedStop" class="city-fields"><label>实际开始<input v-model="actualStart" type="datetime-local" :disabled="busy"></label><label>实际结束<input v-model="actualEnd" type="datetime-local" :disabled="busy"></label><button class="button secondary" :disabled="busy||dirty||Boolean(proposal)" @click="complete">确认完成记录</button></div>
  </template>
 </section>
</div></template>

<style scoped>
.city-trip-page{max-width:860px;margin-inline:auto}.city-trip-page h1{margin-block:16px}.city-trip-page p{line-height:1.65}.city-trip-page fieldset{min-width:0}.city-trip-page .panel{margin-block:18px}.city-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.city-fields label,.city-stops label{display:grid;gap:7px;min-width:0}.city-fields input,.city-fields select,.city-stops input[type=number]{width:100%;min-width:0;padding:10px;border:1px solid var(--line,#ddd);border-radius:10px;background:white;color:inherit;font:inherit}.city-fields button{align-self:end}.city-trip-page legend{padding:0 6px}.city-stops{padding-left:24px}.city-stops li{display:grid;gap:10px;padding:14px 0;border-bottom:1px solid var(--line,#ddd)}.city-stops span{font-size:14px}.city-check{display:flex!important;gap:8px;align-items:center;margin-block:8px}.city-check input{width:auto}.memory-proposal button{margin:6px}.city-trip-page .interview-actions{flex-wrap:wrap}.city-trip-page button{max-width:100%}@media(max-width:560px){.city-fields{grid-template-columns:minmax(0,1fr)}}
</style>
