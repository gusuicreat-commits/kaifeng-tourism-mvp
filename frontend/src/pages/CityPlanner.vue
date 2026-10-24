<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue'
import {useRouter} from 'vue-router'
import {api,mutate} from '../api'
import {cityApi,emptyCityDraft,type CityTripState} from '../cityApi'
import {journey,loadJourney,clock,localDate,nameOf} from '../journey'
import {explorationApi,type Itinerary} from '../explorationApi'

const router=useRouter(),day=ref(''),start=ref('17:30'),end=ref('22:00'),busy=ref(false),error=ref('')
const origin=ref('pl_mvp_kaifengfu_meeting'),destination=ref('pl_mvp_kaifengfu_meeting')
const stops=ref([{id:'pl_wb_000003',minutes:90,selected:true},{id:'pl_wb_vendor_000001',minutes:60,selected:true},{id:'pl_wb_000013',minutes:45,selected:true}])
const trip=ref<CityTripState>(),preview=ref<{candidate_id:string;base_version:number;result:{status:string;itineraries:Itinerary[];issues:{message:string}[];warnings:string[]}}>(),selected=ref(0)
const places=computed(()=>journey.catalog.filter(x=>x.record.kind==='place'&&x.ready&&!x.record.parent_place_id))
const chosen=computed(()=>preview.value?.result.itineraries[selected.value])
watch([day,start,end,origin,destination,stops],()=>{preview.value=undefined},{deep:true})
onMounted(async()=>{await loadJourney(true);day.value=journey.catalog.filter(x=>x.record.kind==='open_rule'&&x.record.business_date&&x.record.business_date>=localDate()).map(x=>x.record.business_date!).sort()[0]||localDate()})
async function plan(){
 if(busy.value)return
 busy.value=true;error.value='';preview.value=undefined
 try{
  if(!day.value||end.value<=start.value||!stops.value.some(s=>s.selected))throw new Error('请选择日期、有效时间和至少一个地点。')
  await api.memorySession()
  if(!trip.value)trip.value=await cityApi.create(emptyCityDraft())
  const goals=stops.value.filter(s=>s.selected).map(s=>({goal_id:'visit:'+s.id,target_kind:'place',target_id:s.id,kind:s.id.includes('vendor')?'dining':'visit',required:true,duration_seconds:s.minutes*60}))
  preview.value=await mutate(`/city/v1/trips/${trip.value.id}/engine/preview`,{expected_version:trip.value.version,requirements:{business_date:day.value,starts_at:`${day.value}T${start.value}:00+08:00`,ends_at:`${day.value}T${end.value}:00+08:00`,origin_place_id:origin.value,destination_place_id:destination.value,modes:['walking'],goals}},'POST',90000)
  selected.value=0
  if(preview.value?.result.status!=='feasible')error.value=preview.value?.result.issues.map(i=>i.message).join('；')||'当前时间或资料不足，请调整后重试。'
 }catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
async function confirm(){
 if(!trip.value||!preview.value||busy.value)return
 busy.value=true;error.value=''
 try{await explorationApi.confirm(trip.value.id,preview.value.base_version,preview.value.candidate_id,selected.value);await loadJourney(true);await router.push('/city-trip/'+trip.value.id)}
 catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
</script>
<template><div class="page city-planner">
 <header><span class="tiny-caption">开封夜游</span><h1>安排这趟行程</h1><p>开封府、第一楼寺后街店、鼓楼夜市。按你的时间安排，路程由高德估算。</p></header>
 <form @submit.prevent="plan"><fieldset :disabled="busy"><legend>出发与返回</legend><div class="planner-grid"><label>游玩日期<input v-model="day" type="date" :min="localDate()" required></label><label>出发时间<input v-model="start" type="time" required></label><label>最晚返回<input v-model="end" type="time" required></label></div><label>起点<select v-model="origin"><option v-for="p in places" :key="p.record.fact_id" :value="p.record.fact_id">{{p.record.name}}</option></select></label><label>返程地点<select v-model="destination"><option v-for="p in places" :key="p.record.fact_id" :value="p.record.fact_id">{{p.record.name}}</option></select></label></fieldset>
 <fieldset :disabled="busy"><legend>想去哪里</legend><div v-for="s in stops" :key="s.id" class="planner-stop"><label><input v-model="s.selected" type="checkbox">{{nameOf(s.id)}}</label><label>停留<input v-model.number="s.minutes" type="number" min="5" max="240" required>分钟</label></div></fieldset>
 <p class="pending-copy">已采用团队资料与队员回填。门票需自行购买；夜市闭市时间尚未明确，营业、入口与菜品供应请以现场为准。</p>
 <button type="submit" class="button primary" :disabled="busy">{{busy?'正在查询高德并安排行程…':trip?'重新计算行程':'创建新行程并试排'}}</button></form>
 <p v-if="error" role="alert" class="error-state">{{error}}</p>
 <section v-if="preview?.result.status==='feasible'" class="planner-results" aria-label="行程候选"><h2>选一份合适的安排</h2><div class="planner-options"><button v-for="(p,i) in preview.result.itineraries" :key="i" class="button secondary" :aria-pressed="selected===i" :disabled="busy" @click="selected=i">方案 {{i+1}} · {{clock(p.returns_at)}}返回</button></div><ol v-if="chosen"><li v-for="a in chosen.activities" :key="a.goal_id"><time>{{clock(a.starts_at)}}–{{clock(a.departs_at)}}</time><strong>{{nameOf(a.place_id)}}</strong></li></ol><p v-if="chosen">步行约{{Math.round(chosen.walking_distance_m)}}米 · 转场合计{{Math.ceil(chosen.travel_seconds/60)}}分钟，已计预留时间</p><p class="pending-copy">这份安排尚未保存，确认后成为你的行程。</p><button class="button primary" :disabled="busy" @click="confirm">确认并保存这份行程</button></section>
</div></template>
<style scoped>
.city-planner{max-width:800px}.city-planner header{margin:12px 0 28px}.city-planner h1,.city-planner h2{font-family:'Songti SC','STSong',serif}.city-planner header p{line-height:1.8;color:#77694e}fieldset{border:1px solid #d9cbb5;border-radius:3px 20px 3px 20px;padding:20px;margin:20px 0;background:#fffaf0}legend{font-weight:600;padding:0 8px}label{display:flex;align-items:center;gap:10px;margin:10px 0;min-width:0}input,select{font:inherit;min-height:44px;padding:8px;border:1px solid #cbbda4;background:#fffcf5;border-radius:6px;min-width:0}select{flex:1;max-width:100%}.planner-grid{display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px}.planner-grid label{flex-direction:column;align-items:stretch}.planner-stop{display:flex;justify-content:space-between;align-items:center;gap:12px;border-bottom:1px solid #e8ddca}.planner-stop input[type=number]{width:72px}.planner-stop input[type=checkbox]{min-height:20px;width:20px}.planner-results{margin-top:30px;padding-top:22px;border-top:1px solid #d9cbb5}.planner-options{display:flex;gap:12px;flex-wrap:wrap}.planner-options [aria-pressed=true]{background:#345944;color:white}.planner-results ol{padding:0;list-style:none}.planner-results li{display:flex;gap:20px;align-items:center;padding:18px 0;border-bottom:1px solid #e5d7c0}.planner-results time{white-space:nowrap;font-variant-numeric:tabular-nums}.pending-copy{line-height:1.8}button{min-height:44px}@media(max-width:550px){.planner-grid{grid-template-columns:1fr 1fr}.planner-grid label:first-child{grid-column:1/-1}fieldset{padding:14px}.planner-stop{align-items:flex-start;flex-direction:column;gap:0}.planner-stop label{margin:8px 0}.planner-results li{align-items:flex-start;flex-direction:column;gap:6px}}
</style>
