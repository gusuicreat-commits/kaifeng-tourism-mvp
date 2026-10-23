<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,reactive,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ArrowRight,Check,RefreshCw,Send} from 'lucide-vue-next'
import {api} from '../api'
import type {Crowds,Interest,Network,Plan,Scenic,ScheduleFact} from '../types'
import {interestLabels} from '../types'
import {placeName} from '../presentation'
import {basicQuestion,basicsError,changeSite,complete,createInterview,fromPlan,nextStep,questions,toPlan} from '../planningWorkflow'
import type {QuestionStep,Step} from '../planningWorkflow'
import InternalMap from './InternalMap.vue'
import type {ChatResponse,MemoryTrip} from '../memoryTypes'
import {pendingVoiceText,memoryEpoch} from '../voiceDraft'

const route=useRoute(),router=useRouter()
const state=reactive(createInterview()),d=state.draft
const trip=ref<MemoryTrip>(),memoryBusy=ref(false),memoryError=ref(''),chatResponse=ref<ChatResponse>(),inputChannel=ref<'text'|'voice'>('text')
const currentRoute=ref<Plan>()
const historyEnabled=ref(true)
const scenicList=ref<Scenic[]>([]),network=ref<Network>(),sessions=ref<ScheduleFact[]>([]),crowds=ref<Crowds>()
const loading=ref(true),ready=ref(false),networkBusy=ref(false),scheduleBusy=ref(false),busy=ref(false),aiBusy=ref(false)
const error=ref(''),networkError=ref(''),scheduleHint=ref(''),notice=ref(''),aiHint=ref(''),input=ref('')
const editing=ref<QuestionStep|null>(null),questionElement=ref<HTMLElement>(),history=ref<{role:'assistant'|'user';text:string}[]>([])
const selected=computed(()=>scenicList.value.find(s=>s.id===d.scenic_id))
const step=computed<Step>(()=>editing.value||nextStep(state))
const steps:Step[]=['basics','interests','pace','review']
const question=computed(()=>step.value==='basics'?basicQuestion(d,selected.value?.name):questions[step.value].question)
const poi=computed(()=>network.value?.features.filter(f=>f.geometry.type==='Point'&&f.properties.kind==='poi')||[])
const knownPoiIds=computed(()=>new Set(poi.value.map(p=>p.properties.id)))
const missingPoi=computed(()=>network.value?d.must_visit.filter(id=>!knownPoiIds.value.has(id)):[])
const missingSessions=computed(()=>!scheduleBusy.value&&scheduleHint.value!=='演出日程暂不可用，请稍后重试；未核实场次不作观看承诺。'?d.must_attend.filter(id=>!sessions.value.some(s=>s.id===id)):[])
const targetTime=computed(()=>!basicsError(d)?`${d.visit_date}T${d.visit_time}:00+08:00`:undefined)
const modeLabels={preference:'按兴趣安排',shortest:'少走路',quiet:'尽量避拥挤'}
const walkingLabels={relaxed:'轻松慢走',normal:'正常步速',brisk:'轻快步行'}
function proposalValue(key:string,value:unknown):string{
 if(Array.isArray(value))return value.map(v=>key==='interests'?interestLabels[v as Interest]:key==='must_visit'?placeName(poi.value.find(p=>p.properties.id===v)?.properties.name)||'待核对地点':sessions.value.find(s=>s.id===v)?.name||'待核对场次').join('、')||'不限'
 if(key==='mode')return modeLabels[value as keyof typeof modeLabels]
 if(key==='walking')return walkingLabels[value as keyof typeof walkingLabels]
 if(key==='visit_start')return new Date(String(value)).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
 return String(value)+(key==='available_minutes'?' 分钟':'')
}
const interestSummary=computed(()=>d.interests.map(i=>interestLabels[i]).join('、')||'不限兴趣')
const stopSummary=computed(()=>d.must_visit.map(id=>placeName(poi.value.find(p=>p.properties.id===id)?.properties.name)||id).join('、')||'没有指定必去点')
const paceSummary=computed(()=>`${modeLabels[d.mode||'preference']} · ${walkingLabels[d.walking||'normal']}`)
const crowdHint=computed(()=>crowds.value?.nodes.some(n=>!n.is_simulated&&n.level!=null)?'将结合点位观测或历史参考安排，未知点位仍保留未知。':'暂无客流参考，会先按兴趣和步行安排。')
let networkVersion=0,scheduleVersion=0,crowdVersion=0,bootstrapVersion=0,disposed=false
let aiController:AbortController|undefined

function applyTrip(value:MemoryTrip){trip.value=value;Object.assign(d,value.interview.draft);Object.assign(state.answered,value.interview.answered)}
function snapshot(){return JSON.parse(JSON.stringify(state))}
async function saveDraft(){
 const draft=snapshot()
 const same=trip.value&&Object.keys(draft.draft).every(k=>JSON.stringify(draft.draft[k])===JSON.stringify((trip.value!.interview.draft as unknown as Record<string,unknown>)[k]))&&Object.keys(draft.answered).every(k=>draft.answered[k]===(trip.value!.interview.answered as unknown as Record<string,unknown>)[k])
 if(same&&!['COMPLETED','CANCELLED','ARCHIVED'].includes(trip.value!.status))return
 memoryBusy.value=true;memoryError.value=''
 try{
  if(!trip.value||['COMPLETED','CANCELLED','ARCHIVED'].includes(trip.value.status)||(trip.value.constraints&&trip.value.interview.draft.scenic_id!==d.scenic_id)){trip.value=await api.createTrip(snapshot());currentRoute.value=undefined}
  else trip.value=await api.saveDraft(trip.value,snapshot())
  chatResponse.value=undefined
 }catch(e){memoryError.value=(e as Error).message;throw e}
 finally{memoryBusy.value=false}
}
function takeVoice(){if(pendingVoiceText.value){input.value=pendingVoiceText.value;pendingVoiceText.value='';inputChannel.value='voice';aiHint.value=''}}
watch(pendingVoiceText,takeVoice)
async function focusQuestion(){await nextTick();questionElement.value?.focus({preventScroll:true})}
watch(step,()=>{error.value='';void focusQuestion()})
function clock(value:string){return new Date(value).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false})}
function dateChoice(offset:number){const now=new Date();now.setUTCDate(now.getUTCDate()+offset);d.visit_date=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(now)}
async function loadNetwork(){
 const id=d.scenic_id,v=++networkVersion;network.value=undefined;networkError.value='';networkBusy.value=false
 if(!id)return
 networkBusy.value=true
 try{const n=await api.network(id);if(!disposed&&v===networkVersion)network.value=n}
 catch(e){if(!disposed&&v===networkVersion)networkError.value=(e as Error).message}
 finally{if(!disposed&&v===networkVersion)networkBusy.value=false}
}
async function loadSchedules(){
 const v=++scheduleVersion;sessions.value=[];scheduleHint.value='';scheduleBusy.value=false
 if(!d.scenic_id||!/^\d{4}-\d{2}-\d{2}$/.test(d.visit_date))return
 scheduleBusy.value=true
 try{const r=await api.schedules(d.scenic_id,d.visit_date);if(!disposed&&v===scheduleVersion){sessions.value=r.items.filter(f=>f.kind==='show'&&!f.is_simulated);if(!sessions.value.length)scheduleHint.value='暂无已核验场次，可以先安排场地游览。'}}
 catch{if(!disposed&&v===scheduleVersion)scheduleHint.value='演出日程暂不可用，请稍后重试；未核实场次不作观看承诺。'}
 finally{if(!disposed&&v===scheduleVersion)scheduleBusy.value=false}
}
async function loadCrowds(){
 const v=++crowdVersion;crowds.value=undefined
 if(!targetTime.value)return
 try{const c=await api.crowds(d.scenic_id,'reported',Number(d.visit_time.slice(0,2)),targetTime.value);if(!disposed&&v===crowdVersion)crowds.value=c}catch{/* Optional evidence; no invented low crowd. */}
}
async function bootstrap(){
 const v=++bootstrapVersion;loading.value=true;error.value='';ready.value=false
 try{
  const list=(await api.scenics()).items.filter(s=>s.internal_map)
  if(disposed||v!==bootstrapVersion)return
  scenicList.value=list
  const session=await api.memorySession()
  historyEnabled.value=session.history_enabled
  if(disposed||v!==bootstrapVersion)return
  try{sessionStorage.removeItem('bianyou:planning-interview:v1')}catch{}
  if(typeof route.query.plan==='string'){
   const saved=await api.getPlan(route.query.plan)
   if(disposed||v!==bootstrapVersion)return
   if(saved.trip_id)applyTrip(await api.activateTrip(saved.trip_id))
   else applyTrip(await api.createTrip(fromPlan(saved.request)))
   notice.value='已恢复该行程最新的时间、兴趣和必去要求，可以继续修改。'
  }else{
   applyTrip(session.active_trip_id?await api.memoryTrip(session.active_trip_id):await api.createTrip(createInterview()))
   if(typeof route.query.scenic==='string'&&list.some(s=>s.id===route.query.scenic))changeSite(state,route.query.scenic)
  }
  if(disposed||v!==bootstrapVersion)return
  currentRoute.value=trip.value?.route_id?await api.getPlan(trip.value.route_id):undefined
  if(disposed||v!==bootstrapVersion)return
  const context=await api.memoryContext(trip.value!.id)
  if(disposed||v!==bootstrapVersion)return
  history.value=context.recent_messages.map(m=>({role:m.role,text:m.text}))
  if(d.scenic_id&&!list.some(s=>s.id===d.scenic_id)){changeSite(state,'');notice.value='原景区暂不支持内部规划，请重新选择。'}
  await Promise.all([loadNetwork(),loadSchedules()])
  if(disposed||v!==bootstrapVersion)return
  if(missingPoi.value.length||missingSessions.value.length){state.answered.interests=false;notice.value='部分原地点或场次已不可用，请重新核对。'}
  ready.value=true;void loadCrowds();takeVoice()
 }catch(e){if(!disposed&&v===bootstrapVersion)error.value=(e as Error).message}
 finally{if(!disposed&&v===bootstrapVersion)loading.value=false}
}
onMounted(()=>{void bootstrap()})
watch(()=>route.query.plan,()=>{if(ready.value)void bootstrap()})
watch(memoryEpoch,()=>{bootstrapVersion++;networkVersion++;scheduleVersion++;crowdVersion++;aiController?.abort();Object.assign(d,createInterview().draft);Object.assign(state.answered,createInterview().answered);trip.value=undefined;currentRoute.value=undefined;history.value=[];input.value='';chatResponse.value=undefined;network.value=undefined;sessions.value=[];ready.value=false;loading.value=false;error.value='本次记录已删除，请重新加载开始新会话。'})
watch(()=>route.query.scenic,value=>{if(ready.value&&typeof value==='string'&&scenicList.value.some(s=>s.id===value))chooseSite(value)})
watch(()=>d.scenic_id,()=>{if(ready.value){void loadNetwork();void loadSchedules()}})
watch(()=>d.visit_date,(value,old)=>{
 if(!ready.value||value===old)return
 if(d.must_attend.length){d.must_attend=[];state.answered.interests=false;notice.value='日期已变，原必看场次已清除，请在下一步重新选择。'}
 void loadSchedules()
})
watch(targetTime,()=>{if(ready.value)void loadCrowds()})
onBeforeUnmount(()=>{disposed=true;networkVersion++;scheduleVersion++;crowdVersion++;bootstrapVersion++;aiController?.abort()})
function chooseSite(id:string){if(id!==d.scenic_id&&d.must_visit.length)notice.value='景区已变，原必去点已清除，请重新选择。';changeSite(state,id)}
function toggleInterest(id:Interest){d.interests=d.interests.includes(id)?d.interests.filter(i=>i!==id):[...d.interests,id]}
function togglePoi(id:string){if(step.value!=='interests'||!knownPoiIds.value.has(id))return;d.must_visit=d.must_visit.includes(id)?d.must_visit.filter(n=>n!==id):[...d.must_visit,id]}
function summarize(s:QuestionStep){return s==='basics'?`${selected.value?.name}，${d.visit_date} ${d.visit_time} 入园，游玩 ${d.available_minutes} 分钟。`:s==='interests'?`${interestSummary.value}；${stopSummary.value}${d.must_attend.length?'；必看 '+d.must_attend.length+' 个场次':''}。`:paceSummary.value+'。'}
async function advance(skip=false){
 const current=step.value
 if(current==='review')return
 error.value=''
 if(current==='basics'&&!scenicList.value.some(s=>s.id===d.scenic_id)){error.value='请选择当前支持规划的景区。';return}
 if(current==='interests'&&!skip&&(missingPoi.value.length||missingSessions.value.length)){error.value='请取消已失效的地点或场次，再继续。';return}
 if(current==='pace'&&!skip&&(!d.mode||!d.walking)){error.value='请选好路线重点和步行节奏，或选择按常规节奏安排。';return}
 if(skip&&current==='interests'){d.interests=[];d.must_visit=[];d.must_attend=[]}
 const problem=complete(state,current)
 if(problem){error.value=problem;return}
 try{await saveDraft()}catch{return}
 history.value.push({role:'user',text:summarize(current)})
 editing.value=null;void focusQuestion()
}
function edit(s:QuestionStep){editing.value=s;error.value='';void focusQuestion()}
async function restart(){
 memoryBusy.value=true;error.value=''
 try{applyTrip(await api.createTrip(createInterview()));currentRoute.value=undefined;history.value=[];editing.value=null;notice.value='';aiHint.value='';chatResponse.value=undefined;memoryError.value='';await router.replace({path:'/assistant'})}
 catch(e){error.value=(e as Error).message}finally{memoryBusy.value=false}
}
async function parseText(){
 if(!input.value.trim()||aiBusy.value)return
 const id=d.scenic_id,text=input.value.trim();aiBusy.value=true;aiHint.value='';aiController=new AbortController()
 try{
  await saveDraft()
  const response=await api.chat(trip.value!,text,inputChannel.value)
  if(disposed||id!==d.scenic_id||aiController.signal.aborted)return
  chatResponse.value=response;aiHint.value=response.reply
  history.value.push({role:'user',text},{role:'assistant',text:response.reply});input.value='';inputChannel.value='text'
 }catch(e){if(!disposed&&!aiController.signal.aborted)aiHint.value=(e as Error).message+' 也可以继续用选项回答。'}
 finally{if(!disposed)aiBusy.value=false}
}
async function confirmProposal(){
 if(!trip.value||!chatResponse.value?.proposal_id)return
 memoryBusy.value=true;memoryError.value=''
 try{const result=await api.confirm(trip.value,chatResponse.value.proposal_id);if(disposed)return;applyTrip(result.trip);chatResponse.value=undefined;editing.value=null;aiHint.value=result.plan?'路线已提交更新。':'已填入需求卡，请核对后生成路线。';if(result.plan)await router.push('/result/'+result.plan.id)}
 catch(e){memoryError.value=(e as Error).message}finally{memoryBusy.value=false}
}
async function submit(){
 error.value=''
 if(step.value!=='review'||nextStep(state)!=='review'){error.value='请先完成当前问题。';return}
 if(networkBusy.value||!network.value){error.value=networkError.value||'路网尚未就绪，请稍后重试。';return}
 if(missingPoi.value.length||missingSessions.value.length){editing.value='interests';error.value='请核对已失效的地点或场次。';return}
 busy.value=true
 try{await saveDraft();const result=await api.planTrip(trip.value!,toPlan(state));if(!disposed&&result.plan){trip.value=result.trip;await router.push('/result/'+result.plan.id)}}
 catch(e){if(!disposed)error.value=(e as Error).message}
 finally{if(!disposed)busy.value=false}
}
</script>

<template>
 <div v-if="loading" class="state" role="status">正在准备你的游览安排…</div>
 <div v-else-if="!ready" class="error-state" role="alert">{{error}} <button type="button" @click="bootstrap">重新加载</button></div>
 <div v-else class="guided-layout" :class="{'review-step':step==='review'}">
  <section class="interview-panel panel" aria-label="小汴梁行程问答">
   <div class="interview-header"><img src="/images/assistant/xiaobianliang-character.png" alt="小汴梁主题形象"><div><h1>一起安排这一程</h1><p>告诉我你的安排，我来串起路线。</p></div><button type="button" class="interview-reset" :disabled="busy||aiBusy||memoryBusy" @click="restart"><RefreshCw :size="14"/>重新开始</button></div>
   <ol class="interview-progress" aria-label="规划进度"><li v-for="(s,i) in steps" :key="s" :aria-current="step===s?'step':undefined" :class="{current:step===s,done:s!=='review'&&state.answered[s]}"><span>{{i+1}}</span>{{questions[s].title}}</li></ol>
   <div v-if="history.length" class="interview-history"><details><summary>已告诉小汴梁的安排</summary><p v-for="(m,i) in history" :key="i">{{m.text}}</p></details></div>
   <div class="interview-question" aria-live="polite"><span class="assistant-avatar">汴</span><h2 ref="questionElement" tabindex="-1">{{question}}</h2></div>
   <p v-if="notice" class="interview-notice">{{notice}}</p>
   <p class="memory-status" aria-live="polite">{{memoryBusy?'正在保存…':memoryError?'保存未完成，原路线仍保留':'已连接本次会话'}} · <RouterLink to="/memory">查看与管理记录</RouterLink></p>
   <div v-if="memoryError" class="error-state" role="alert">{{memoryError}} <button type="button" @click="bootstrap">重新加载已保存状态</button></div>
   <form class="interview-answer" novalidate @submit.prevent="step==='review'?submit():advance()">
    <fieldset :disabled="busy||aiBusy||memoryBusy">
     <template v-if="step==='basics'">
      <div class="interview-field"><label>先选一个景区</label><div class="interview-choices" role="group" aria-label="游玩景区"><button v-for="s in scenicList" :key="s.id" type="button" :aria-pressed="d.scenic_id===s.id" @click="chooseSite(s.id)">{{s.name}}<Check v-if="d.scenic_id===s.id" :size="14"/></button></div><p v-if="!scenicList.length">暂时没有支持内部规划的景区。</p></div>
      <div class="interview-time-fields"><div><label for="trip-date">游玩日期</label><input id="trip-date" v-model="d.visit_date" type="date"><div class="interview-shortcuts"><button type="button" @click="dateChoice(0)">今天</button><button type="button" @click="dateChoice(1)">明天</button></div></div><div><label for="trip-time">入园时间</label><input id="trip-time" v-model="d.visit_time" type="time"></div><div><label for="trip-duration">游玩时长（分钟）</label><input id="trip-duration" v-model.number="d.available_minutes" type="number" min="15" max="720" placeholder="例如 180"><div class="interview-shortcuts"><button v-for="n in [60,120,180,240]" :key="n" type="button" @click="d.available_minutes=n">{{n/60}}小时</button></div></div></div>
     </template>
     <template v-else-if="step==='interests'">
      <div class="interview-field"><label>你更喜欢什么？</label><div class="interview-choices" role="group" aria-label="兴趣偏好"><button v-for="(label,key) in interestLabels" :key="key" type="button" :aria-pressed="d.interests.includes(key)" @click="toggleInterest(key)">{{label}}<Check v-if="d.interests.includes(key)" :size="14"/></button></div></div>
      <div v-if="networkBusy" class="interview-notice" role="status">正在查看景区地点…</div><div v-if="networkError" class="error-state">{{networkError}} <button type="button" @click="loadNetwork">重试地点加载</button></div>
      <div class="interview-field"><label>有没有一定想去的地方？<small>可选，也可以展开地图点选。</small></label><div class="interview-checks"><label v-for="p in poi" :key="p.properties.id"><input v-model="d.must_visit" type="checkbox" :value="p.properties.id">{{placeName(p.properties.name)}}</label><label v-for="id in missingPoi" :key="id"><input v-model="d.must_visit" type="checkbox" :value="id">已失效地点 {{id}}（取消勾选可移除）</label></div></div>
      <div v-if="sessions.length||d.must_attend.length" class="interview-field"><label>有一定想看的场次吗？</label><div class="interview-checks"><label v-for="s in sessions" :key="s.id"><input v-model="d.must_attend" type="checkbox" :value="s.id">{{s.name}} {{clock(s.starts_at)}}–{{clock(s.ends_at)}}</label><label v-for="id in missingSessions" :key="id"><input v-model="d.must_attend" type="checkbox" :value="id">已失效场次 {{id}}（取消勾选可移除）</label></div></div>
      <p v-if="scheduleBusy" class="schedule-hint">正在核对当日场次…</p><p v-else-if="scheduleHint" class="schedule-hint">{{scheduleHint}}</p>
     </template>
     <template v-else-if="step==='pace'">
      <div class="interview-field"><label>这一程更看重什么？</label><div class="interview-choices" role="group" aria-label="路线重点"><button v-for="(label,key) in modeLabels" :key="key" type="button" :aria-pressed="d.mode===key" @click="d.mode=key">{{label}}</button></div><p v-if="d.mode==='quiet'" class="preference-hint">{{crowdHint}}</p></div>
      <div class="interview-field"><label>想按什么节奏走？</label><div class="interview-choices" role="group" aria-label="步行节奏"><button v-for="(label,key) in walkingLabels" :key="key" type="button" :aria-pressed="d.walking===key" @click="d.walking=key">{{label}}</button></div></div>
     </template>
     <template v-else><p class="interview-confirmation">需求卡中的安排会交给路线算法计算。预算包含步行、停留、已知候场和出园，未包含排队。</p><p v-if="d.mode==='quiet'" class="preference-hint">{{crowdHint}}</p></template>
     <div v-if="error" class="error-state" role="alert">{{error}}</div>
     <div class="interview-actions"><button v-if="step!=='review'" class="button primary" type="submit">{{step==='basics'?'记下了，继续':step==='interests'?'继续，安排节奏':'查看我的需求'}}<ArrowRight :size="16"/></button><button v-if="step==='interests'" class="button secondary" type="button" @click="advance(true)">暂时没有特别要求</button><button v-if="step==='pace'" class="button secondary" type="button" @click="advance(true)">按常规节奏安排</button><button v-if="step==='review'" class="button primary" type="submit" :disabled="busy||networkBusy||!network"><span v-if="busy" class="spinner"/>{{busy?'正在计算路线…':'确认，生成我的路线'}}<ArrowRight v-if="!busy" :size="16"/></button></div>
    </fieldset>
   </form>
   <form class="interview-composer" @submit.prevent="parseText"><label for="interview-text">也可以直接告诉小汴梁</label><p class="memory-retention-notice">{{historyEnabled?'发送的对话用于接续本次游览，24小时不活跃后清理，可随时在记录管理中关闭。':'当前不保留对话，已确认的行程仍会保存。'}}</p><div><input id="interview-text" v-model="input" :disabled="busy||aiBusy||memoryBusy" maxlength="2000" placeholder="例如：11月1日14点入园，玩两小时，喜欢演出"><button type="submit" class="button secondary" :disabled="busy||aiBusy||memoryBusy||!input.trim()" aria-label="发送安排">{{aiBusy?'理解中…':'发送'}}<Send :size="15"/></button></div></form><p v-if="aiHint" class="interview-notice" role="status">{{aiHint}}</p>
   <div v-if="chatResponse?.proposal_id" class="memory-proposal"><strong>待你确认的修改</strong><p>{{chatResponse.candidate?.operation==='remove_stop'?'取消游览点：'+(placeName(poi.find(p=>p.properties.id===chatResponse?.candidate?.poi_id)?.properties.name)||'待核对地点'):'只影响本次行程，不保存为长期偏好。'}}</p><dl v-if="chatResponse.candidate?.operation==='update_constraints'"><div v-for="(value,key) in chatResponse.candidate.patch" :key="key"><dt>{{({available_minutes:'游玩分钟',interests:'兴趣',walking:'步行节奏',mode:'路线重点',must_visit:'必去点',must_attend:'必看场次',visit_start:'入园时间'} as Record<string,string>)[key]||key}}</dt><dd>{{proposalValue(key,value)}}</dd></div></dl><button type="button" class="button primary" :disabled="busy||aiBusy||memoryBusy" @click="confirmProposal">{{trip?.route_id?'确认调整并重新规划':'确认填入需求卡'}}</button><button type="button" class="button secondary" :disabled="memoryBusy" @click="chatResponse=undefined">暂不修改</button></div>
  </section>
  <aside class="interview-summary panel" aria-label="我的游览需求"><div class="interview-summary-head"><h2>我的游览需求</h2><span>{{step==='review'?'待你确认':'正在整理'}}</span></div><dl><div><dt>基本安排 <button type="button" :disabled="busy||aiBusy||memoryBusy" @click="edit('basics')">修改基本安排</button></dt><dd>{{selected?.name||'待选择景区'}}<br>{{d.visit_date||'日期待定'}} {{d.visit_time||'时间待定'}}<br>{{d.available_minutes?d.available_minutes+' 分钟':'时长待补充'}}</dd></div><div><dt>兴趣与地点 <button type="button" :disabled="busy||aiBusy||memoryBusy||!state.answered.basics" @click="edit('interests')">修改兴趣地点</button></dt><dd>{{state.answered.interests?interestSummary:'待了解你的兴趣'}}<br>{{stopSummary}}<span v-if="d.must_attend.length"><br>必看 {{d.must_attend.length}} 个场次</span></dd></div><div><dt>游玩节奏 <button type="button" :disabled="busy||aiBusy||memoryBusy||!state.answered.basics" @click="edit('pace')">修改游玩节奏</button></dt><dd>{{state.answered.pace?paceSummary:'待选择，也可按常规安排'}}</dd></div></dl>
   <section v-if="currentRoute" class="memory-route-order" aria-label="当前路线顺序"><h3>当前路线 · 第 {{currentRoute.route_version||trip?.route_version}} 版</h3><ol><li v-for="stop in currentRoute.stops.slice(1,-1)" :key="stop.id">{{placeName(stop.name)}}</li></ol><RouterLink :to="'/result/'+currentRoute.id">查看这版路线</RouterLink><p>“第二个点”等指代按以上游览点顺序核对。</p></section>
   <details v-if="network&&state.answered.basics" class="interview-map"><summary>{{step==='interests'?'展开地图，选择必去点':'查看景区示意图'}}</summary><InternalMap :network="network" :crowds="crowds?.nodes" @select="togglePoi"/></details>
   <p v-if="network?.is_simulated" class="interview-footnote">当前为景区示意路网，位置和距离待核实，请以现场指引为准。</p>
   <p v-if="networkError&&step==='review'" class="error-state">{{networkError}} <button type="button" @click="loadNetwork">重试地点加载</button></p>
  </aside>
 </div>
</template>
