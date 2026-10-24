<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref} from 'vue'
import {ArrowUpRight,Search,CalendarDays,MapPin,Utensils,Ticket,ArrowRight} from 'lucide-vue-next'
import ScenicCover from '../components/ScenicCover.vue'
import FoodCover from '../components/FoodCover.vue'
import CardRail from '../components/CardRail.vue'
import DestinationActions from '../components/DestinationActions.vue'
import {api} from '../api'
import {scenicCatalog,type ScenicSummary} from '../data/scenicCatalog'
import {foodCatalog} from '../data/foods'
import {journey,loadJourney,nameOf,cityStatus,nextCityStop,arrivalSummary,clock,localDate} from '../journey'
const items=ref<ScenicSummary[]>([...scenicCatalog]),busy=ref(false),error=ref(''),query=ref(''),category=ref('全部')
let disposed=false
const scenics=computed(()=>items.value.filter(s=>(s.name+s.category).includes(query.value.trim())))
const foods=computed(()=>foodCatalog.filter(f=>f.name.includes(query.value.trim())))
const shows=computed(()=>journey.catalog.filter(x=>x.record.kind==='show_session'&&!x.record.is_demo&&x.ready&&x.record.business_date===localDate()).filter(x=>(nameOf(x.record.program_id)+nameOf(x.record.stage_place_id)).includes(query.value.trim())))
const showScenic=computed(()=>['全部','景点'].includes(category.value)),showFood=computed(()=>['全部','美食'].includes(category.value))
const hasTripContent=computed(()=>!!(journey.trip&&(journey.trip.draft.stops.length||journey.trip.draft.business_date)))
const currentTrip=computed(()=>journey.trip),next=computed(()=>nextCityStop.value)
const terminal=computed(()=>currentTrip.value&&['COMPLETED','CANCELLED','ARCHIVED'].includes(currentTrip.value.status))
async function load(){if(busy.value)return;busy.value=true;error.value='';try{const data=await api.scenics('',5000);if(!disposed)items.value=data.items}catch(e){if(!disposed)error.value=(e as Error).message}finally{if(!disposed)busy.value=false}}
onMounted(()=>{void load();void loadJourney(true)});onBeforeUnmount(()=>{disposed=true})
</script>
<template>
 <div class="page home-page discovery-page">
  <h1 class="visually-hidden">发现开封</h1>
  <section v-if="currentTrip&&hasTripContent" class="home-trip-card has-trip" aria-label="当前行程">
   <div class="trip-card-top"><span class="home-trip-kicker">我的开封之旅</span><span class="journey-status">{{cityStatus[currentTrip.status]}}</span></div>
   <h2>{{currentTrip.draft.business_date||'开封之旅'}}</h2>
   <div class="next-destination"><MapPin :size="19"/><div><small>{{terminal?'这一程已留存':'下一站'}}</small><strong>{{next?nameOf(next.place_id):terminal?'查看游玩记录':'继续完善安排'}}</strong><span v-if="next">{{arrivalSummary(next.stop_id)}}</span></div></div>
   <div class="home-trip-actions"><RouterLink class="button primary" :to="'/city-trip/'+currentTrip.id">{{terminal?'回顾行程':'继续行程'}}<ArrowRight :size="15"/></RouterLink><RouterLink v-if="currentTrip.draft.stops.length" class="text-link" to="/guide?mode=city">查看地图</RouterLink></div>
  </section>
  <div class="home-banner" :class="{'compact-banner':hasTripContent}"><img src="/images/scenics/bianliang.jpg" alt="汴梁城市主题画" width="1448" height="1086" fetchpriority="high" decoding="async"><div v-if="!hasTripContent" class="home-banner-action"><RouterLink class="button primary" to="/assistant?context=city"><CalendarDays :size="17"/>规划我的开封之旅<ArrowUpRight :size="17"/></RouterLink></div></div>
  <section v-if="journey.catalog.some(x=>x.record.fact_id==='open_mvp_kaifengfu_20261025')" class="home-trip-card"><h2>开封夜游怎么安排</h2><p>开封府 → 第一楼 → 鼓楼夜市，按你的时间试排。</p><RouterLink class="button primary" to="/plan-city">用真实资料规划 <ArrowRight :size="15"/></RouterLink></section>
  <div class="explore-toolbar"><div class="discovery-tabs" aria-label="探索内容"><button v-for="tab in ['全部','景点','美食','演出']" :key="tab" type="button" :class="{active:category===tab}" :aria-pressed="category===tab" @click="category=tab">{{tab}}</button></div><label class="explore-search"><Search :size="17"/><input v-model="query" aria-label="搜索景点、美食或演出" placeholder="找一处风景，寻一味开封"><button v-if="query" type="button" aria-label="清除搜索" @click="query=''">清除</button></label></div>
  <p v-if="journey.choices.length" class="selection-basket">{{journey.choices.length}} 项素材<RouterLink to="/basket">旅行素材篮 <ArrowRight :size="13"/></RouterLink></p>
  <RouterLink class="quiet-link" to="/catalog">浏览全部已接入地点、夜市、餐饮与园内资料 <ArrowRight :size="13"/></RouterLink>
  <section v-if="showScenic" class="destinations"><div class="section-heading"><h2>游览景点</h2><span v-if="error" class="home-service-note" role="status">基础展示 · 服务暂不可用 <button :disabled="busy" @click="load">重试</button></span></div>
   <CardRail v-if="scenics.length" label="景点选择"><article v-for="s in scenics" :key="s.id" class="scenic-card catalog-card"><RouterLink :to="'/scenic/'+s.id" class="catalog-main"><div class="card-art"><ScenicCover :id="s.id" :name="s.name" :theme="s.theme"/><span class="pill card-category">{{s.category}}</span></div><div class="card-copy"><div class="card-title"><h3>{{s.name}}</h3><ArrowUpRight :size="18"/></div><p>{{s.subtitle}}</p></div></RouterLink><DestinationActions source="home" kind="scenic" :id="s.id" :name="s.name"/></article></CardRail>
   <div v-else class="journey-empty"><MapPin :size="25"/><p>没有找到相符的景点</p><button type="button" @click="query=''">清除搜索</button></div>
  </section>
  <section v-if="showFood" class="food-destinations"><div class="section-heading"><h2>开封美食</h2><span class="food-selection-note">先选风味，再找店家</span></div>
   <CardRail v-if="foods.length" label="美食选择"><article v-for="food in foods" :key="food.id" class="food-card catalog-card"><RouterLink :to="'/food/'+food.id" class="catalog-main"><div class="card-art"><FoodCover :name="food.name"/><span class="pill card-category">开封风味</span></div><div class="card-copy"><div class="card-title"><h3>{{food.name}}</h3><ArrowUpRight :size="18"/></div><span class="food-caption">查看风味与对应商家</span></div></RouterLink><DestinationActions source="home" kind="food" :id="food.id" :name="food.name"/></article></CardRail>
   <div v-else class="journey-empty"><Utensils :size="25"/><p>没有找到相符的美食</p><button type="button" @click="query=''">清除搜索</button></div>
  </section>
  <section v-if="category==='演出'" class="discover-shows"><div class="section-heading"><h2>演出与文化体验</h2><RouterLink to="/guide" class="text-link">打开景区地图 <ArrowRight :size="14"/></RouterLink></div><div v-if="!shows.length" class="journey-empty"><Ticket :size="28"/><p>{{journey.error?'演出资料暂时无法加载':'暂无今日可核验场次'}}</p><small>以景区当天公告为准，未核实的时间不会加入行程。</small><button type="button" @click="loadJourney(true)">重新查看</button></div><article v-for="s in shows" :key="s.record.fact_id" class="show-discovery-card"><span>{{clock(s.record.starts_at)}}</span><div><h3>{{nameOf(s.record.program_id)}}</h3><small>{{nameOf(s.record.stage_place_id)}} · 已核验场次</small></div><DestinationActions source="home" kind="show" :id="s.record.fact_id" :name="nameOf(s.record.program_id)"/></article></section>
 </div>
</template>
