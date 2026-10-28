<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue'
import {Heart,ChevronRight,Settings,ArrowRight} from 'lucide-vue-next'
import {api} from '../api'
import {cityApi,draftDays} from '../cityApi'
import type {CityTripState} from '../cityApi'
import type {MemoryTrip} from '../memoryTypes'
import {journey,loadJourney,detailFor,toggleFavorite,cityStatus,tripDateLabel} from '../journey'
import {memoryEpoch} from '../voiceDraft'
const trips=ref<CityTripState[]>([]),legacy=ref<MemoryTrip[]>([]),error=ref(''),busy=ref(false)
const meaningfulTrips=computed(()=>trips.value.filter(t=>t.id!==journey.trip?.id&&draftDays(t.draft).some(d=>d.stops.length||d.business_date)))
async function load(){busy.value=true;error.value='';const epoch=memoryEpoch.value;try{await loadJourney(true);const data=await cityApi.list();if(epoch===memoryEpoch.value)trips.value=data.items;try{const old=await api.memoryTrips();if(epoch===memoryEpoch.value)legacy.value=old.items.filter(t=>t.route_id)}catch{}}catch(e){if(epoch===memoryEpoch.value)error.value=(e as Error).message}finally{busy.value=false}}
watch(memoryEpoch,()=>{trips.value=[];legacy.value=[]});onMounted(load)
</script>
<template><div class="page my-page"><header class="journey-page-heading"><h1>我的游记</h1><RouterLink to="/memory" aria-label="设置" class="icon-button"><Settings :size="22"/></RouterLink></header>
 <h2 class="my-section-title">收藏</h2>
 <p v-if="error" class="error-state" role="alert">{{error}}<button @click="load">重试</button></p>
 <div v-if="!journey.favorites.length" class="journey-empty"><Heart :size="30"/><h2>还没有收藏</h2><RouterLink to="/" class="button secondary">去发现 <ArrowRight :size="15"/></RouterLink></div><article v-for="favorite in journey.favorites" :key="favorite.key" class="favorite-row"><RouterLink :to="detailFor(favorite)"><span class="tiny-caption">{{favorite.kind==='food'?'美食':favorite.kind==='show'?'演出':'地点'}}</span><h2>{{favorite.name}}</h2></RouterLink><button type="button" :aria-label="'取消收藏'+favorite.name" @click="toggleFavorite(favorite)"><Heart :size="20" fill="currentColor"/></button></article>
 <details v-if="meaningfulTrips.length||legacy.length" class="legacy-trips"><summary>往期行程</summary><RouterLink v-for="trip in meaningfulTrips" :key="trip.id" :to="'/city-trip/'+trip.id" class="saved-trip-card"><div><span class="tiny-caption">{{trip.status==='DRAFT'?'待安排':cityStatus[trip.status]}}</span><h2>{{tripDateLabel(trip.draft)}}</h2><p>{{draftDays(trip.draft).reduce((n,d)=>n+d.stops.length,0)?draftDays(trip.draft).reduce((n,d)=>n+d.stops.length,0)+' 站':'尚未安排行程'}}</p></div><ChevronRight :size="20"/></RouterLink><details v-if="legacy.length" class="legacy-trips"><summary>园内行程</summary><RouterLink v-for="trip in legacy" :key="trip.id" :to="'/result/'+trip.route_id" class="saved-trip-card"><div><h2>{{trip.interview.draft.scenic_id==='qingming'?'清明上河园':'万岁山武侠城'}}</h2><p>{{trip.interview.draft.visit_date}}</p></div><ChevronRight :size="18"/></RouterLink></details></details>
</div></template>
<style scoped>
.my-page .journey-page-heading{margin-bottom:24px}.my-page .journey-page-heading h1{margin:0}.my-page .journey-empty{min-height:220px;padding:35px 18px;border:0;background:transparent}.my-page .journey-empty h2{font:23px "Songti SC",serif;margin:16px 0 22px}.my-section-title{font-size:15px;font-weight:500;color:#8d7d65;margin:0;padding-bottom:16px;border-bottom:1px solid #dfd6c5}.legacy-trips{margin-top:20px;color:#9b896e;font-size:12px}.legacy-trips summary{min-height:44px;cursor:pointer}
</style>
