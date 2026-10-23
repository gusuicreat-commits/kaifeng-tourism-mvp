<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {api} from '../api'
import {cityAgentApi,type AgentTask,type CityContext,type RouteLocationChoice} from '../cityAgentApi'
import {memoryEpoch,pendingVoiceText} from '../voiceDraft'
import {cityApi} from '../cityApi'
import {journey} from '../journey'
import {loadBasket} from '../journey'
import BasketPlanning from './BasketPlanning.vue'
import type {ObjectRef} from '../basketApi'
import ReferenceRouteCard from './ReferenceRouteCard.vue'

const props=defineProps<{tripId:string;focus?:ObjectRef|null;initialQuestion?:string;planning?:boolean}>()
const showPlanning=ref(!!props.planning),planner=ref<InstanceType<typeof BasketPlanning>>(),planningBusy=ref(false)
const inputBusy=computed(()=>busy.value||planningBusy.value)
const ctx=ref<CityContext|null>(null),text=ref(''),task=ref<AgentTask|null>(null),busy=ref(false),error=ref('')
const messages=ref<{role:string;text:string}[]>([])
const chatScroll=ref<HTMLElement>(),atLatest=ref(true)
const earlier=ref<number|null>(null),historyBusy=ref(false)
function trackScroll(){const el=chatScroll.value;if(el)atLatest.value=el.scrollHeight-el.scrollTop-el.clientHeight<48}
async function scrollLatest(){atLatest.value=true;await nextTick();const el=chatScroll.value;if(el)el.scrollTop=el.scrollHeight}
async function loadEarlier(){
 if(!earlier.value||historyBusy.value)return
 historyBusy.value=true;const guard=memoryEpoch.value,id=props.tripId,el=chatScroll.value
 const height=el?.scrollHeight||0,top=el?.scrollTop||0;atLatest.value=false
 try{const page=await cityAgentApi.messages(id,earlier.value);if(disposed||guard!==memoryEpoch.value||id!==props.tripId)return
  messages.value.unshift(...page.messages.map(m=>({role:m.role==='user'?'你':'小汴梁',text:m.text})));earlier.value=page.before
  await nextTick();if(el)el.scrollTop=top+el.scrollHeight-height
 }catch(e){error.value=e instanceof Error?e.message:'旧记录暂未读到，请重试'}finally{historyBusy.value=false}
}
function takeVoice(){if(pendingVoiceText.value){text.value=[text.value.trim(),pendingVoiceText.value].filter(Boolean).join('\n');pendingVoiceText.value=''}}
watch(pendingVoiceText,takeVoice,{immediate:true})
let epoch=0,disposed=false,resizeObserver:ResizeObserver|undefined
const labels:Record<string,string>={search_locations:'正在查找起点和终点',query_reference_route:'正在查询高德参考路线',search_dining:'正在查找合适商家',query_shows:'正在核对演出时间',verify_travel:'正在验证交通衔接',preview_plan:'正在验证行程安排',understand:'正在理解你的要求'}
const progress=computed(()=>task.value?.waiting?'小汴梁正在思考':labels[task.value?.events.at(-1)?.tool||'']||'正在理解你的要求')
const candidate=computed(()=>task.value?.status==='needs_confirmation'?task.value.result:null)
watch(()=>[messages.value.length,busy.value,showPlanning.value,candidate.value,task.value?.status,error.value],async()=>{if(atLatest.value)await scrollLatest()},{flush:'post'})
const activityName=(id:string)=>candidate.value?.activity_names?.[id]||'待核对活动'
const clock=(value:string)=>new Date(value).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'})
async function refresh(){const guard=memoryEpoch.value,id=props.tripId;const value=await cityAgentApi.context(id);if(!disposed&&guard===memoryEpoch.value&&id===props.tripId)ctx.value=value}
async function load(){const guard=memoryEpoch.value,id=props.tripId;try{await api.memorySession();await refresh();const page=await cityAgentApi.messages(id);if(disposed||guard!==memoryEpoch.value||id!==props.tripId)return;if(!props.focus&&!props.initialQuestion&&journey.choices.length&&!journey.trip?.route_version)showPlanning.value=true;earlier.value=page.before;atLatest.value=true;messages.value=page.messages.map(m=>({role:m.role==='user'?'你':'小汴梁',text:m.text}));if(props.initialQuestion){text.value=props.initialQuestion;await send()}}catch(e){error.value=e instanceof Error?e.message:'暂时无法读取行程'}}
async function send(){
 if(!text.value.trim()||!ctx.value||inputBusy.value)return
 const current=++epoch,input=text.value.trim();text.value='';error.value='';busy.value=true
 atLatest.value=true
 messages.value.push({role:'你',text:input})
 try{
  if(task.value?.status==='awaiting_locations')await cityAgentApi.cancel(task.value)
  await refresh()
  if(current!==epoch||disposed)return
  let t=await cityAgentApi.start(ctx.value!,input,props.focus)
  if(current!==epoch||disposed){await cityAgentApi.cancel(t);return}
  task.value=t
  t=await drive(t,current)
  if(current!==epoch||disposed)return
  if(t.result?.planning_action){
   showPlanning.value=true;await nextTick()
   if(current!==epoch||disposed)return
   const response=await planner.value?.applyAction(t.result.planning_action)
   if(current!==epoch||disposed)return
   messages.value.push({role:'小汴梁',text:response?.reply||'规划条件尚未接上，请重试。'})
  }else messages.value.push({role:'小汴梁',text:t.result?.reply||'本次处理已停止，请重试。'})
 }catch(e){if(current===epoch)error.value=e instanceof Error?e.message:'处理失败，请重试'}
 finally{if(current===epoch)busy.value=false}
}
async function drive(t:AgentTask,current:number):Promise<AgentTask>{
 for(let step=0;step<12&&t.status==='running';step++){
   if(t.waiting){
    const until=Date.now()+Math.max(0,t.waiting.retry_after_seconds)*1000+50
    while(Date.now()<until&&current===epoch&&!disposed)await new Promise(resolve=>setTimeout(resolve,Math.min(250,until-Date.now())))
    if(current!==epoch||disposed)return t
   }
   const poll=setInterval(()=>{void cityAgentApi.get(t).then(value=>{if(current===epoch&&!disposed)task.value=value}).catch(()=>{})},700)
   try{t=await cityAgentApi.step(t)}finally{clearInterval(poll)}
   if(current!==epoch||disposed){await cityAgentApi.cancel(t);return t}
   task.value=t
  }
 return t
}
async function selectLocation(choice:RouteLocationChoice,candidateId:string){
 if(!task.value||busy.value)return
 const current=++epoch;busy.value=true;error.value=''
 try{let t=await cityAgentApi.selectLocation(task.value,choice,candidateId);if(current!==epoch||disposed)return
  task.value=t;t=await drive(t,current);if(current!==epoch||disposed)return;task.value=t
  if(t.result?.reply)messages.value.push({role:'小汴梁',text:t.result.reply})
 }catch(e){if(current===epoch)error.value=(e as Error).message}finally{if(current===epoch)busy.value=false}
}
async function cancel(){
 ++epoch;busy.value=false
 const t=task.value
 if(!t)return
 try{task.value=await cityAgentApi.cancel(t);messages.value.push({role:'小汴梁',text:'已取消本次修改，正式行程保持不变。'})}
 catch(e){error.value=e instanceof Error?e.message:'取消未完成，请重试'}
}
async function confirm(){
 if(!task.value||!candidate.value||busy.value)return
 busy.value=true;error.value='';const guard=memoryEpoch.value
 try{
  await cityAgentApi.confirm(task.value)
  if(disposed||guard!==memoryEpoch.value)return
  task.value=null;await refresh()
  const latest=await cityApi.get(props.tripId)
  if(disposed||guard!==memoryEpoch.value)return
  journey.trip=latest
  await loadBasket()
  messages.value.push({role:'小汴梁',text:'确认成功，已读取最新正式路线。'})
 }catch(e){error.value=e instanceof Error?e.message:'候选未提交，请重新计算'}finally{busy.value=false}
}
watch(memoryEpoch,()=>{epoch++;busy.value=false;ctx.value=null;task.value=null;messages.value=[];earlier.value=null;text.value='';error.value=''})
watch(()=>props.tripId,()=>{epoch++;if(task.value&&['running','awaiting_locations'].includes(task.value.status))void cityAgentApi.cancel(task.value).catch(()=>{});task.value=null;messages.value=[];void load()})
onMounted(()=>{void load();resizeObserver=new ResizeObserver(()=>{if(atLatest.value)void scrollLatest()});if(chatScroll.value)resizeObserver.observe(chatScroll.value)})
onBeforeUnmount(()=>{disposed=true;epoch++;resizeObserver?.disconnect();if(task.value&&['running','awaiting_locations'].includes(task.value.status))void cityAgentApi.cancel(task.value).catch(()=>{})})
</script>

<template>
 <section class="panel city-agent-chat" aria-label="城市旅行对话">
  <header class="chat-heading"><span>行旅小笺</span><button v-if="!atLatest" type="button" @click="scrollLatest">回到最新 ↓</button><small v-else>向上翻阅记录</small></header>
  <div ref="chatScroll" class="chat-scroll" role="region" aria-label="聊天记录与待确认安排" tabindex="0" @scroll="trackScroll">
  <button v-if="earlier" type="button" class="chat-earlier" :disabled="historyBusy" @click="loadEarlier">{{historyBusy?'正在翻阅…':'查看更早的记录 ↑'}}</button>
  <p v-if="ctx?.progress.next" class="chat-next">下一站：{{ctx.progress.next.name}}</p>
  <p v-if="focus" class="detail-context-note">当前话题：{{focus.kind==='food'?'这道美食':focus.kind==='scenic'?'这座景区':'当前地点'}} <RouterLink :to="focus.kind==='scenic'?'/scenic/'+focus.object_id:focus.kind==='food'?'/food/'+focus.object_id:focus.kind==='vendor'?'/vendor/'+encodeURIComponent(focus.object_id):'/place/'+encodeURIComponent(focus.object_id)">返回详情</RouterLink></p>
  <p v-if="ctx?.official_plan.status==='stale'">当前路线需要重新核验。</p>
  <div class="agent-messages" role="log" aria-label="聊天记录" aria-live="polite" aria-relevant="additions"><article v-for="(m,i) in messages" :key="i" class="chat-message" :class="{'from-user':m.role==='你'}"><span class="message-author">{{m.role}}</span><p>{{m.text}}</p></article></div>
  <p v-if="busy" role="status">{{progress}}…</p>
  <p v-if="error" role="alert">{{error}}</p>
  <p v-for="(answer,i) in task?.result?.answers||[]" :key="i">{{answer.reply}}</p>
  <section v-if="task?.status==='awaiting_locations'" class="route-location-choices" aria-label="地点消歧">
   <h3>请选择你所指的地点</h3>
   <p>只用于本次参考查询，不会提交正式行程。</p>
   <div v-for="choice in task.result?.location_choices||[]" :key="choice.role">
    <h4>{{choice.role==='origin'?'起点':'终点'}}：{{choice.query}}</h4>
    <button v-for="item in choice.candidates" :key="item.candidate_id" type="button" :disabled="busy" @click="selectLocation(choice,item.candidate_id)">
     <strong>{{item.name}}</strong><small>{{item.address||'地址待补充'}}</small>
    </button>
   </div>
   <button type="button" class="text-link" :disabled="busy" @click="cancel">取消查询</button>
  </section>
  <ReferenceRouteCard v-if="task?.result?.reference_route" :route="task.result.reference_route"/>
  <section v-if="candidate" aria-label="待确认修改" class="agent-preview">
   <h3>请核对新安排</h3>
   <p>以下为待确认候选，正式路线尚未改变。</p>
   <ol v-if="candidate.planning?.result.itineraries[0]">
    <li v-for="stop in candidate.planning.result.itineraries[0].activities" :key="stop.goal_id">{{candidate.activity_names?.[stop.goal_id]||'待核对活动'}} · {{clock(stop.starts_at)}}—{{clock(stop.departs_at)}}</li>
   </ol>
   <p v-if="candidate.changes">步行距离变化 {{Math.round(candidate.changes.walking_distance_m_delta)}} 米；交通耗时变化 {{Math.round(candidate.changes.travel_seconds_delta/60)}} 分钟。</p>
   <p v-if="candidate.changes?.removed.length">移除的活动：{{candidate.changes.removed.map(activityName).join('、')}}</p>
   <p v-for="change in candidate.changes?.start_changes||[]" :key="change.goal_id">{{candidate.activity_names?.[change.goal_id]||'活动'}}：{{clock(change.before)}} → {{clock(change.after)}}</p>
   <div class="interview-actions"><button class="button" :disabled="busy" @click="confirm">确认修改</button><button class="button secondary" :disabled="busy" @click="cancel">放弃修改</button></div>
  </section>
  <BasketPlanning v-if="showPlanning&&!candidate" ref="planner" :key="tripId" :trip-id="tripId" @busy="planningBusy=$event"/>
  <div v-else-if="!messages.length&&!focus" class="chat-welcome"><p>想怎么逛开封？</p><button type="button" @click="showPlanning=true">一起安排行程</button></div>
  </div>
  <form class="chat-composer" @submit.prevent="send()">
   <label for="city-agent-input" class="visually-hidden">告诉小汴梁你的问题或想调整的安排</label>
   <textarea id="city-agent-input" v-model="text" rows="2" maxlength="2000" :disabled="!ctx||inputBusy||!!candidate" placeholder="告诉小汴梁你的想法…"/>
   <div class="interview-actions"><button class="button" :disabled="!ctx||inputBusy||!text.trim()||!!candidate">发送</button><button v-if="busy" type="button" class="button secondary" @click="cancel">停止处理</button></div>
  </form>

 </section>
</template>

<style scoped>
.city-agent-chat{display:flex;flex-direction:column;height:clamp(420px,calc(100dvh - 230px),820px);box-sizing:border-box;padding:0;gap:0;overflow:hidden;background:#fbf8f1;border:1px solid #d9cdb8;border-radius:3px 20px 3px 20px}
.chat-heading{flex-shrink:0;display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:44px;padding:0 20px;border-bottom:1px solid #e7ddcb;color:#8e704c;background:#f4eddf}
.chat-heading>span{font:15px/1.6 "Songti SC","STSong",serif;letter-spacing:.2em}.chat-heading small{font-size:11px;color:#9a8b76}.chat-heading button{min-height:44px;font-size:12px;color:#466a5b}
.chat-scroll{flex:1;min-height:0;overflow-y:auto;overscroll-behavior-y:contain;scrollbar-width:thin;scrollbar-color:#c8b99f transparent;padding:20px;scroll-padding-block:20px;-webkit-overflow-scrolling:touch}
.chat-scroll:focus-visible{outline:2px solid #78917e;outline-offset:-3px}.agent-messages{display:flex;flex-direction:column;gap:20px;overflow-wrap:anywhere}.chat-message{display:flex;flex-direction:column;align-items:flex-start;max-width:88%;align-self:flex-start;min-width:0}.message-author{font:12px/1.6 "Songti SC","STSong",serif;color:#907853;letter-spacing:.12em;padding:0 4px 6px}.chat-message p{margin:0;padding:13px 16px;border:1px solid #e4d8c4;border-radius:2px 14px 14px 14px;background:#fffcf6;box-shadow:0 3px 9px #765d3410;color:#304e43;font:17px/1.9 "Songti SC","STSong","Noto Serif CJK SC",serif;white-space:pre-wrap;overflow-wrap:anywhere}.chat-message.from-user{align-self:flex-end;align-items:flex-end}.from-user .message-author{color:#6d816d}.from-user p{border-color:#d5decb;background:#eaf0e3;border-radius:14px 2px 14px 14px}
.chat-earlier{display:block;margin:0 auto 18px;min-height:44px;color:#88724e;font-size:12px}
.chat-welcome p{font:24px/1.6 "Songti SC",serif;margin:0 0 18px}.chat-welcome button{border:1px solid #c8cfbb;padding:12px 18px;border-radius:6px;color:#456653;min-height:44px}
.chat-composer{flex-shrink:0;padding:12px 20px 16px;border-top:1px solid #ded4c2;background:#f8f3e9}.chat-composer .interview-actions{justify-content:flex-end;margin-top:8px}.chat-composer .button{background:#29483b;color:#fff8e9;min-height:44px;padding:10px 24px;font-size:14px}.chat-next{font-size:13px;color:#7b846a}.agent-preview{border:1px solid #c7b795;border-radius:3px 14px;padding:1rem;margin-top:20px}.city-agent-chat textarea{display:block;width:100%;box-sizing:border-box;margin:0;padding:10px 12px;border:1px solid #c9baa1;border-radius:3px 10px;font:16px/1.6 "Songti SC","STSong",serif;background:#fffcf6;resize:none;max-height:100px}.city-agent-chat textarea:focus{outline:2px solid #839b82;outline-offset:2px}.city-agent-chat label{display:block}
@media(max-width:760px){.city-agent-chat{height:clamp(360px,calc(100dvh - 222px),820px)}.chat-heading{padding-inline:14px}.chat-scroll{padding:16px 12px}.chat-message{max-width:92%}.chat-message p{font-size:16px;padding:11px 13px;line-height:1.85}.chat-composer{padding:10px 86px 12px 12px}.chat-composer .button{padding:8px 20px}.chat-composer .interview-actions{justify-content:flex-start;gap:6px}.chat-composer .secondary{padding-inline:10px}}
@media(prefers-reduced-motion:reduce){.chat-scroll{scroll-behavior:auto}}
.route-location-choices{border:1px solid #d9cdb8;padding:14px;margin:16px 0;border-radius:4px 16px;background:#fffcf6}.route-location-choices h3{font:20px "Songti SC",serif}.route-location-choices p,.route-location-choices small{font-size:12px}.route-location-choices button{display:grid;width:100%;gap:6px;min-height:44px;text-align:left;padding:12px;margin:8px 0;border:1px solid #ddd3bd;border-radius:6px;color:#29483b}.route-location-choices .text-link{border:0}
</style>
