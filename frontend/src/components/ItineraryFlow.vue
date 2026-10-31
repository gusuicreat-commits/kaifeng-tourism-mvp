<script setup lang="ts">
import {computed} from 'vue'
import {Check,Flag,MapPin,Landmark,Utensils,Ticket,Coffee,LockKeyhole,Footprints,Car,BusFront,ArrowDown} from 'lucide-vue-next'
import {draftDays,emptyCityDraft,type CityTripState} from '../cityApi'
import type {SingleDayItinerary,PlannedActivity,ScheduledLeg} from '../explorationApi'
import {clock,journey,nameOf} from '../journey'

const props=defineProps<{trip:CityTripState;itinerary:SingleDayItinerary;dayId?:string;actionable?:boolean}>()
type Point={id:string;place:string|null;kind:string;title:string;status:string;time:string|null;end?:string|null;duration?:number|null;locked?:boolean;state?:string;incoming?:ScheduledLeg;history?:boolean}
const labels:Record<string,string>={visit:'游览',dining:'用餐',show:'演出',rest:'休息',return:'返程',origin:'出发',resume:'接续行程'}
const icons:Record<string,typeof MapPin>={visit:Landmark,dining:Utensils,show:Ticket,rest:Coffee,return:Flag,origin:MapPin,resume:MapPin}
const travelLabels:Record<string,string>={walking:'步行',driving:'驾车',transit:'公共交通'}
const travelIcons:Record<string,typeof Footprints>={walking:Footprints,driving:Car,transit:BusFront}
function title(activity:PlannedActivity){
 if(activity.kind==='show'&&activity.session_id){
  const session=journey.catalog.find(x=>x.record.kind==='show_session'&&x.record.fact_id===activity.session_id)
  const program=journey.catalog.find(x=>x.record.kind==='program'&&x.record.fact_id===session?.record.program_id)
  if(program?.record.name)return program.record.name
 }
 return nameOf(activity.place_id)
}
const points=computed(()=>{
 const route=props.itinerary,draft=draftDays(props.trip.draft).find(d=>d.day_id===props.dayId)||draftDays(props.trip.draft)[0]||emptyCityDraft(),firstLeg=route.legs[0],lastLeg=route.legs.at(-1)
 const history=draft.stops.filter(s=>s.progress!=='pending'),ongoing=route.ongoing
 const result:Point[]=[{id:'origin',place:history.length||ongoing?draft.origin_place_id:firstLeg?.journey.travel.origin_place_id||draft.origin_place_id,
  kind:'origin',title:'',status:history.length||ongoing?'原定出发':'按计划出发',time:history.length||ongoing?draft.starts_at:firstLeg?.departs_at||draft.starts_at}]
 for(const s of history)result.push({id:s.stop_id,place:s.place_id,kind:s.kind,title:nameOf(s.place_id),
  status:s.progress==='completed'?'已游玩':'已跳过',time:s.actual_start,end:s.actual_end,state:s.progress,history:true})
 if(ongoing){
  const activity=ongoing.original_activity,stop=draft.stops.find(s=>s.stop_id===ongoing.stop_id)
  result.push({id:ongoing.stop_id,place:ongoing.place_id||activity?.place_id||stop?.place_id||null,kind:activity?.kind||stop?.kind||'visit',
   title:activity?title(activity):nameOf(stop?.place_id),status:props.actionable===false?'原计划进行项':'正在进行',time:activity?.starts_at||null,end:ongoing.ends_at,state:'ongoing',locked:activity?.required})
 }else if(history.length&&firstLeg)result.push({id:'resume',place:firstLeg.journey.travel.origin_place_id,kind:'resume',title:'',status:'剩余行程从此接续',time:firstLeg.departs_at})
 const done=new Set(history.map(s=>s.stop_id));if(ongoing)done.add(ongoing.stop_id)
 route.activities.forEach((a,i)=>{
  if(done.has(a.goal_id))return
  result.push({id:a.goal_id,place:a.place_id,kind:a.kind,title:title(a),status:'待游玩',time:a.starts_at,end:a.departs_at,
   duration:a.duration_seconds,locked:a.required,state:'pending',incoming:route.legs[i]})
  done.add(a.goal_id)
 })
 result.push({id:'destination',place:lastLeg?.journey.travel.destination_place_id||draft.destination_place_id,kind:'return',title:'',status:'预计抵达',time:route.returns_at,
  incoming:route.legs.length>route.activities.length?lastLeg:undefined})
 const next=result.find(p=>p.state==='pending');if(next&&!ongoing&&props.actionable!==false){next.state='next';next.status='下一站'}
 return result
})
// 园内节目：确认后素材篮把节目关联到所属景区的停靠点，这里显示在该景区下方。
const dayDraft=computed(()=>draftDays(props.trip.draft).find(d=>d.day_id===props.dayId)||draftDays(props.trip.draft)[0]||emptyCityDraft())
function programsAt(point:Point){
 if(!point.place||['origin','return','resume'].includes(point.kind))return []
 const ids=new Set(dayDraft.value.stops.filter(s=>s.place_id===point.place).map(s=>s.stop_id))
 return journey.choices.filter(c=>c.kind==='program'&&c.stop_ids?.some(id=>ids.has(id)))
}
function legBetween(from:Point,to:Point){const leg=to.incoming;return leg&&leg.journey.travel.origin_place_id===from.place&&leg.journey.travel.destination_place_id===to.place?leg:null}
function travelText(leg:ScheduledLeg){const seconds=(Date.parse(leg.arrives_at)-Date.parse(leg.departs_at))/1000
 if(leg.journey.time_basis==='provider_estimate'&&leg.journey.estimated_seconds!=null){
  const estimate=Math.ceil(leg.journey.estimated_seconds/60),buffer=Math.ceil((leg.journey.planning_buffer_seconds||0)/60)
  return estimate===0?'原地衔接 · 另计进出园预留':`高德预计${estimate}分钟${buffer?' + 预留'+buffer+'分钟':''}`
 }
 if(!Number.isFinite(seconds)||seconds<0)return '转场时间待核验'
 return seconds===0?'原地衔接 · 无需转场':`${travelLabels[leg.journey.travel.mode]||'交通'}约 ${Math.ceil(seconds/60)} 分钟`
}
</script>

<template>
 <ol class="journey-timeline journey-flow" aria-label="行程流程安排">
  <li v-for="(point,index) in points" :key="index+':'+point.id" class="flow-point" :class="[point.state,{'flow-activity':!!point.state,'flow-endpoint':!point.state}]" :aria-current="point.state==='next'||point.state==='ongoing'?'step':undefined">
   <div class="flow-time"><span class="timeline-clock">{{point.history&&!point.time?(point.state==='skipped'?'—':'未记录'):clock(point.time)}}</span><small v-if="point.end">{{clock(point.end)}} {{point.state==='completed'?'结束':point.state==='ongoing'?'预计结束':'离开'}}</small></div>
   <span class="flow-marker" aria-hidden="true"><Check v-if="point.state==='completed'" :size="13"/><component :is="icons[point.kind]||MapPin" v-else :size="13"/></span>
   <article class="flow-card">
    <div class="flow-card-meta"><span>{{labels[point.kind]||'活动'}}</span><span class="flow-status">{{point.status}}</span></div>
    <h3><RouterLink v-if="point.place" :to="'/place/'+encodeURIComponent(point.place)">{{point.title||nameOf(point.place)}}</RouterLink><span v-else>地点待核验</span><LockKeyhole v-if="point.locked" :size="13" aria-label="必去"/></h3>
    <p v-if="point.kind==='show'&&point.title!==nameOf(point.place)" class="flow-place">{{nameOf(point.place)}}</p>
    <p v-if="point.duration!=null" class="flow-duration">{{point.kind==='dining'?'用餐':point.kind==='show'?'演出':'停留'}} {{Math.round(point.duration/60)}} 分钟</p><p v-for="c in programsAt(point)" :key="c.key" class="flow-program">园内观看《{{c.name.replace(/^《|》$/g,'')}}》 · 场次以景区当日公告为准</p>
    <RouterLink v-if="point.place&&point.state!=='completed'&&point.state!=='skipped'&&point.state" class="flow-location" :to="'/guide?mode=city&place='+encodeURIComponent(point.place)">查看位置 <MapPin :size="12"/></RouterLink>
   </article>
   <div v-if="index<points.length-1" class="flow-transfer">
    <template v-if="legBetween(point,points[index+1])"><component :is="travelIcons[legBetween(point,points[index+1])!.journey.travel.mode]||ArrowDown" :size="14"/><span>{{travelText(legBetween(point,points[index+1])!)}}</span></template>
    <span v-else-if="!points[index+1].history&&points[index+1].kind!=='resume'&&points[index+1].state!=='ongoing'">转场资料待核验</span>
   </div>
  </li>
 </ol>
</template>

<style scoped>
.flow-program{margin:6px 0 0;padding-left:9px;border-left:2px solid #c9b98f;font-size:12px;line-height:1.7;color:#6b7e5e}
.journey-timeline.journey-flow{--clock:62px;--rail:24px;--gap:12px;list-style:none;margin:22px 0 8px;padding:0}
.journey-flow>.flow-point{position:relative;display:grid;grid-template-columns:var(--clock) var(--rail) minmax(0,1fr);column-gap:var(--gap);min-height:0;isolation:isolate}
.journey-flow>.flow-point:not(:last-child):before{content:'';position:absolute;left:calc(var(--clock) + var(--gap) + var(--rail)/2 - 1px);top:25px;bottom:-25px;border-left:1px solid #c8baa0;z-index:-1}
.journey-flow>.flow-point:not(:last-child):after{content:'';position:absolute;left:calc(var(--clock) + var(--gap) + var(--rail)/2 - 3px);top:auto;bottom:14px;width:6px;height:6px;border-right:1px solid #a18b66;border-bottom:1px solid #a18b66;background:none;transform:rotate(45deg)}
.flow-time{padding-top:17px;text-align:right;min-width:0}.flow-time .timeline-clock{display:block;padding:0;color:#6f715d;font:17px/1.3 "Songti SC",serif}.flow-time small{display:block;font-size:10px;line-height:1.6;color:#a0927c;margin-top:6px;white-space:nowrap}
.flow-marker{width:24px;height:24px;box-sizing:border-box;margin-top:16px;display:grid;place-items:center;border:1px solid #c7b99f;border-radius:50%;background:#f7f1e6;color:#997d52}
.flow-card{min-width:0;padding:18px 20px;background:#fffbf3;border:1px solid #dfd3bf;border-radius:3px 18px 3px 18px;box-shadow:0 5px 14px #66513409}
.flow-card-meta{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:6px;color:#9b8462;font-size:11px;letter-spacing:.08em}.flow-status{font-size:10px;letter-spacing:0}
.flow-card h3{display:flex;gap:7px;align-items:center;margin:10px 0 0;font:24px/1.5 "Songti SC","STSong",serif;color:#3a5145;overflow-wrap:anywhere}.flow-card h3 svg{flex-shrink:0;color:#a17b47}
.flow-duration,.flow-place{font-size:12px;color:#8b806b;line-height:1.8;margin:7px 0 0}.flow-location{display:flex;align-items:center;gap:5px;width:fit-content;min-height:40px;margin:9px 0 -7px;font-size:12px;color:#5c7863}
.flow-transfer{grid-column:3;display:flex;gap:7px;align-items:center;min-height:48px;padding:6px 3px;box-sizing:border-box;font-size:11px;color:#948367}.flow-transfer svg{flex-shrink:0}
.flow-endpoint .flow-card{background:#f1ebde;padding-block:14px;box-shadow:none}.flow-endpoint h3{font-size:20px;margin-top:6px}
.next .flow-card,.ongoing .flow-card{border-color:#728c72;background:#eef2e7;box-shadow:0 5px 16px #4260430d}.next .flow-marker,.ongoing .flow-marker{background:#355c48;border-color:#355c48;color:#fff8e9;box-shadow:0 0 0 4px #e7ecde}.next .flow-status,.ongoing .flow-status{color:#42654f;font-weight:600}.completed .flow-marker{background:#9aa98f;border-color:#9aa98f;color:#fff}.completed .flow-card{background:#f5f3ea}.skipped .flow-card{border-style:dashed;box-shadow:none}.skipped h3{color:#8f8879;text-decoration:line-through}
@media(max-width:760px){.journey-timeline.journey-flow{--clock:46px;--rail:22px;--gap:8px;margin-top:18px}.flow-marker{width:22px;height:22px}.flow-time .timeline-clock{font-size:15px}.flow-time small{font-size:9px}.flow-card{padding:14px 13px}.flow-card h3{font-size:21px}.flow-card-meta{font-size:10px}.flow-endpoint h3{font-size:19px}.flow-transfer{font-size:10px;min-height:45px;gap:5px}.flow-duration,.flow-place{font-size:11px}}
</style>
