<script setup lang="ts">
import BudgetBreakdown from './BudgetBreakdown.vue'
import {isMultiDayItinerary} from '../explorationApi'
import {computed} from 'vue'
import {dayLabel,objectiveLabel,routeDays,type Itinerary,type PlanChanges} from '../explorationApi'
import {clock,nameOf} from '../journey'
interface DayChanges extends Partial<PlanChanges>{day_id?:string;business_date?:string;walking_distance_m_delta?:number;travel_seconds_delta?:number}
interface TripChanges extends DayChanges{days?:DayChanges[]}
const props=defineProps<{plans:Itinerary[];selected:number;disabled?:boolean;names?:Record<string,string>;changes?:TripChanges|null}>()
const emit=defineEmits<{select:[index:number]}>()
const chosen=computed(()=>props.plans[props.selected])
const budget=computed(()=>chosen.value&&isMultiDayItinerary(chosen.value)?chosen.value.budget:null)
const days=computed(()=>chosen.value?routeDays({trip_id:'preview',version:0,route_version:0,itinerary:chosen.value}):[])
const vehicleCosts=computed(()=>days.value.flatMap(d=>d.legs).filter(l=>l.journey.travel.mode==='driving'&&l.journey.vehicle_cost_fen!=null).map(l=>l.journey.vehicle_cost_fen!))
const vehicleTotal=computed(()=>vehicleCosts.value.reduce((sum,fen)=>sum+fen,0)/100)
const changes=computed(()=>props.changes?.days||[])
const count=(plan:Itinerary)=>routeDays({trip_id:'preview',version:0,route_version:0,itinerary:plan}).reduce((n,d)=>n+d.activities.length,0)
const dayActivities=(d:ReturnType<typeof routeDays>[number])=>d.ongoing?.original_activity?[d.ongoing.original_activity,...d.activities]:d.activities
const label=(id:string)=>props.names?.[id]||nameOf(days.value.flatMap(d=>d.activities).find(a=>a.goal_id===id)?.place_id)||'待核对活动'
const moment=(iso:string)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(iso))
</script>
<template><div class="multi-plan-preview" aria-label="整套行程方案">
 <div class="plan-alternatives"><button v-for="(plan,index) in plans" :key="index" type="button" class="plan-option" :class="{selected:selected===index}" :aria-pressed="selected===index" :disabled="disabled" @click="emit('select',index)">方案 {{index+1}} · {{objectiveLabel(plan.objectives)}} · {{count(plan)}} 项</button></div>
 <template v-if="chosen"><p v-if="chosen.evidence_mode==='synthetic'" class="pending-copy">隔离演示资料</p><p class="whole-trip-metrics">全程步行 {{Math.round(chosen.walking_distance_m)}} 米 · {{budget?'已计入费用':'已知费用上限'}} ¥{{(chosen.known_cost_upper_fen/100).toFixed(2)}}{{chosen.cost_unknown?' · 部分费用待添加':''}}<template v-if="vehicleTotal>0&&!(isMultiDayItinerary(chosen)&&chosen.budget)"> · 打车参考约 ¥{{Number(vehicleTotal.toFixed(2))}}/车（高德估计，未计入上方费用）</template></p><p v-if="chosen.objectives?.includes('lower_cost')&&chosen.cost_unknown">费用资料不全，只按已知费用比较</p>
<BudgetBreakdown v-if="isMultiDayItinerary(chosen)&&chosen.budget" :budget="chosen.budget"/><details v-for="(d,index) in days" :key="d.day_id" :open="index===0" class="preview-day"><summary>第 {{index+1}} 天 · {{dayLabel(d)}} · {{dayActivities(d).length}} 项</summary><p v-if="d.original_requirements">{{nameOf(d.original_requirements.origin_place_id)}} → {{nameOf(d.original_requirements.destination_place_id)}}</p><ol><li v-for="stop in dayActivities(d)" :key="stop.goal_id"><time>{{clock(stop.starts_at)}}—{{clock(stop.departs_at)}}</time> <strong>{{names?.[stop.goal_id]||nameOf(stop.place_id)}}</strong><small>{{Math.round(stop.duration_seconds/60)}} 分钟{{stop.required?' · 保留必去':''}}{{d.ongoing?.stop_id===stop.goal_id?' · 正在进行':''}}</small></li></ol><p>当日步行 {{Math.round(d.walking_distance_m)}} 米 · 交通 {{Math.ceil(d.travel_seconds/60)}} 分钟 · {{budget?'当日支出归属':'已知费用上限'}} ¥{{((budget?.by_day[d.business_date]??d.known_cost_upper_fen)/100).toFixed(2)}}{{d.cost_unknown?' · 部分费用待添加':''}} · {{clock(d.returns_at)}} 结束</p><p v-if="d.overnight" class="overnight-note">过夜点：{{nameOf(d.overnight.place_id)}}。夜间住宿与条件待核对，次日从这里继续；不计作跨夜交通。</p></details>
 <section v-if="changes.length" class="planning-change-list" aria-label="按日行程变更"><h4>各日变更</h4><div v-for="(change,index) in changes" :key="change.day_id||index"><h5>{{change.business_date?dayLabel({business_date:change.business_date}):'第 '+(index+1)+' 天'}}</h5><p v-if="change.added?.length">新增：{{change.added.map(label).join('、')}}</p><p v-if="change.removed?.length">移除：{{change.removed.map(label).join('、')}}</p><p v-if="change.reordered?.length">调整顺序：{{change.reordered.map(label).join('、')}}</p><p v-for="item in change.start_changes||[]" :key="item.goal_id">{{label(item.goal_id)}}：{{moment(item.before)}} → {{moment(item.after)}}</p><p v-for="item in change.target_changes||[]" :key="'target:'+item.goal_id">{{label(item.goal_id)}}：更换地点或场次，请核对当天新安排。</p><p v-if="change.walking_distance_m_delta!==undefined">步行距离变化 {{Math.round(change.walking_distance_m_delta)}} 米；交通耗时变化 {{Math.round((change.travel_seconds_delta||0)/60)}} 分钟。</p></div></section>
 </template>
</div></template>
<style scoped>
.plan-alternatives{display:flex;flex-wrap:wrap;gap:8px}.plan-option{border:1px solid #cbbda4;border-radius:8px;padding:12px;text-align:left;min-height:44px;color:#345944;background:#fffaf1}.plan-option.selected{background:#345944;color:#fffaf1}.whole-trip-metrics{font-weight:600;line-height:1.8}.preview-day{padding:12px 0;border-bottom:1px solid #ded4c2}.preview-day summary{cursor:pointer;min-height:44px;line-height:1.8}.preview-day ol{padding-left:20px}.preview-day li{padding:8px 0;line-height:1.8}.preview-day small{display:block}.preview-day p,.planning-change-list p{font-size:13px;line-height:1.8}.overnight-note{padding:10px;background:#f1ecdf}.planning-change-list h5{margin:12px 0 4px}
</style>
