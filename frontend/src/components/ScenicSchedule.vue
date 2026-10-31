<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {CalendarCheck,Clock3,MapPin,ShieldCheck} from 'lucide-vue-next'
import {scenicScheduleApi,type Schedule} from '../scenicScheduleApi'
import {isFeaturedProgram} from '../data/scenicPresentation'
import DestinationActions from './DestinationActions.vue'
import {matchPrograms,programIdFor} from '../programMatch'
const props=defineProps<{parkId:string;serviceDate:string;featuredOnly?:boolean}>()
const schedule=ref<Schedule>(),error=ref(''),busy=ref(false)
let sequence=0,controller:AbortController|undefined
const label=computed(()=>schedule.value?.display_status==='official_schedule_reviewed'?'官方日程已核验':schedule.value?.display_status==='stale_reference_only'?'旧日程仅供参考':'今日尚未确认')
const groups=computed(()=>{
 const result=new Map<string,Schedule['performances']>()
 for(const show of schedule.value?.performances||[]){
  if(props.featuredOnly&&(!show.start_time||!isFeaturedProgram(props.parkId,show.program_name)))continue
  const key=[show.program_name,show.venue_name,show.area||''].join('|')
  result.set(key,[...(result.get(key)||[]),show])
 }
 return [...result.values()]
})
async function load(){
 const current=++sequence;controller?.abort();controller=new AbortController();busy.value=true;error.value='';schedule.value=undefined
 try{const value=await scenicScheduleApi.performances(props.parkId,props.serviceDate,controller.signal);if(current===sequence)schedule.value=value}
 catch(e){if(current===sequence)error.value=(e as Error).message}
 finally{if(current===sequence)busy.value=false}
}
watch(()=>[props.parkId,props.serviceDate],load,{immediate:true})
const scenicId=computed(()=>props.parkId==='qmsy'?'qingming':props.parkId)
watch(groups,list=>{if(list.length)void matchPrograms(scenicId.value,list.map(g=>g[0].program_name))})
onBeforeUnmount(()=>controller?.abort())
const time=(value:string|null)=>value?.slice(0,5)||'时刻待确认'
</script>
<template>
<section class="scenic-schedule" aria-labelledby="schedule-title">
 <header class="schedule-heading"><div><span class="tiny-caption">按日期查询 · {{serviceDate}}</span><h3 id="schedule-title">{{featuredOnly?'主要演出场次':'当日节目表'}}</h3></div><CalendarCheck :size="22"/></header>
 <div v-if="busy" class="schedule-state" role="status">正在核对节目日期与来源…</div>
 <div v-else-if="error" class="schedule-state" role="status">今日尚未确认<small>暂时无法读取节目日程，请稍后重试。</small><button class="quiet-link" @click="load">重新查询</button></div>
 <template v-else-if="schedule">
  <div class="schedule-notice" :data-status="schedule.display_status"><ShieldCheck :size="18"/><div><strong>{{label}}</strong><p>{{schedule.notice}}</p></div></div>
  <p v-if="schedule.approved_schedule&&schedule.latest_review_status==='review_required'" class="schedule-pending" role="status">临时变动：发现待复核的新版本，当前仍保留上一次审核结果。</p>
  <div v-if="!groups.length" class="schedule-state">{{featuredOnly?'暂无该日期已审核的主要演出场次。':'没有该日期已审核的场次。'}}<small>不会用其他日期的节目表补齐。</small></div>
  <div v-else class="schedule-groups">
   <article v-for="group in groups" :key="group[0].performance_id" class="schedule-program">
    <div><h4>{{group[0].program_name}}</h4><p><MapPin :size="13"/>{{group[0].area?group[0].area+' · ':''}}{{group[0].venue_name}}</p><DestinationActions v-if="programIdFor(scenicId,group[0].program_name)" class="schedule-actions" kind="program" :id="programIdFor(scenicId,group[0].program_name)!" :name="group[0].program_name"/></div>
    <ul><li v-for="show in group" :key="show.performance_id"><Clock3 :size="14"/><strong>{{time(show.start_time)}}</strong><span v-if="show.operating_status==='cancelled'">已取消</span><span v-else-if="show.schedule_note">{{show.schedule_note}}</span><span v-else>时长与入场规则待核验</span></li></ul>
   </article>
  </div>
  <footer class="schedule-source">来源日期：{{schedule.source_updated_on||'未知'}} · 最近采集：{{schedule.last_fetched_at?new Date(schedule.last_fetched_at).toLocaleString():'未知'}}<a v-if="schedule.source?.source_ref.startsWith('http')" :href="schedule.source.source_ref" target="_blank" rel="noopener noreferrer">查看公开来源</a></footer>
 </template>
</section>
</template>
<style scoped>
.schedule-actions{margin:10px 0 0;padding:0;max-width:280px}
.scenic-schedule{margin:22px 0 26px;padding:20px;border:1px solid #ddd4c0;border-radius:18px;background:linear-gradient(135deg,#fffdf6,#f4f0e5)}
.schedule-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}.schedule-heading h3{margin:4px 0 0;font-size:21px}.schedule-notice{display:flex;gap:10px;margin:16px 0;padding:12px;background:#e9efe4;border-radius:12px;color:#345340}.schedule-notice[data-status=stale_reference_only],.schedule-notice[data-status=today_unconfirmed]{background:#f4ead7;color:#70572c}.schedule-notice p{margin:4px 0 0;font-size:12px;line-height:1.65}.schedule-pending{padding:10px 12px;background:#f7e8d6;color:#80502c;font-size:13px}.schedule-groups{display:grid;gap:10px}.schedule-program{padding:14px 0;border-top:1px solid #ddd4c0}.schedule-program h4{margin:0 0 5px;font-size:16px}.schedule-program p{display:flex;align-items:center;gap:4px;margin:0;color:#6e6a5d;font-size:12px}.schedule-program ul{display:flex;flex-wrap:wrap;gap:8px;padding:0;margin:12px 0 0;list-style:none}.schedule-program li{display:flex;align-items:center;gap:6px;padding:7px 9px;background:#fff;border:1px solid #e0d8c8;border-radius:9px;font-size:12px}.schedule-program li span{color:#827866;font-size:11px}.schedule-source{margin-top:16px;color:#7c7566;font-size:11px;line-height:1.7;overflow-wrap:anywhere}.schedule-source a{display:inline-block;margin-left:10px}.schedule-state{padding:20px 0;color:#756e60}.schedule-state small{display:block;margin-top:6px}.schedule-state button{display:block;padding:8px 0}
@media(max-width:640px){.scenic-schedule{padding:16px;margin-inline:0}.schedule-program ul{display:grid}.schedule-program li{flex-wrap:wrap}.schedule-program li span{flex-basis:100%;padding-left:20px}}
</style>
