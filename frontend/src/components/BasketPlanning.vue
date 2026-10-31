<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {ArrowRight,Check,Clock3,Footprints,RefreshCw} from 'lucide-vue-next'
import {journey,clock,nameOf,loadBasket,loadJourney,loadConfirmedRoute,localDate,saveChoice,choice} from '../journey'
import {alternativeDifference,explorationApi,isMultiDayItinerary,objectiveLabel,type PlanningConstraints,type PlanningInput,type MultiDayPlanningConstraints,type PlanningResult,type PlanningReply,type SingleDayItinerary} from '../explorationApi'
import {ApiError} from '../api'
import {memoryEpoch} from '../voiceDraft'
import LocationPicker from './LocationPicker.vue'
import PlanPreview from './PlanPreview.vue'
import BudgetControls from './BudgetControls.vue'
import {isMultiDayDraft} from '../cityApi'
import {interactionModes,readMode,writeMode,type InteractionMode} from '../interactionMode'
const props=defineProps<{tripId:string;revision?:number;autoPlan?:boolean;workflowResult?:PlanningReply|null}>(),emit=defineEmits<{busy:[value:boolean];complete:[];close:[]}>()
const initializing=ref(true),confirming=ref(false),conversation=ref<HTMLElement>()
let disposed=false,requestSequence=0
const busy=ref(false),error=ref(''),result=ref<PlanningResult|null>(null),selected=ref(0),day=ref(''),start=ref(''),end=ref(''),budget=ref<string|number>(''),rounds=ref(0),cancelled=ref(false)
const submittedHash=ref(''),planningVersion=ref(0)
const form=ref<PlanningConstraints>({business_date:null,starts_at:null,ends_at:null,origin_place_id:null,destination_place_id:null,pace:'standard',objective:'balanced',modes:['walking'],budget_fen:null})
interface DayForm{day_id:string;business_date:string;starts_at:string|null;ends_at:string|null;origin_place_id:string|null;destination_place_id:string|null;origin_text?:string|null;destination_text?:string|null}
interface OvernightForm{from_day_id:string;to_day_id:string;place_id:string|null;[key:string]:unknown}
interface MultiForm extends MultiDayPlanningConstraints{days:DayForm[];overnights?:OvernightForm[];clarifications?:string[]}
const multiForm=ref<MultiForm|null>(null)
const multi=computed(()=>!!multiForm.value)
const multiReadOnly=computed(()=>multi.value&&journey.trip?.status!=='DRAFT')
const readyMulti=computed(()=>!!multiForm.value?.days.length&&multiForm.value.days.every(d=>d.starts_at&&d.ends_at&&Date.parse(d.ends_at)>Date.parse(d.starts_at)&&d.origin_place_id&&d.destination_place_id)&&!multiForm.value.clarifications?.length&&!(multiForm.value.pending_fields as unknown[]||[]).length)
const copy=<T,>(value:T):T=>JSON.parse(JSON.stringify(value)) as T
function normalize(value:PlanningReply):PlanningResult{
 if('planning' in value)return value
 const plan=value.result
 return {...value,planning_version:value.planning_version??planningVersion.value,constraints:value.constraints||params(),status:plan?.status==='feasible'?'feasible':plan?.status==='infeasible'?'infeasible':'unknown',reply:value.reply||(plan?.status==='feasible'?'请比较整套方案并确认，正式行程尚未改变。':'请核对各日条件与资料。'),issues:value.issues||(plan as unknown as {issues?:PlanningResult['issues']})?.issues||[],options:value.options||[],rounds:value.rounds||0,basket_version:value.basket_version??journey.basketVersion,trip_version:value.trip_version??value.base_version,planning:{candidate_id:value.candidate_id,base_version:value.base_version,result:plan,changes:value.changes,alternative_changes:value.alternative_changes}}
}
function updateDayTime(d:DayForm,field:'starts_at'|'ends_at',value:string){d[field]=value?d.business_date+'T'+value+':00+08:00':null}
function updateOvernight(o:OvernightForm,value:string){o.place_id=value||null;Object.assign(o,{source_kind:'user_place',service_selection_id:null,service_version:null,binding_revision:null,place_evidence:'unknown',stay_evidence:'unknown',evidence_refs:[]});for(const d of multiForm.value?.days||[]){if(d.day_id===o.from_day_id){d.destination_place_id=o.place_id;d.destination_text=null}if(d.day_id===o.to_day_id){d.origin_place_id=o.place_id;d.origin_text=null}}}
const places=computed(()=>journey.catalog.filter(x=>x.record.kind==='place'))
const editingStep=ref(''),activeStep=ref('date'),earlyTimeConfirmed=ref(false)
// MultiDayConstraints.days and its date-range validator both cap executable plans at 3 days.
const maxTripDays=3,tripDays=ref(1),daysConfirmed=ref(false)
const validDays=computed(()=>Number.isInteger(tripDays.value)&&tripDays.value>=1&&tripDays.value<=maxTripDays)
const preferencesComplete=ref(false)
const interactionMode=computed(()=>interactionModes.value[props.tripId]??null)
const partySize=computed({get:()=>form.value.party_size??null,set:(value:number|null)=>{form.value.party_size=value}})
const validParty=computed(()=>Number.isInteger(partySize.value)&&Number(partySize.value)>=1&&Number(partySize.value)<=50)
const earlyTime=computed(()=>!!start.value&&Number(start.value.slice(0,2))<6)
watch([day,start,end],()=>{earlyTimeConfirmed.value=false})
const coreMissing=computed(()=>!day.value?'date':!daysConfirmed.value?'days':!start.value||!end.value?'time':!(form.value.origin_place_id||form.value.origin_text?.trim())?'origin':!(form.value.destination_place_id||form.value.destination_text?.trim())?'destination':'ready')
const missingStep=computed(()=>multi.value?(readyMulti.value?'ready':'multi'):!materials.value.length&&!day.value?'empty':coreMissing.value!=='ready'?coreMissing.value:!validParty.value?'party':!interactionMode.value?'mode':interactionMode.value==='deep'&&!preferencesComplete.value?'preferences':'ready')
const step=computed(()=>editingStep.value||activeStep.value)
const personalMissing=computed(()=>result.value?.issues?.some(x=>x.code==='PERSONAL_CHOICE_MISSING'))
const visibleResult=computed(()=>result.value&&!personalMissing.value&&(multi.value||!['party','mode','preferences'].includes(activeStep.value))?result.value:null)
watch([step,visibleResult,busy],async()=>{if(busy.value||initializing.value)return;await nextTick();conversation.value?.querySelector<HTMLElement>('.planning-question,.planning-assessment')?.scrollIntoView({block:'nearest',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})})
const materials=computed(()=>journey.choices.filter(x=>x.status!=='standby'))
const enteringLocations=computed(()=>['origin','destination'].includes(step.value)&&!places.value.length)
const question=computed(()=>enteringLocations.value?(tripDays.value>1?'每天从哪里出发，最后返回哪里？':'从哪里出发，最后返回哪里？'):({empty:'素材篮还是空的，先挑几个地方，还是用推荐路线？',date:tripDays.value>1?'第一天是哪天？':'你打算哪天去？',days:'这次玩几天？',time:tripDays.value>1?'每天几点开始、几点结束？':'当天几点开始，最晚几点结束？',origin:tripDays.value>1?'每天从哪里出发？':'从哪里出发？',destination:tripDays.value>1?'每天最后回到哪里？':'最后回到哪里？',party:'几个人一起去？',mode:'想怎么安排？',preferences:'最后几个问题，帮我更懂你',ready:'这些安排合适吗？'}[step.value]||''))
const canContinue=computed(()=>enteringLocations.value?!!(form.value.origin_place_id||form.value.origin_text?.trim())&&!!(form.value.destination_place_id||form.value.destination_text?.trim()):step.value==='days'?validDays.value:step.value==='party'?validParty.value:step.value==='date'?!!day.value:step.value==='time'?!!start.value&&!!end.value:step.value==='origin'?!!(form.value.origin_place_id||form.value.origin_text?.trim()):step.value==='destination'?!!(form.value.destination_place_id||form.value.destination_text?.trim()):!['empty','mode'].includes(step.value))
watch(()=>busy.value||initializing.value,value=>emit('busy',value),{immediate:true})
function chooseDay(offset:number){const date=new Date(localDate()+'T12:00:00+08:00');date.setDate(date.getDate()+offset);day.value=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(date);void advance()}
async function advance(){
 if(!canContinue.value)return
 if(start.value&&end.value&&end.value<=start.value){error.value='结束时间需要晚于开始时间';return}
 if(earlyTime.value&&!earlyTimeConfirmed.value){editingStep.value='time';error.value='开始时间为凌晨，请核对并确认后继续。';return}
 error.value=''
 const current=step.value,revising=!!editingStep.value&&!!result.value&&!personalMissing.value
 if(current==='days')daysConfirmed.value=true
 if(current==='preferences'){preferencesComplete.value=true;result.value=null}
 if(current==='ready'||(current!=='preferences'&&revising)||coreMissing.value!=='ready')await run()
 if(!error.value){activeStep.value=missingStep.value;editingStep.value='';if(current!=='ready'&&!revising&&missingStep.value==='ready'&&interactionMode.value==='quick')await run()}
}
async function chooseDays(value:number){tripDays.value=value;await advance()}
async function chooseParty(value:number){partySize.value=value;await advance()}
function selectMode(value:InteractionMode){writeMode(props.tripId,value)}
function toggleMode(){selectMode(interactionMode.value==='quick'?'deep':'quick')}
watch(interactionMode,async(value,previous)=>{
 if(!value||value===previous||initializing.value||multi.value)return
 preferencesComplete.value=false
 if(!['mode','preferences','ready'].includes(step.value))return
 error.value=''
 if(value==='deep'){editingStep.value='preferences';return}
 editingStep.value='';activeStep.value=missingStep.value
 if(missingStep.value==='ready')await run()
})
async function recommendedRoute(){
 if(busy.value)return
 busy.value=true;error.value='';const guard=memoryEpoch.value,id=props.tripId,sequence=++requestSequence
 const active=()=>!disposed&&guard===memoryEpoch.value&&journey.trip?.id===id&&sequence===requestSequence
 try{
  await loadBasket()
  if(journey.basketError)throw new Error(journey.basketError)
  for(const [placeId,name] of [['pl_wb_000003','开封府'],['pl_wb_vendor_000001','第一楼寺后街店'],['pl_wb_000013','鼓楼夜市']]){
   if(!active())return
   if(!journey.choices.some(x=>x.kind==='place'&&x.id===placeId)&&!await saveChoice({...choice('place',placeId,name),source:'assistant'}))throw new Error(journey.basketError||'素材未加入，请重试')
  }
  if(!active())return
  result.value=null;editingStep.value='';activeStep.value='date'
 }catch(e){if(active())error.value=(e as Error).message}finally{if(active())busy.value=false}
}
function editStep(value:string){editingStep.value=value;error.value=''}
function applyLocation(role:'origin'|'destination',name:string,factId:string|null){form.value[role+'_place_id' as 'origin_place_id'|'destination_place_id']=factId;form.value[role+'_text' as 'origin_text'|'destination_text']=name}
const stale=computed(()=>!!result.value&&(result.value.basket_version!==journey.basketVersion||result.value.trip_version!==journey.trip?.version||submittedHash.value!==JSON.stringify(params())))
const chosenChanges=computed(()=>result.value?.planning?.alternative_changes?.[selected.value]||result.value?.planning?.changes)
const alternatives=computed(()=>result.value?.planning?.result.itineraries||[])
const multiPreview=computed(()=>alternatives.value.some(isMultiDayItinerary))
const singleAlternatives=computed(()=>alternatives.value.filter((p):p is SingleDayItinerary=>!isMultiDayItinerary(p)))
const omittedMulti=computed(()=>{const plan=alternatives.value[selected.value];if(!plan||!isMultiDayItinerary(plan))return [];const included=new Set(plan.days.flatMap(d=>[...d.itinerary.activities.map(a=>a.goal_id),...(d.itinerary.ongoing?[d.itinerary.ongoing.stop_id]:[])]));const goals=(multiForm.value?.goals||('schema_version' in result.value!.constraints?result.value!.constraints.goals:[])||[]) as {goal_id:string;target_id:string}[];return goals.filter(g=>!included.has(g.goal_id)).map(g=>result.value?.activity_names?.[g.goal_id]||nameOf(g.target_id))})
const omitted=(index:number)=>{const chosen=new Set(singleAlternatives.value[index]?.activities.map(x=>result.value?.goal_keys?.[x.goal_id]));return journey.choices.filter(x=>x.status==='standby'||!chosen.has(x.key)&&x.status!=='arranged')}
const omittedText=(index:number)=>{
 const items=new Map<string,string>(),remaining=omitted(index)
 for(const x of result.value?.unscheduled?.[String(index)]||[])if(!items.has(x.key))items.set(x.key,`${x.name}（${x.reason}）`)
 for(const x of [...remaining.filter(x=>x.status==='standby'),...remaining.filter(x=>x.status!=='standby')])if(!items.has(x.key))items.set(x.key,x.name+(x.status==='standby'?'（候补）':''))
 return [...items.values()].join('、')
}
function params():PlanningInput{
 if(multiForm.value)return copy(multiForm.value)
 const c={...form.value,business_date:day.value||null,starts_at:day.value&&start.value?day.value+'T'+start.value+':00+08:00':null,ends_at:day.value&&end.value?day.value+'T'+end.value+':00+08:00':null,budget_fen:String(budget.value).trim()?Math.round(Number(budget.value)*100):null}
 if(tripDays.value===1||!validDays.value)return c
 const days=Array.from({length:tripDays.value},(_,i)=>{
  const date=new Date(day.value+'T12:00:00+08:00');date.setUTCDate(date.getUTCDate()+i)
  const business_date=day.value?date.toISOString().slice(0,10):''
  return {day_id:'day:'+business_date,business_date,starts_at:start.value?business_date+'T'+start.value+':00+08:00':null,ends_at:end.value?business_date+'T'+end.value+':00+08:00':null,origin_place_id:c.origin_place_id,destination_place_id:c.destination_place_id,origin_text:c.origin_text,destination_text:c.destination_text}
 })
 return {schema_version:'city-constraints/2',start_date:day.value||null,end_date:days.at(-1)?.business_date||null,trip_days:tripDays.value,days,pace:c.pace,objective:c.objective,modes:c.modes,party_size:c.party_size,per_person_budget_fen:c.per_person_budget_fen,per_person_budget_daily:c.per_person_budget_daily,...(c.budget_fen!=null?{pricing:{party_size:c.party_size??null,vehicle_count:1,driving_basis:'taxi',total_budget_fen:c.budget_fen,per_person_budget_fen:c.per_person_budget_fen??null,per_person_daily:c.per_person_budget_daily??false,dining_per_person_day_fen:null,hard_limit:true}}:{})}
}
async function run(){success.value=false;if(initializing.value||busy.value||multiReadOnly.value||journey.trip?.id!==props.tripId)return;if(!multi.value&&(!validDays.value||tripDays.value>1&&coreMissing.value!=='ready'))return;if(!multi.value&&earlyTime.value&&!earlyTimeConfirmed.value){editingStep.value='time';error.value='开始时间为凌晨，请核对并确认后继续。';return}busy.value=true;error.value='';cancelled.value=false;result.value=null;const epoch=memoryEpoch.value,id=props.tripId,sequence=++requestSequence
 try{await loadBasket();if(disposed||sequence!==requestSequence||epoch!==memoryEpoch.value||journey.trip?.id!==id)return;if(journey.basketError)throw new Error(journey.basketError);const input=params();submittedHash.value=JSON.stringify(input);const r=await explorationApi.preview(id,journey.trip.version,journey.basketVersion,input,planningVersion.value);if(!disposed&&sequence===requestSequence&&epoch===memoryEpoch.value&&id===props.tripId&&!cancelled.value){result.value=normalize(r);planningVersion.value=r.planning_version??planningVersion.value;rounds.value=r.rounds||0;selected.value=0}}
 catch(e){if(!disposed&&epoch===memoryEpoch.value&&sequence===requestSequence){error.value=(e as Error).message;if(e instanceof ApiError&&e.status===409)initializing.value=true}}finally{if(!disposed&&epoch===memoryEpoch.value&&sequence===requestSequence)busy.value=false}}
function applyConstraints(c:PlanningInput){if('schema_version' in c){multiForm.value=copy(c) as typeof multiForm.value;return}multiForm.value=null;tripDays.value=1;daysConfirmed.value=!!c.business_date;form.value={...c,modes:[...c.modes]};day.value=c.business_date||'';start.value=c.starts_at?clock(c.starts_at):'';end.value=c.ends_at?clock(c.ends_at):'';budget.value=c.budget_fen!=null?String(c.budget_fen/100):''}
async function answer(reply:{option_id?:string;text?:string}){if(busy.value||stale.value||!journey.trip)return;busy.value=true;error.value='';const guard=memoryEpoch.value,id=props.tripId,sequence=++requestSequence
 try{const saved=await explorationApi.answer(id,journey.trip.version,journey.basketVersion,planningVersion.value,reply);if(disposed||guard!==memoryEpoch.value||sequence!==requestSequence||journey.trip?.id!==id)return;planningVersion.value=saved.version;rounds.value=saved.rounds;applyConstraints(saved.constraints!);result.value=null;await loadBasket();busy.value=false;await run()}
 catch(e){if(!disposed&&guard===memoryEpoch.value&&sequence===requestSequence){error.value=(e as Error).message;if(e instanceof ApiError&&e.status===409)initializing.value=true}}
 finally{if(!disposed&&guard===memoryEpoch.value&&sequence===requestSequence)busy.value=false}}
async function choose(option:PlanningResult['options'][number]){if(option.kind==='constraints'){editStep('time');return}await answer({option_id:option.id})}

async function confirm(){const p=result.value?.planning;if(!p?.candidate_id||stale.value||busy.value)return;confirming.value=true;busy.value=true;error.value='';const epoch=memoryEpoch.value
 try{await explorationApi.confirm(props.tripId,p.base_version,p.candidate_id,selected.value);if(disposed||epoch!==memoryEpoch.value||journey.trip?.id!==props.tripId)return;result.value=null;await loadJourney(true);await loadConfirmedRoute();error.value='';success.value=true;emit('complete')}catch(e){if(!disposed&&epoch===memoryEpoch.value)error.value=(e as Error).message}finally{if(!disposed&&epoch===memoryEpoch.value){busy.value=false;confirming.value=false}}}
const success=ref(false)
function resetLocal(){requestSequence++;busy.value=false;cancelled.value=true;result.value=null;success.value=false;error.value=''}
async function cancel(){if(confirming.value)return;const version=planningVersion.value;resetLocal();if(!journey.trip)return;busy.value=true;const guard=memoryEpoch.value
 try{const saved=await explorationApi.cancel(props.tripId,journey.trip.version,journey.basketVersion,version);if(!disposed&&guard===memoryEpoch.value){planningVersion.value=saved.version;rounds.value=0;emit('close')}}catch(e){if(!disposed&&guard===memoryEpoch.value){error.value=(e as Error).message;initializing.value=true}}finally{if(!disposed&&guard===memoryEpoch.value)busy.value=false}}
async function initialize(){error.value='';initializing.value=true;const epoch=memoryEpoch.value,id=props.tripId,sequence=++requestSequence;if(!(id in interactionModes.value))readMode(id)
 try{await loadJourney();if(journey.trip?.id!==id)throw new Error('当前旅程已变化，请重新打开规划');const draft=journey.trip.draft;const saved=await explorationApi.constraints(id);if(disposed||sequence!==requestSequence||epoch!==memoryEpoch.value||journey.trip?.id!==id)return;const c=saved.constraints;if(c)applyConstraints(c);else if(isMultiDayDraft(draft))throw new Error('多天规划条件暂未读取，请从小汴梁继续安排。');else applyConstraints({business_date:draft.business_date,starts_at:draft.starts_at,ends_at:draft.ends_at,origin_place_id:draft.origin_place_id,destination_place_id:draft.destination_place_id,pace:draft.pace||'standard',objective:draft.objective||'balanced',modes:[...draft.modes],budget_fen:null});planningVersion.value=saved.version;rounds.value=saved.rounds;result.value=props.workflowResult?normalize({...props.workflowResult,planning_version:saved.version}):saved.result?normalize(saved.result):null;submittedHash.value=JSON.stringify(params());preferencesComplete.value=!!result.value&&!personalMissing.value||!!c&&'party_size' in c&&!!c.party_size;activeStep.value=missingStep.value;initializing.value=false}
 catch(e){if(!disposed&&epoch===memoryEpoch.value)error.value='规划条件未加载完成：'+(e as Error).message}}
async function resume(){await initialize();if(!disposed&&!initializing.value&&props.autoPlan&&missingStep.value==='ready')await run()}
watch(()=>props.revision,()=>void resume())
onMounted(resume)
onBeforeUnmount(()=>emit('busy',false))
onBeforeUnmount(()=>{disposed=true;requestSequence++})
watch(memoryEpoch,()=>{resetLocal();error.value='记录已清除';busy.value=false})
</script>
<template><section ref="conversation" class="basket-planning conversation-planning" aria-label="素材篮规划协商">
 <div class="planning-materials"><p v-if="materials.length">这次想去、想吃的：<strong>{{materials.map(x=>x.name).join('、')}}</strong></p><p v-else>接着安排这一程</p><RouterLink to="/basket">调整素材</RouterLink></div>
 <p v-if="initializing" role="status">正在接上你的安排…</p>
 <template v-else-if="!success">
 <template v-if="!multi"><div v-if="day||start||form.origin_place_id||form.destination_place_id||form.origin_text||form.destination_text" class="planning-known" aria-label="已提供的安排">
  <button v-if="day" type="button" :disabled="busy" @click="editStep('date')">{{day}}</button>
  <button v-if="day" type="button" :disabled="busy" @click="editStep('days')">{{tripDays}} 天</button>
  <button v-if="start&&end" type="button" :disabled="busy" @click="editStep('time')">{{start}}—{{end}}</button>
  <button v-if="form.origin_place_id||form.origin_text" type="button" :disabled="busy" @click="editStep('origin')">从{{form.origin_place_id?nameOf(form.origin_place_id):form.origin_text}}</button>
  <button v-if="form.destination_place_id||form.destination_text" type="button" :disabled="busy" @click="editStep('destination')">回到{{form.destination_place_id?nameOf(form.destination_place_id):form.destination_text}}</button>
  <button v-if="validParty" type="button" :disabled="busy" @click="editStep('party')">{{partySize}} 人</button>
  <button v-if="interactionMode" type="button" :disabled="busy" @click="toggleMode">{{interactionMode==='deep'?'深度互动':'快速安排'}}</button>
  <button type="button" :disabled="busy" @click="editStep('preferences')">{{{relaxed:'轻松',standard:'适中',compact:'紧凑'}[form.pace]}} · {{{walking:'步行',driving:'驾车',transit:'公共交通'}[form.modes[0]]}}</button>
 </div>
 <form v-if="!visibleResult||editingStep" class="planning-question" @submit.prevent="advance">
  <p class="planning-question-text">{{question}}</p>
  <div v-if="step==='empty'" class="planning-empty-actions"><RouterLink class="button secondary" to="/">先去发现开封</RouterLink><button type="button" class="button primary" :disabled="busy" @click="recommendedRoute">用推荐的开封夜游路线</button></div>
  <template v-else-if="step==='date'"><div class="planning-quick"><button type="button" :disabled="busy" @click="chooseDay(0)">今天</button><button type="button" :disabled="busy" @click="chooseDay(1)">明天</button></div><label>旅行日期<input v-model="day" type="date" required :disabled="busy"></label></template>
  <template v-else-if="step==='days'"><div class="planning-picks" role="group" aria-label="快捷选择天数"><button v-for="n in maxTripDays" :key="n" type="button" :aria-pressed="tripDays===n" :disabled="busy" @click="chooseDays(n)">{{n}}天</button></div><label>游玩天数<input v-model.number="tripDays" type="number" min="1" :max="maxTripDays" step="1" required :disabled="busy"></label></template>
  <div v-else-if="step==='time'" class="planning-time-pair"><label>开始时间<input v-model="start" type="time" required :disabled="busy"></label><label>最晚结束<input id="planning-end" v-model="end" type="time" required :disabled="busy"></label></div>
  <template v-else-if="step==='origin'||step==='destination'">
   <label v-if="places.length">{{step==='origin'?'出发地点':'返程地点'}}<select :value="step==='origin'?form.origin_place_id:form.destination_place_id" :disabled="busy" @change="step==='origin'?form.origin_place_id=($event.target as HTMLSelectElement).value:form.destination_place_id=($event.target as HTMLSelectElement).value"><option :value="null">请选择地点</option><option v-for="p in places" :key="p.record.fact_id" :value="p.record.fact_id">{{p.record.name}}</option></select></label>
   <div class="planning-location-fields">
    <label>{{places.length?'或查找其他出发地点':'出发地点'}}<input v-model="form.origin_text" @input="form.origin_place_id=null" type="text" maxlength="120" placeholder="车站、酒店或具体地址" :required="(enteringLocations||step==='origin')&&!form.origin_place_id" :disabled="busy"></label>
    <LocationPicker :query="form.origin_text||''" label="出发地点" :disabled="busy" @confirmed="(name,factId)=>applyLocation('origin',name,factId)"/>
    <label>{{places.length?'或查找其他返回地点':'返回地点'}}<input v-model="form.destination_text" @input="form.destination_place_id=null" type="text" maxlength="120" placeholder="最后回到哪里" :required="(enteringLocations||step==='destination')&&!form.destination_place_id" :disabled="busy"></label>
    <LocationPicker :query="form.destination_text||''" label="返回地点" :disabled="busy" @confirmed="(name,factId)=>applyLocation('destination',name,factId)"/>
   </div>
  </template>
  <template v-else-if="step==='party'"><div class="planning-picks" role="group" aria-label="快捷选择人数"><button v-for="n in 5" :key="n" type="button" :aria-pressed="partySize===n" :disabled="busy" @click="chooseParty(n)">{{n}} 人</button></div><label>旅行人数<input v-model.number="partySize" type="number" min="1" max="50" step="1" required :disabled="busy"></label></template>
  <div v-else-if="step==='mode'" class="planning-mode-cards" role="group" aria-label="选择互动模式"><button v-for="mode in (['deep','quick'] as const)" :key="mode" type="button" :aria-pressed="interactionMode===mode" :disabled="busy" @click="selectMode(mode)"><strong>{{mode==='deep'?'深度互动':'快速安排'}}</strong><span>{{mode==='deep'?'我先问你几个偏好，再排行程':'按默认偏好直接生成，之后随时可改'}}</span></button></div>
  <template v-else-if="step==='preferences'">
   <fieldset class="planning-preference" :disabled="busy"><legend>游玩节奏</legend><div class="planning-picks"><button v-for="option in ([['relaxed','轻松'],['standard','适中'],['compact','紧凑']] as const)" :key="option[0]" type="button" :aria-pressed="form.pace===option[0]" @click="form.pace=option[0]">{{option[1]}}</button></div></fieldset>
   <fieldset class="planning-preference" :disabled="busy"><legend>景点之间怎么走</legend><div class="planning-picks"><button v-for="option in ([['walking','步行'],['transit','公交地铁'],['driving','打车']] as const)" :key="option[0]" type="button" :aria-pressed="form.modes[0]===option[0]" @click="form.modes=[option[0]]">{{option[1]}}</button></div></fieldset>
   <fieldset class="planning-preference" :disabled="busy"><legend>更在意什么</legend><div class="planning-picks"><button v-for="option in ([['balanced','综合平衡'],['less_walking','少走路'],['lower_cost','少花钱'],['less_waiting','少等待']] as const)" :key="option[0]" type="button" :aria-pressed="form.objective===option[0]" @click="form.objective=option[0]">{{option[1]}}</button></div></fieldset>
   <label>预算上限（选填）<input v-model="budget" type="number" min="0" max="1000000" placeholder="人民币" :disabled="busy"></label><p v-if="form.per_person_budget_fen!=null" class="per-person-budget">已记下预算：每人{{form.per_person_budget_daily?'每天':''}} {{form.per_person_budget_fen/100}} 元{{form.party_size?`（${form.party_size} 人）`:''}}，方案出来后按已知费用核对。</p>
  </template>
  <label v-if="earlyTime" class="early-time-confirm"><input v-model="earlyTimeConfirmed" type="checkbox" :disabled="busy">确认 {{start}} 为实际的凌晨出发时间（北京时间）</label>
  <button v-if="!['empty','mode'].includes(step)" class="button primary" :disabled="busy||!canContinue">{{busy?'正在整理…':step==='ready'?'生成安排':'继续'}}<ArrowRight :size="16"/></button>
 </form></template>
 <form v-else-if="multiForm" class="planning-question multi-day-conditions" aria-label="每日规划条件" @submit.prevent="run"><fieldset :disabled="busy||multiReadOnly"><h3>{{multiForm.start_date}}—{{multiForm.end_date}} · {{multiForm.days.length}} 天</h3><p>修改日期范围请告诉小汴梁；以下调整只用于重新预览，确认整套方案后才改变正式路线。</p><section v-for="d in multiForm.days" :key="d.day_id" class="day-conditions"><h4>{{d.business_date}}</h4><div class="planning-time-pair"><label>当天开始<input type="time" :value="d.starts_at?clock(d.starts_at):''" :disabled="busy||multiReadOnly" @input="updateDayTime(d,'starts_at',($event.target as HTMLInputElement).value)"></label><label>当天结束<input type="time" :value="d.ends_at?clock(d.ends_at):''" :disabled="busy||multiReadOnly" @input="updateDayTime(d,'ends_at',($event.target as HTMLInputElement).value)"></label></div><label>当天出发地点<select v-model="d.origin_place_id" :disabled="busy||multiReadOnly"><option :value="null">{{d.origin_text||'待选择已有地点'}}</option><option v-for="p in places" :key="p.record.fact_id" :value="p.record.fact_id">{{p.record.name}}</option></select></label><label>当天终点<select v-model="d.destination_place_id" :disabled="busy||multiReadOnly"><option :value="null">{{d.destination_text||'待选择已有地点'}}</option><option v-for="p in places" :key="p.record.fact_id" :value="p.record.fact_id">{{p.record.name}}</option></select></label><p v-if="!d.starts_at||!d.ends_at||!d.origin_place_id||!d.destination_place_id">这一天仍需补齐时段与起终点。</p></section><label v-for="o in multiForm.overnights||[]" :key="o.from_day_id">过夜地点（同时用于前日终点和次日起点）<select :value="o.place_id" :disabled="busy||multiReadOnly" @change="updateOvernight(o,($event.target as HTMLSelectElement).value)"><option :value="null">待选择已有地点</option><option v-for="p in places" :key="p.record.fact_id" :value="p.record.fact_id">{{p.record.name}}</option></select></label><BudgetControls v-model="multiForm.pricing"/><p v-for="item in (multiForm.clarifications as string[]||[])" :key="item">{{item}}</p><p v-for="item in (multiForm.range_impacts as string[]||[])" :key="item">{{item}}</p><p v-if="(multiForm.pending_fields as unknown[]||[]).length">还有未明确适用日的条件，请告诉小汴梁这些条件适用于哪一天。</p><p v-if="multiReadOnly">途中调整请告诉小汴梁“今天剩余怎么改”“今天及后续天一起改”或“明天/第几天怎么改”，也可到行程页选择范围后预览。</p><button v-else type="submit" class="button primary" :disabled="busy||multiReadOnly||!readyMulti">重新预览整套方案</button></fieldset></form></template>
 <p v-if="busy" role="status">{{confirming?'正在提交已确认的方案…':'正在检查目标、时间和交通资料…'}}</p><p v-if="error" class="error-state" role="alert">{{error}}<button v-if="initializing" type="button" @click="initialize">重新读取条件</button></p><section v-if="success" class="planning-success" role="status"><Check :size="24"/><h3>已确认并更新正式路线</h3><RouterLink :to="'/city-trip/'+tripId" class="button primary">查看行程</RouterLink></section><section v-if="result&&!personalMissing&&!editingStep" class="planning-assessment" :class="result.status" aria-label="规划检测结果"><span class="assessment-status">{{result.status==='feasible'?'已有经过校验的候选':result.status==='infeasible'?'需要你决定取舍':'信息不足 · 待添加'}}</span><h3>{{result.reply}}</h3><div v-if="(form.origin_text||form.destination_text)&&!form.origin_place_id" class="location-recheck"><button type="button" class="text-link" :disabled="busy" @click="editStep('origin')">核对出发与返回地点</button></div><ul v-if="result.issues.length" class="planning-issues"><li v-for="(issue,i) in result.issues" :key="i"><strong>{{issue.code}}</strong>：{{issue.message}}</li></ul><p v-if="stale" role="alert" class="trip-warning">素材篮、条件或行程已变化，旧预览失效。请重新计算。</p><div v-if="result.status==='infeasible'&&rounds<2" class="negotiation-options"><button v-for="option in result.options" :key="option.id" type="button" :disabled="busy||stale" @click="choose(option)">{{option.label}} <ArrowRight :size="15"/></button></div><p v-else-if="result.status==='infeasible'&&rounds>=2">仍有冲突，可以调整时间或素材优先级。</p><p v-for="note in (multiPreview&&'hotel_notices' in result?result.hotel_notices||[]:[])" :key="note" class="hotel-pending-note">{{note}}</p><PlanPreview v-if="multiPreview" :plans="alternatives" :selected="selected" :disabled="busy||stale" :names="result.activity_names" :changes="chosenChanges" @select="selected=$event"/><p v-if="multiPreview&&omittedMulti.length">暂不安排：{{omittedMulti.join('、')}}。未纳入的素材仍保留。</p><div v-if="!multiPreview" class="plan-alternatives"><button v-for="(plan,index) in singleAlternatives" :key="index" class="plan-option" :class="{selected:selected===index}" :aria-pressed="selected===index" :disabled="busy||stale" type="button" @click="selected=index"><header><span>方案 {{index+1}} · {{objectiveLabel(plan.objectives)}} · {{plan.activities.length}} 项</span><Check v-if="selected===index" :size="18"/></header><small v-if="plan.objectives?.length===0">{{alternativeDifference(plan,singleAlternatives[0])}}</small><small v-if="plan.objectives?.includes('lower_cost')&&plan.cost_unknown">费用资料不全，只按已知费用比较</small><ol><li v-for="stop in plan.activities" :key="stop.goal_id"><time>{{clock(stop.starts_at)}}</time><div><strong>{{result.activity_names?.[stop.goal_id]||nameOf(stop.place_id)}}</strong><small>{{Math.round(stop.duration_seconds/60)}} 分钟{{stop.required?' · 保留必去':''}}</small></div></li></ol><div class="plan-metrics"><span>游览与用餐 {{Math.round(plan.activities.reduce((n,a)=>n+a.duration_seconds,0)/60)}} 分钟</span><span><Clock3 :size="14"/>交通 {{Math.ceil(plan.travel_seconds/60)}} 分钟</span><span><Footprints :size="14"/>步行 {{Math.round(plan.walking_distance_m)}} 米</span><span>预计 {{clock(plan.returns_at)}} 返程</span><span>{{plan.cost_unknown?'费用待添加':'已知费用上限 ¥'+(plan.known_cost_upper_fen/100).toFixed(0)}}</span></div><p v-if="omittedText(index)">暂不安排：{{omittedText(index)}}<small>候补、未选择或在当前约束下未纳入；素材不会删除。</small></p><small>{{plan.evidence_mode==='synthetic'?'隔离演示资料':result.planning?.result.warnings?.some(w=>w.startsWith('演示模式'))?'演示模式：参考资料参与规划，交通为预计耗时':'交通为有依据的估计，非到达保证'}}</small></button></div><p v-for="warning in result.planning?.result.warnings||[]" :key="warning" class="pending-copy">{{warning}}</p><ul v-if="chosenChanges&&!multiPreview" class="planning-change-list" aria-label="正式行程变更对比"><li v-if="chosenChanges.added.length">新增：{{chosenChanges.added.map(id=>result!.activity_names?.[id]||'待核对活动').join('、')}}</li><li v-if="chosenChanges.removed.length">移除：{{chosenChanges.removed.map(id=>result!.activity_names?.[id]||'待核对活动').join('、')}}</li><li v-if="chosenChanges.reordered.length">调整顺序：{{chosenChanges.reordered.map(id=>result!.activity_names?.[id]||'待核对活动').join('、')}}</li><li v-for="change in chosenChanges.start_changes||[]" :key="change.goal_id">{{result.activity_names?.[change.goal_id]||'活动'}}：{{clock(change.before)}} → {{clock(change.after)}}</li><li v-for="change in chosenChanges.target_changes||[]" :key="'target:'+change.goal_id">更换地点或场次：{{result.activity_names?.[change.goal_id]||'活动'}}，请核对上方新安排。</li></ul><p v-if="!multiPreview&&chosenChanges?.removed?.length" class="trip-warning">将移除 {{chosenChanges!.removed.length}} 个原活动，确认前请比较新安排。</p><div v-if="alternatives.length" class="planning-confirm"><button type="button" class="button primary" :disabled="busy||stale" @click="confirm">{{multiPreview?'确认整套方案':'确认方案 '+(selected+1)+'，更新正式行程'}}</button><button type="button" class="text-link" :disabled="busy||initializing" @click="cancel">保留原行程</button></div><div v-else class="planning-confirm"><button type="button" class="text-link" :disabled="busy||initializing" @click="run"><RefreshCw :size="14"/>重新核验</button><RouterLink to="/basket" class="text-link">整理素材篮</RouterLink><button type="button" class="text-link" @click="cancel">取消本次规划</button></div></section><button v-if="busy&&!confirming" type="button" class="text-link" @click="cancel">取消等待，保留原行程</button></section></template>

<style scoped>
.multi-day-conditions fieldset{border:0;padding:0;min-width:0}.day-conditions{padding:12px 0;border-bottom:1px solid #ded4c2}.conversation-planning{border:0;padding-top:0}.planning-materials{display:flex;align-items:start;gap:12px;margin:0 0 22px;padding-bottom:16px;border-bottom:1px solid #dfd6c5;font-size:13px;color:#6c7d69}.planning-materials p{flex:1;margin:0;line-height:1.8}.planning-materials strong{font-weight:500;color:#29483b}.planning-materials a{white-space:nowrap;font-size:12px;min-height:32px;padding:4px 0}.planning-known{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:20px}.planning-known button{font:inherit;font-size:12px;border:1px solid #dbd5c5;border-radius:16px;padding:7px 11px;color:#777b63;min-height:36px}.planning-question,.planning-assessment{scroll-margin-bottom:100px}.planning-question{display:grid;gap:14px!important}.planning-question-text{font:22px/1.6 'Songti SC',serif;color:#29483b;margin:0 0 4px!important}.planning-question label{font-size:12px;color:#8d7d65}.planning-question input,.planning-question select{display:block;width:100%;min-width:0;box-sizing:border-box;margin-top:7px;font-size:16px;min-height:44px;background:#fffcf5}.planning-question .button{justify-self:start;min-height:44px;background:#29483b;color:#fff8e9}.planning-quick{display:flex;gap:10px}.planning-quick button{padding:10px 24px;border:1px solid #c9cdbb;border-radius:6px;font-size:14px;color:#456653;min-height:44px}.planning-time-pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}.planning-question .pending-copy{margin:0;color:#97856b;font-size:13px}.planning-issues{margin:12px 0;padding-left:20px;color:#8a6e4d;font-size:13px;line-height:1.8}.planning-issues strong{color:#6f573d;font-weight:600}
.per-person-budget{margin:0;font-size:13px;line-height:1.7;color:#6c7d69}.planning-location-fields{display:grid;gap:14px}.early-time-confirm{display:flex;align-items:center;gap:8px}.planning-question .early-time-confirm input{width:20px;min-height:24px;margin:0;flex-shrink:0}
.planning-picks,.planning-empty-actions{display:flex;flex-wrap:wrap;gap:8px}.planning-picks button,.planning-mode-cards button{min-height:44px;padding:10px 14px;border:1px solid #c9cdbb;border-radius:6px;color:#456653;background:#fffcf5;font:inherit;font-size:14px}.planning-picks button[aria-pressed=true],.planning-mode-cards button[aria-pressed=true]{background:#e9efe3;border-color:#77917a;color:#29483b}.planning-preference{border:0;margin:0;padding:0;min-width:0}.planning-preference legend{font-size:13px;color:#8d7d65;margin-bottom:8px}.planning-mode-cards{display:grid;gap:12px}.planning-mode-cards button{display:grid;gap:6px;text-align:left}.planning-mode-cards span{font-size:13px;line-height:1.7}.planning-empty-actions .button{white-space:normal}
</style>
