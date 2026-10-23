<script setup lang="ts">
import {ref,onMounted,watch,computed} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ArrowLeft,Clock3,Footprints,MapPin,RefreshCw,Check,Copy} from 'lucide-vue-next'
import {api} from '../api'
import type {Plan,Scenic,Network,Crowds} from '../types'
import {category,crowdLabels} from '../types'
import InternalMap from '../components/InternalMap.vue'
import {placeName} from '../presentation'
import {memoryEpoch} from '../voiceDraft'
const route=useRoute(),router=useRouter(),plan=ref<Plan>(),scenic=ref<Scenic>(),network=ref<Network>(),crowds=ref<Crowds>(),busy=ref(true),replanning=ref(false),error=ref(''),copied=ref(false),active=ref('')
const stops=computed(()=>plan.value?.stops.map(s=>({...s,crowd_level:plan.value?.request.dataset==='reported'?s.crowd_level:null,reasons:s.reasons.filter(r=>!/(演示|代价|顺路游览)/.test(r))}))||[])
const strategy=computed(()=>plan.value?.request.mode==='shortest'?'少走路':plan.value?.crowd_optimization_applied?'尽量避拥挤':'兴趣优先')
let version=0
async function load(){
 const v=++version;busy.value=true;error.value='';crowds.value=undefined
 try{
  const p=await api.getPlan(String(route.params.id))
  const[s,n]=await Promise.all([api.scenic(p.scenic_id),api.network(p.scenic_id)])
  if(v!==version)return
  plan.value=p;scenic.value=s;network.value=n
  if(p.crowd_evidence)crowds.value={scenic_id:p.scenic_id,dataset:p.request.dataset,is_simulated:p.request.dataset==='demo',notice:'规划时的客流依据',hour:p.request.hour,nodes:p.crowd_evidence}
  else void api.crowds(p.scenic_id,'reported',p.request.hour).then(c=>{if(v===version)crowds.value=c}).catch(()=>{})
 }catch(e){if(v===version)error.value=(e as Error).message}
 finally{if(v===version)busy.value=false}
}
async function replan(quiet:boolean){if(!plan.value)return;replanning.value=true;error.value='';try{
 const constraints={...plan.value.request,dataset:'reported' as const,crowd_overrides:{},mode:quiet?'quiet' as const:'shortest' as const,avoid_crowds:quiet}
 if(plan.value.trip_id){const trip=await api.memoryTrip(plan.value.trip_id);const result=await api.planTrip({...trip,version:plan.value.trip_version||trip.version},constraints);if(result.plan)await router.replace('/result/'+result.plan.id)}
 else{const p=await api.plan(constraints);await router.replace('/result/'+p.id)}
 }catch(e){error.value=(e as Error).message}finally{replanning.value=false}}
async function copy(){try{await navigator.clipboard.writeText(location.href);copied.value=true;setTimeout(()=>copied.value=false,2000)}catch{error.value='复制不可用，可从地址栏复制本地路线链接。'}}
onMounted(load);watch(()=>route.params.id,load)
watch(memoryEpoch,()=>{version++;plan.value=undefined;network.value=undefined;crowds.value=undefined;busy.value=false;error.value='本次记录已删除，旧路线不再显示。'})
</script>
<template>
 <div class="page result-page"><RouterLink :to="'/assistant'+(plan?'?plan='+plan.id:'')" class="back-link"><ArrowLeft :size="16"/> 调整游玩需求</RouterLink>
 <div v-if="busy" class="state" role="status"><span class="spinner"/>正在展开你的行程…</div><div v-else-if="!plan&&error" class="error-state" role="alert">{{error}} <button @click="load">重试</button></div>
 <template v-if="plan&&!busy"><div class="result-heading"><div><span class="eyebrow">A JOURNEY MADE FOR YOU</span><h1>你的{{scenic?.name}}漫游路线</h1><p>把喜欢的地方串起来，让这一程刚刚好。</p></div><button class="button secondary" @click="copy"><Copy :size="16"/>{{copied?'已复制':'复制路线链接'}}</button></div>
 <div class="route-stats"><div><Clock3/><span><strong>{{plan.total_minutes}}<small>分钟</small></strong><label>预计总耗时</label></span></div><div><Footprints/><span><strong>{{(plan.distance_m/1000).toFixed(2)}}<small>公里</small></strong><label>示意步行 · {{plan.walk_minutes}} 分钟</label></span></div><div><MapPin/><span><strong>{{plan.stops.length-2}}<small>个点位</small></strong><label>游览停留 · {{plan.stay_minutes}} 分钟</label></span></div><div><Check/><span><strong>{{plan.remaining_minutes}}<small>分钟</small></strong><label>预算余量 · 未含排队</label></span></div></div>
 <p class="route-context">{{plan.request.mode==='quiet'&&!plan.crowd_optimization_applied?'暂无客流参考，已按兴趣和步行安排':plan.crowd_optimization_applied?'已结合客流参考调整路线':strategy}} · 候场约 {{plan.wait_minutes||0}} 分钟，未含排队</p>
 <p v-if="plan.explanation" class="route-explanation">{{plan.explanation}}</p><details class="panel route-evidence"><summary>行程依据与提醒</summary><ul><li v-for="w in plan.warnings" :key="w">{{w}}</li></ul></details>
 <div v-if="error" class="error-state" role="alert">{{error}}</div>
 <div class="result-layout"><section class="result-map"><InternalMap v-if="network" :network="network" :route="plan.route_geojson" :crowds="crowds?.nodes" :selected="active" @select="active=$event"/><div class="route-adjust panel"><div><h3>换一种走法？</h3><p>保留时间、兴趣和必去点，重新计算。</p></div><div><button class="button secondary" :disabled="replanning" @click="replan(false)"><Footprints :size="16"/>少走路</button><button class="button secondary" :disabled="replanning" @click="replan(true)"><RefreshCw :size="16"/>{{replanning?'重新规划中…':'尽量避拥挤'}}</button></div></div></section>
 <aside class="panel itinerary"><div class="panel-heading"><h2>一路好风景</h2><span class="pill">游览顺序</span></div><ol><li v-for="(s,i) in stops" :key="s.id" :class="{selected:active===s.id}" @click="active=s.id"><span class="stop-number">{{i===0?'起':i===plan.stops.length-1?'终':i}}</span><div><small>{{s.arrival_time?new Date(s.arrival_time).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}):'第 '+s.arrival_minutes+' 分钟'}} 抵达<span v-if="s.wait_minutes"> · 候场 {{s.wait_minutes}} 分钟</span></small><h3>{{placeName(s.name)}}</h3><p v-if="s.stay_minutes">停留约 {{s.stay_minutes}} 分钟 <span v-if="s.crowd_level!=null" :class="['crowd-tag',category(s.crowd_level)]">拥挤参考：{{crowdLabels[category(s.crowd_level)]}}</span></p><p v-for="r in s.reasons" :key="r" class="reason">{{r}}</p></div></li></ol></aside></div>
 </template></div>
</template>
