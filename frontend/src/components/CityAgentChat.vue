<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {api,ApiError} from '../api'
import {cityAgentApi,type AgentTask,type CityContext,type RouteLocationChoice,type WeatherReport,type BasketChoicePrompt} from '../cityAgentApi'
import {memoryEpoch,pendingVoiceText} from '../voiceDraft'
import {cityApi} from '../cityApi'
import {journey} from '../journey'
import {loadBasket} from '../journey'
import BasketPlanning from './BasketPlanning.vue'
import {basketApi,type ObjectRef} from '../basketApi'
import {basketReply} from '../basketReply'
import ReferenceRouteCard from './ReferenceRouteCard.vue'
import TravelServiceCards from './TravelServiceCards.vue'
import WeatherCard from './WeatherCard.vue'
import {isMultiDayItinerary,objectiveLabel,type PlanningReply} from '../explorationApi'
import PlanPreview from './PlanPreview.vue'
import PreferenceCard from './PreferenceCard.vue'
import {interactionModes,readMode,writeMode,type InteractionMode} from '../interactionMode'

const props=defineProps<{tripId:string;focus?:ObjectRef|null;initialQuestion?:string;planning?:boolean}>()
const interactionMode=computed(()=>interactionModes.value[props.tripId]??'deep')
function selectMode(mode:InteractionMode){preferenceCardOpened.value=false;writeMode(props.tripId,mode)}
const showPlanning=ref(!!props.planning),planningRevision=ref(0),planningBusy=ref(false)
const planningCompleted=ref(false)
watch([showPlanning,planningRevision],()=>planningCompleted.value=false)
const continuePlanning=ref(false),workflowResult=ref<PlanningReply|null>(null)
const inputBusy=computed(()=>busy.value||planningBusy.value)
const ctx=ref<CityContext|null>(null),text=ref(''),task=ref<AgentTask|null>(null),busy=ref(false),slowBusy=ref(false),error=ref('')
let slowBusyTimer:ReturnType<typeof setTimeout>|undefined
const weather=ref<WeatherReport|null>(null)
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
const labels:Record<string,string>={query_weather:'正在查询和风天气',search_locations:'正在查找起点和终点',query_reference_route:'正在查询高德参考路线',search_dining:'正在查找合适商家',query_shows:'正在核对演出时间',verify_travel:'正在验证交通衔接',preview_plan:'正在验证行程安排',understand:'正在理解你的要求'}
watch(busy,value=>{if(slowBusyTimer)clearTimeout(slowBusyTimer);slowBusy.value=false;if(value)slowBusyTimer=setTimeout(()=>slowBusy.value=true,8000)},{flush:'sync'})
const progress=computed(()=>slowBusy.value?'正在向高德查询路线和计算方案，地点较多时可能需要半分钟…':task.value?.waiting?'小汴梁正在思考':labels[task.value?.events.at(-1)?.tool||'']||'正在理解你的要求')
const candidate=computed(()=>task.value?.status==='needs_confirmation'&&task.value.result?.confirmation?task.value.result:null)
const selectedCandidate=ref(0)
const candidatePlans=computed(()=>candidate.value?.planning?.result.itineraries||[])
const multiCandidate=computed(()=>candidatePlans.value.some(isMultiDayItinerary))
const singleCandidate=computed(()=>{const plan=candidatePlans.value[0];return plan&&!isMultiDayItinerary(plan)?plan:null})
const candidateChanges=computed(()=>candidate.value?.alternative_changes?.[selectedCandidate.value]||candidate.value?.changes)
watch(()=>candidate.value?.confirmation?.candidate_id,()=>selectedCandidate.value=0)
const basketProposal=computed(()=>task.value?.result?.basket_proposal)
const basketChoices=computed(()=>task.value?.result?.basket_choices||[])
const preferenceCardKey=computed(()=>`bianliang:prefs-card:${props.tripId}`)
const preferenceCardClosed=ref(false),preferenceCardOpened=ref(false)
watch(interactionMode,()=>preferenceCardOpened.value=false)
const planningFormVisible=computed(()=>showPlanning.value&&!candidate.value&&!basketProposal.value&&!planningCompleted.value)
const showPreferenceCard=computed(()=>Boolean(ctx.value&&!planningFormVisible.value&&((interactionMode.value==='deep'&&ctx.value.identity.route_version===0)||preferenceCardOpened.value)&&!preferenceCardClosed.value&&!inputBusy.value&&!candidate.value))
function readPreferenceCardState(){preferenceCardOpened.value=false;try{preferenceCardClosed.value=localStorage.getItem(preferenceCardKey.value)==='1'}catch{preferenceCardClosed.value=false}}
function closePreferenceCard(){try{localStorage.setItem(preferenceCardKey.value,'1')}catch{}preferenceCardClosed.value=true;preferenceCardOpened.value=false}
function reopenPreferenceCard(){try{localStorage.removeItem(preferenceCardKey.value)}catch{}preferenceCardClosed.value=false;preferenceCardOpened.value=true;void scrollLatest()}
function submitPreference(sentence:string){closePreferenceCard();void send(sentence)}
watch(()=>[messages.value.length,busy.value,showPlanning.value,showPreferenceCard.value,candidate.value,basketProposal.value,basketChoices.value.length,task.value?.status,error.value],async()=>{if(atLatest.value)await scrollLatest()},{flush:'post'})
const activityName=(id:string)=>candidate.value?.activity_names?.[id]||'待核对活动'
const clock=(value:string)=>new Date(value).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'})
async function refresh(){const guard=memoryEpoch.value,id=props.tripId;const value=await cityAgentApi.context(id);if(!disposed&&guard===memoryEpoch.value&&id===props.tripId)ctx.value=value}
async function completePlanning(){try{await refresh()}catch(e){error.value=e instanceof Error?e.message:'暂时无法读取行程'}finally{planningCompleted.value=true}}
async function checkWeather(){
 const id=props.tripId,guard=memoryEpoch.value,version=ctx.value?.identity.version,routeVersion=ctx.value?.identity.route_version
 if(version===undefined||routeVersion===undefined)return
 try{const result=await cityAgentApi.weather(id)
  if(disposed||guard!==memoryEpoch.value||id!==props.tripId||result.weather?.trip_version!==version||result.weather.route_version!==routeVersion)return
  if(result.weather?.impacts.length){weather.value=result.weather
   if(messages.value.at(-1)?.text!==result.reply)messages.value.push({role:'小汴梁',text:result.reply})
  }
 }catch{/* An optional weather check never blocks the existing trip or chat. */}
}
async function load(){const guard=memoryEpoch.value,id=props.tripId;if(!(id in interactionModes.value))readMode(id);readPreferenceCardState();try{await api.memorySession();await refresh();const page=await cityAgentApi.messages(id);if(disposed||guard!==memoryEpoch.value||id!==props.tripId)return;if(!props.focus&&!props.initialQuestion&&journey.choices.length&&!journey.trip?.route_version)showPlanning.value=true;earlier.value=page.before;atLatest.value=true;messages.value=page.messages.map(m=>({role:m.role==='user'?'你':'小汴梁',text:m.text}));void checkWeather();if(props.initialQuestion){text.value=props.initialQuestion;await send()}}catch(e){error.value=e instanceof Error?e.message:'暂时无法读取行程'}}
async function send(inputOverride?:string){
 const input=(inputOverride??text.value).trim()
 if(!input||!ctx.value||inputBusy.value)return
 text.value='';error.value=''
 atLatest.value=true
 messages.value.push({role:'你',text:input})
 if(basketProposal.value){
  const reply=basketReply(input)
  if(reply==='accept'&&!basketChoices.value.length){await confirmBasket();return}
  if(reply==='decline'){discardBasket();return}
  // Anything else would silently drop the pending proposal; keep the text and ask first.
  messages.value.pop();text.value=input
  error.value=reply==='accept'?'请先在上面的候选里选定，再确认加入。':'请先回答是否加入素材篮：回复“好的”或“不用了”，也可以点下方按钮。';return
 }
 const current=++epoch;busy.value=true;continuePlanning.value=false
 try{
  if(task.value?.status==='awaiting_locations')await cityAgentApi.cancel(task.value)
  await refresh()
  if(current!==epoch||disposed)return
  let t=await cityAgentApi.start(ctx.value!,input,props.focus)
  if(current!==epoch||disposed){await cityAgentApi.cancel(t);return}
  task.value=t
  t=await drive(t,current)
  if(current!==epoch||disposed)return
  if(t.result?.interaction_mode_changed)writeMode(t.trip_id,t.result.interaction_mode!)
  weather.value=t.result?.weather||null
  if(t.result?.planning_workflow){
   if(t.result.planning_workflow.trip_id!==props.tripId)throw new Error('安排已变化，请重新读取当前旅程。')
   await loadBasket()
   if(current!==epoch||disposed)return
   workflowResult.value=t.result.planning_workflow.result;planningRevision.value++;showPlanning.value=!t.result.basket_proposal&&!t.result.basket_choices
  }
  messages.value.push({role:'小汴梁',text:t.result?.reply||'本次处理已停止，请重试。'})
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
  if(t.result?.interaction_mode_changed)writeMode(t.trip_id,t.result.interaction_mode!)
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
  await cityAgentApi.confirm(task.value,selectedCandidate.value)
  if(disposed||guard!==memoryEpoch.value)return
  task.value=null;await refresh()
  const latest=await cityApi.get(props.tripId)
  if(disposed||guard!==memoryEpoch.value)return
  journey.trip=latest
  await loadBasket()
  messages.value.push({role:'小汴梁',text:'确认成功，已读取最新正式路线。'})
 }catch(e){error.value=e instanceof Error?e.message:'候选未提交，请重新计算'}finally{busy.value=false}
}
async function confirmBasket(){
 const proposal=basketProposal.value
 if(!proposal||inputBusy.value||basketChoices.value.length)return
 const workflow=task.value?.result?.planning_workflow?.result
 const current=++epoch,guard=memoryEpoch.value,id=props.tripId
 const active=()=>!disposed&&current===epoch&&guard===memoryEpoch.value&&id===props.tripId
 busy.value=true;error.value=''
 let applied=0
 try{
  let basket=await basketApi.get(id)
  if(!active())return
  if(basket.version!==proposal.basket_version)throw new Error('素材篮已变化，请刷新后重新提出要求；本次未覆盖已有素材。')
  for(const item of proposal.items){
   const existing=basket.items.find(x=>x.kind===item.kind&&x.id===item.object_id)
   if(item.operation==='standby'&&existing?.status==='arranged')throw new Error(item.name+'已在正式行程里，请刷新后使用跳过操作。')
   basket=await basketApi.change(id,basket.version,{...existing,key:item.kind+':'+item.object_id,kind:item.kind,id:item.object_id,
    name:item.name,priority:item.priority,status:item.operation==='standby'?'standby':'pending',source:'assistant'})
   applied++;if(!active())return
  }
  task.value=null;await loadBasket();await refresh();if(!active())return
  messages.value.push({role:'小汴梁',text:'素材篮已更新，接着按已保存的条件安排。'})
  continuePlanning.value=true;workflowResult.value=workflow||null;planningRevision.value++;showPlanning.value=true
 }catch(e){if(active()){
  task.value=null;await loadBasket()
  error.value=e instanceof ApiError&&e.status===409?'素材篮或行程已变化，请刷新后重新提出要求；本次未覆盖冲突项。':e instanceof Error?e.message:'素材篮未更新，请重试'
  if(applied)error.value+='已确认写入的 '+applied+' 项已保留，请核对后继续。'
 }}finally{if(active())busy.value=false}
}
function selectBasketChoice(choice:BasketChoicePrompt,item:BasketChoicePrompt['candidates'][number]){
 const result=task.value?.result,version=result?.basket_proposal?.basket_version??result?.planning_workflow?.result.basket_version
 if(!result||version===undefined||inputBusy.value)return
 const proposal=result.basket_proposal||{basket_version:version,items:[]}
 const existing=journey.choices.find(x=>x.kind===item.kind&&x.id===item.object_id)
 if(!proposal.items.some(i=>i.kind===item.kind&&i.object_id===item.object_id))proposal.items.push({kind:item.kind,object_id:item.object_id,
  name:item.name,operation:choice.operation||'put',priority:choice.priority_stated===false?existing?.priority||choice.priority:choice.priority,quote:choice.quote})
 result.basket_proposal=proposal;result.basket_choices=result.basket_choices?.filter(c=>c!==choice)
}
function discardBasket(){if(inputBusy.value)return;task.value=null;messages.value.push({role:'小汴梁',text:'已放弃本次素材篮变更。'})}
watch(memoryEpoch,()=>{epoch++;busy.value=false;continuePlanning.value=false;ctx.value=null;task.value=null;messages.value=[];earlier.value=null;text.value='';error.value=''})
watch(()=>props.tripId,()=>{epoch++;continuePlanning.value=false;if(task.value&&['running','awaiting_locations'].includes(task.value.status))void cityAgentApi.cancel(task.value).catch(()=>{});ctx.value=null;task.value=null;messages.value=[];void load()})
onMounted(()=>{void load();resizeObserver=new ResizeObserver(()=>{if(atLatest.value)void scrollLatest()});if(chatScroll.value)resizeObserver.observe(chatScroll.value)})
onBeforeUnmount(()=>{disposed=true;epoch++;resizeObserver?.disconnect();if(slowBusyTimer)clearTimeout(slowBusyTimer);if(task.value&&['running','awaiting_locations'].includes(task.value.status))void cityAgentApi.cancel(task.value).catch(()=>{})})
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
  <TravelServiceCards :trip-id="tripId" :revision="task?.result"/>
  <ReferenceRouteCard v-if="task?.result?.reference_route" :route="task.result.reference_route"/>
  <WeatherCard v-if="weather" :report="weather" :busy="inputBusy" @adjust="send('可以，按天气调整行程')"/>
  <section v-if="basketChoices.length" class="route-location-choices" aria-label="素材篮候选选择">
   <h3>请选择对应的对象</h3>
   <div v-for="choice in basketChoices" :key="choice.query"><h4>{{choice.query}}</h4>
    <button v-for="item in choice.candidates" :key="item.kind+':'+item.object_id" type="button" :disabled="inputBusy" @click="selectBasketChoice(choice,item)"><strong>{{item.name}}</strong><small>{{item.address||'地址待补充'}}</small></button>
   </div>
   <button type="button" class="text-link" :disabled="inputBusy" @click="discardBasket">不用了</button>
  </section>
  <section v-if="basketProposal" aria-label="待确认的素材篮变更" class="agent-preview">
   <h3>请确认素材篮变更</h3>
   <ul><li v-for="item in basketProposal.items" :key="item.kind+':'+item.object_id">{{item.name}} · {{item.operation==='standby'?'改为候补':journey.choices.some(x=>x.kind===item.kind&&x.id===item.object_id)?'调整优先级':'加入素材篮'}} · <template v-if="item.operation==='put'"><label class="basket-priority">优先级<select :aria-label="item.name+'优先级'" v-model="item.priority"><option value="must">必去</option><option value="want">想去</option><option value="optional">顺路看看</option></select></label></template><template v-else>{{{must:'必去',want:'想去',optional:'顺路看看'}[item.priority]}}</template></li></ul>
   <p>确认后接着规划，正式行程仍需确认方案才会保存。</p>
   <div class="interview-actions"><button type="button" class="button" :disabled="inputBusy||!!basketChoices.length" @click="confirmBasket">确认加入</button><button type="button" class="button secondary" :disabled="inputBusy" @click="discardBasket">不用了</button></div>
  </section>
  <section v-if="candidate" aria-label="待确认修改" class="agent-preview">
   <h3>请核对新安排</h3>
   <p>以下为待确认候选，正式路线尚未改变。</p>
   <PlanPreview v-if="multiCandidate" :plans="candidatePlans" :selected="selectedCandidate" :disabled="busy" :names="candidate.activity_names" :changes="candidateChanges" @select="selectedCandidate=$event"/>
   <template v-else-if="singleCandidate">
   <h4>{{objectiveLabel(singleCandidate.objectives)}}</h4>
   <small v-if="singleCandidate.objectives?.includes('lower_cost')&&singleCandidate.cost_unknown">费用资料不全，只按已知费用比较</small>
   <ol>
    <li v-for="stop in singleCandidate.activities" :key="stop.goal_id">{{candidate.activity_names?.[stop.goal_id]||'待核对活动'}} · {{clock(stop.starts_at)}}—{{clock(stop.departs_at)}}</li>
   </ol>
   </template>
   <p v-if="candidate.changes&&!multiCandidate">步行距离变化 {{Math.round(candidate.changes.walking_distance_m_delta)}} 米；交通耗时变化 {{Math.round(candidate.changes.travel_seconds_delta/60)}} 分钟。</p>
   <p v-if="!multiCandidate&&candidate.changes?.removed?.length">移除的活动：{{candidate.changes.removed.map(activityName).join('、')}}</p>
   <p v-for="change in !multiCandidate?candidate.changes?.start_changes||[]:[]" :key="change.goal_id">{{candidate.activity_names?.[change.goal_id]||'活动'}}：{{clock(change.before)}} → {{clock(change.after)}}</p>
   <ul v-if="candidate.planning?.result.warnings?.length" class="agent-planning-warnings" aria-label="演示规划提醒"><li v-for="warning in candidate.planning.result.warnings" :key="warning">{{warning}}</li></ul>
   <div class="interview-actions"><button class="button" :disabled="busy" @click="confirm">{{multiCandidate?'确认整套方案':'确认修改'}}</button><button class="button secondary" :disabled="busy" @click="cancel">放弃修改</button></div>
  </section>
  <BasketPlanning v-if="showPlanning&&!candidate&&!basketProposal" :key="tripId" :trip-id="tripId" :revision="planningRevision" :auto-plan="continuePlanning" :workflow-result="workflowResult" @busy="planningBusy=$event" @complete="completePlanning" @close="showPlanning=false"/>
  <div v-else-if="!messages.length&&!focus" class="chat-welcome"><p>想怎么逛开封？</p><button type="button" @click="showPlanning=true">一起安排行程</button></div>
  <div v-if="showPreferenceCard" class="preference-message"><span class="message-author">小汴梁</span><PreferenceCard @submit="submitPreference" @dismiss="closePreferenceCard"/></div>
  </div>
  <form class="chat-composer" @submit.prevent="send()">
   <div class="interaction-mode" role="group" aria-label="互动模式"><button v-for="mode in (['deep','quick'] as const)" :key="mode" type="button" :aria-pressed="interactionMode===mode" :disabled="inputBusy" @click="selectMode(mode)">{{mode==='deep'?'深度互动':'快速安排'}}</button></div>
   <label for="city-agent-input" class="visually-hidden">告诉小汴梁你的问题或想调整的安排</label>
   <textarea id="city-agent-input" v-model="text" rows="2" maxlength="2000" :disabled="!ctx||inputBusy||!!candidate" placeholder="告诉小汴梁你的想法…"/>
   <div class="interview-actions"><button class="button" :disabled="!ctx||inputBusy||!text.trim()||!!candidate">发送</button><button v-if="ctx&&!candidate&&!planningFormVisible" type="button" class="button secondary preference-toggle" :disabled="inputBusy" @click="reopenPreferenceCard">偏好</button><button v-if="busy" type="button" class="button secondary" @click="cancel">停止处理</button></div>
  </form>

 </section>
</template>

<style scoped>
.city-agent-chat{display:flex;flex-direction:column;height:clamp(420px,calc(100dvh - 230px),820px);box-sizing:border-box;padding:0;gap:0;overflow:hidden;background:#fbf8f1;border:1px solid #d9cdb8;border-radius:3px 20px 3px 20px}
.chat-heading{flex-shrink:0;display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:44px;padding:0 20px;border-bottom:1px solid #e7ddcb;color:#8e704c;background:#f4eddf}
.chat-heading>span{font:15px/1.6 "Songti SC","STSong",serif;letter-spacing:.2em}.chat-heading small{font-size:11px;color:#9a8b76}.chat-heading button{min-height:44px;font-size:12px;color:#466a5b}
.chat-scroll{flex:1;min-height:0;overflow-y:auto;overscroll-behavior-y:contain;scrollbar-width:thin;scrollbar-color:#c8b99f transparent;padding:20px;scroll-padding-block:20px;-webkit-overflow-scrolling:touch}
.chat-scroll:focus-visible{outline:2px solid #78917e;outline-offset:-3px}.agent-messages{display:flex;flex-direction:column;gap:20px;overflow-wrap:anywhere}.chat-message{display:flex;flex-direction:column;align-items:flex-start;max-width:88%;align-self:flex-start;min-width:0}.message-author{font:12px/1.6 "Songti SC","STSong",serif;color:#907853;letter-spacing:.12em;padding:0 4px 6px}.chat-message p{margin:0;padding:13px 16px;border:1px solid #e4d8c4;border-radius:2px 14px 14px 14px;background:#fffcf6;box-shadow:0 3px 9px #765d3410;color:#304e43;font:17px/1.9 "Songti SC","STSong","Noto Serif CJK SC",serif;white-space:pre-wrap;overflow-wrap:anywhere}.chat-message.from-user{align-self:flex-end;align-items:flex-end}.from-user .message-author{color:#6d816d}.from-user p{border-color:#d5decb;background:#eaf0e3;border-radius:14px 2px 14px 14px}
.agent-planning-warnings{margin:12px 0;padding:10px 14px;background:#f5eee1;border-left:3px solid #b29567;color:#7d694c;font-size:12px;line-height:1.7}
.chat-earlier{display:block;margin:0 auto 18px;min-height:44px;color:#88724e;font-size:12px}
.preference-message{display:flex;flex-direction:column;align-items:flex-start;max-width:100%;align-self:flex-start;min-width:0;margin-top:20px}.preference-message :deep(.preference-card){margin-top:0}
.chat-composer .button.preference-toggle{padding-inline:12px;font-size:12px}
.interaction-mode{display:flex;gap:4px;margin-bottom:10px;padding:3px;border:1px solid #d9cdb8;border-radius:8px;width:fit-content}.interaction-mode button{min-height:36px;padding:6px 14px;border:0;border-radius:5px;font:inherit;font-size:13px;color:#6d7968}.interaction-mode button[aria-pressed=true]{background:#29483b;color:#fff8e9}
.chat-welcome p{font:24px/1.6 "Songti SC",serif;margin:0 0 18px}.chat-welcome button{border:1px solid #c8cfbb;padding:12px 18px;border-radius:6px;color:#456653;min-height:44px}
.chat-composer{flex-shrink:0;padding:12px 20px 16px;border-top:1px solid #ded4c2;background:#f8f3e9}.chat-composer .interview-actions{justify-content:flex-end;margin-top:8px}.chat-composer .button{background:#29483b;color:#fff8e9;min-height:44px;padding:10px 24px;font-size:14px}.chat-next{font-size:13px;color:#7b846a}.agent-preview{border:1px solid #c7b795;border-radius:3px 14px;padding:1rem;margin-top:20px}.city-agent-chat textarea{display:block;width:100%;box-sizing:border-box;margin:0;padding:10px 12px;border:1px solid #c9baa1;border-radius:3px 10px;font:16px/1.6 "Songti SC","STSong",serif;background:#fffcf6;resize:none;max-height:100px}.city-agent-chat textarea:focus{outline:2px solid #839b82;outline-offset:2px}.city-agent-chat label{display:block}
@media(max-width:760px){.city-agent-chat{height:clamp(360px,calc(100dvh - 222px),820px)}.chat-heading{padding-inline:14px}.chat-scroll{padding:16px 12px}.chat-message{max-width:92%}.chat-message p{font-size:16px;padding:11px 13px;line-height:1.85}.chat-composer{padding:10px 86px 12px 12px}.chat-composer .button{padding:8px 20px}.chat-composer .interview-actions{justify-content:flex-start;gap:6px}.chat-composer .secondary{padding-inline:10px}}
@media(prefers-reduced-motion:reduce){.chat-scroll{scroll-behavior:auto}}
.route-location-choices{border:1px solid #d9cdb8;padding:14px;margin:16px 0;border-radius:4px 16px;background:#fffcf6}.route-location-choices h3{font:20px "Songti SC",serif}.route-location-choices p,.route-location-choices small{font-size:12px}.route-location-choices button{display:grid;width:100%;gap:6px;min-height:44px;text-align:left;padding:12px;margin:8px 0;border:1px solid #ddd3bd;border-radius:6px;color:#29483b}.route-location-choices .text-link{border:0}
</style>
