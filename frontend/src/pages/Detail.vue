<script setup lang="ts">
import {ref,onMounted,watch,computed} from 'vue'
import {useRoute} from 'vue-router'
import {ArrowLeft,ArrowUpRight,MapPin,ExternalLink,Clapperboard,Landmark,Clock3,Ticket,Navigation} from 'lucide-vue-next'
import {api} from '../api'
import {currentHour} from '../presentation'
import type {Scenic,Network,Crowds} from '../types'
import ScenicCover from '../components/ScenicCover.vue'
import InternalMap from '../components/InternalMap.vue'
import ExternalMap from '../components/ExternalMap.vue'
import DestinationActions from '../components/DestinationActions.vue'
import DetailMedia from '../components/DetailMedia.vue'
import ShowPhotos from '../components/ShowPhotos.vue'
import ScenicSchedule from '../components/ScenicSchedule.vue'
import DetailAsk from '../components/DetailAsk.vue'
import {journey,loadJourney} from '../journey'
import {useDetailObject} from '../detailObject'
import programWorkbook from '../data/programWorkbook.generated.json'
import qmsySchedule from '../data/qmsySchedule.generated.json'
import {hasProgramTime,isFeaturedProgram} from '../data/scenicPresentation'
import {matchPrograms,programIdFor} from '../programMatch'
const chinaDate=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
const route=useRoute(),scenic=ref<Scenic>(),network=ref<Network>(),crowds=ref<Crowds>(),busy=ref(true),error=ref(''),mapError=ref(''),tab=ref('外部位置地图')
const {object}=useDetailObject('scenic',()=>String(route.params.id))
const fact=computed(()=>object.value?.fact_id?journey.catalog.find(x=>x.record.kind==='place'&&x.record.fact_id===object.value!.fact_id):null)
const highlights=computed(()=>fact.value?journey.catalog.filter(x=>x.record.kind==='highlight'&&x.record.place_id===fact.value!.record.fact_id).sort((a,b)=>(a.record.sort_order||0)-(b.record.sort_order||0)):[])
const facilities=computed(()=>fact.value?journey.catalog.filter(x=>x.record.kind==='facility'&&x.record.place_id===fact.value!.record.fact_id):[])
const shows=computed(()=>{
 if(!scenic.value)return []
 const id=scenic.value.id
 const records=programWorkbook.records.filter(program=>program.scenicId===id&&isFeaturedProgram(id,program.name))
 const names=new Set(records.map(x=>x.name))
 const extra=journey.catalog.filter(x=>x.record.kind==='program'&&x.record.place_id===fact.value?.record.fact_id&&x.record.business_status!=='cancelled'&&isFeaturedProgram(id,x.record.name||'')&&!names.has(x.record.name||''))
 return [...records.map(program=>{
  const schedule=id==='qingming'?qmsySchedule.records.find(row=>row.name===program.name)?.times||'':program.recommendedTime
  return {name:program.name,venue:program.venue,schedule,source_url:null,tag:program.tag,description:program.description,ticketTips:program.ticketTips,photos:program.photos}
 }),...extra.map(program=>({name:program.record.name||'',venue:program.record.stage_name||'',schedule:program.record.schedule_text||'',source_url:null,tag:'',description:program.record.description||'',ticketTips:'',photos:[]}))].filter(program=>hasProgramTime(program.schedule))
})

watch(shows,list=>{if(scenic.value&&list.length)void matchPrograms(scenic.value.id,list.map(x=>x.name))},{immediate:true})

let version=0
async function load(){const v=++version;busy.value=true;error.value='';mapError.value='';scenic.value=undefined;network.value=undefined;crowds.value=undefined;tab.value='外部位置地图';try{const s=await api.scenic(String(route.params.id));if(v!==version)return;scenic.value=s;if(s.internal_map){try{const[n,c]=await Promise.all([api.network(s.id),api.crowds(s.id,'reported',currentHour())]);if(v===version){network.value=n;crowds.value=c}}catch(e){if(v===version)mapError.value=(e as Error).message}}}catch(e){if(v===version)error.value=(e as Error).message}finally{if(v===version)busy.value=false}}
onMounted(()=>{void load();void loadJourney()});watch(()=>route.params.id,load)
</script>
<template><div class="page editorial-detail scenic-detail-page"><RouterLink to="/" class="back-link"><ArrowLeft :size="16"/>返回发现</RouterLink><div v-if="busy" class="state" role="status"><span class="spinner"/>正在载入景区…</div><div v-else-if="error" class="error-state" role="alert">{{error}} <button @click="load">重试</button></div><template v-else-if="scenic"><figure class="scenic-detail-cover"><ScenicCover :id="scenic.id" :name="scenic.name" :theme="scenic.theme" eager/><figcaption>主题插画</figcaption></figure><header class="editorial-title"><span class="tiny-caption">{{scenic.category}}</span><h1>{{scenic.name}}</h1><p>{{fact?.record.description||scenic.intro.split('。')[0]+'。'}}</p><DestinationActions kind="scenic" :id="scenic.id" :name="scenic.name"/></header><nav class="detail-section-nav" aria-label="景区详情章节"><a v-if="highlights.length||facilities.length" href="#scenic-highlights">看点</a><a href="#scenic-media">图集影像</a><a href="#scenic-shows">演出</a><a href="#scenic-map">地图</a></nav><section v-if="highlights.length||facilities.length" id="scenic-highlights" class="editorial-section"><div class="detail-section-title"><h2>在这里，慢慢看</h2><Landmark :size="21" :stroke-width="1.3"/></div><div class="detail-highlight-grid"><article v-for="h in highlights" :key="h.record.fact_id"><span class="highlight-line"/><h3>{{h.record.name}}</h3><p>{{h.record.description||'详细介绍待添加'}}</p><DetailMedia kind="scenic" :id="h.record.fact_id.replace('hl_wb_','hl_')" :name="h.record.name||'景区看点'"/></article></div><div v-if="facilities.length" class="poi-list"><article v-for="f in facilities" :key="f.record.fact_id"><span>{{f.record.name}}</span><small>{{f.record.category||'景区服务'}} · 位置与开放情况以现场为准</small></article></div></section><div id="scenic-media"><DetailMedia kind="scenic" :id="scenic.id" :name="scenic.name"/></div><section id="scenic-shows" class="editorial-section"><div class="detail-section-title"><div><h2>主要演出</h2><span class="pending-caption" v-if="shows.length">{{shows.length}} 个精选节目</span></div><Clapperboard :size="21" :stroke-width="1.3"/></div><ScenicSchedule v-if="['qingming','wansui'].includes(scenic.id)" :park-id="scenic.id==='qingming'?'qmsy':scenic.id" :service-date="chinaDate" featured-only/><p v-if="shows.length" class="pending-copy">参考时段{{scenic.id==='qingming'?'（来源日期：'+qmsySchedule.sourceDate+'）':'（节目资料整理）'}}，具体场次以景区当日公告为准。</p><div v-else class="content-pending">主要演出时刻待更新</div><article v-for="s in shows" :key="s.name" class="detail-show"><div class="show-main"><Clapperboard :size="20"/><div><span v-if="s.tag" class="show-tag">{{s.tag}}</span><h3>{{s.name}}</h3><p>{{s.venue}} · {{s.schedule}}</p></div></div><DestinationActions v-if="programIdFor(scenic.id,s.name)" class="show-actions" kind="program" :id="programIdFor(scenic.id,s.name)!" :name="s.name"/><ShowPhotos v-if="s.photos.length" :name="s.name" :photos="s.photos"/><div class="show-copy"><p v-if="s.description" class="show-description">{{s.description}}</p><small v-if="s.ticketTips">{{s.ticketTips}}</small><a v-if="s.source_url" :href="s.source_url" target="_blank" rel="noopener noreferrer">查看官方介绍 <ExternalLink :size="12"/></a></div></article><RouterLink v-if="scenic.internal_map" :to="'/guide?scenic='+scenic.id" class="text-link">在景区地图中查看场次 <ArrowUpRight :size="15"/></RouterLink></section><section class="editorial-section"><div class="detail-section-title"><h2>游玩信息</h2></div><dl class="detail-fact-grid"><div><dt><Clock3 :size="16"/>建议时长</dt><dd>{{fact?.record.suggested_stay_min?fact.record.suggested_stay_min+' 分钟（资料参考）':'待添加'}}</dd></div><div><dt><Ticket :size="16"/>开放与票务</dt><dd>待添加</dd></div><div><dt><MapPin :size="16"/>景区位置</dt><dd>{{scenic.address||'待添加'}}</dd></div><div><dt><Navigation :size="16"/>入口指引</dt><dd>待添加</dd></div></dl><a :href="scenic.source_url" target="_blank" rel="noopener noreferrer" class="quiet-link">公开资料来源 · {{scenic.checked_at}} <ExternalLink :size="12"/></a></section><section id="scenic-map" class="editorial-section"><div class="detail-section-title"><h2>找到这一处</h2></div><div class="discovery-tabs"><button v-for="t in ['外部位置地图','内部示意地图']" :key="t" :class="{active:tab===t}" :aria-pressed="tab===t" @click="tab=t">{{t}}</button></div><div v-if="tab==='内部示意地图'&&scenic.id==='wansui'" class="scenic-handmap"><img src="/images/scenic-maps/wansui-map.png" alt="万岁山武侠城手绘导览图" loading="lazy"/><small>万岁山武侠城手绘导览图 · 实际路线以现场标识为准</small></div><div v-else-if="tab==='内部示意地图'&&scenic.id==='qingming'" class="scenic-handmap"><img src="/images/scenic-maps/qmsy-map.jpg" alt="清明上河园手绘导览图" loading="lazy"/><small>清明上河园手绘导览图 · 标注步行时间为休闲估算，实际路线以现场标识为准</small></div><InternalMap v-else-if="tab==='内部示意地图'&&network" :network="network" :crowds="crowds?.nodes"/><div v-else-if="tab==='内部示意地图'" class="content-pending">{{mapError||'园内核验路网待添加'}}</div><ExternalMap v-else :key="scenic.id" :name="scenic.name" :address="scenic.address"/></section><DetailAsk kind="scenic" :id="scenic.id" :name="scenic.name"/><RouterLink class="detail-basket-link" to="/basket">整理旅行素材篮 <ArrowUpRight :size="17"/></RouterLink></template></div></template>
