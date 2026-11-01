<script setup lang="ts">
import { Compass, Map, CalendarDays, MapPinned, UserRound } from 'lucide-vue-next'
import AssistantLauncher from './components/AssistantLauncher.vue'
import BasketEntry from './components/BasketEntry.vue'
import { useRoute } from 'vue-router'
import {journey,loadJourney} from './journey'
import {computed,watch} from 'vue'
const route = useRoute()
const isPitch=computed(()=>route.path==='/pitch')
watch(isPitch,value=>{if(!value&&window.location.pathname!=='/pitch')void loadJourney()},{immediate:true})
</script>
<template>
 <RouterView v-if="isPitch"/>
 <div v-else class="app-shell" :class="{ 'assistant-route': route.path === '/assistant', 'guide-route': route.path === '/guide', 'city-trip-route': route.path.startsWith('/city-trip'), 'memory-route': route.path === '/memory' }">
  <header class="site-header"><div class="header-inner">
   <RouterLink to="/" class="brand" aria-label="汴梁游记首页"><span class="brand-icon"><Compass :size="24"/></span><strong>汴梁游记<span>BIAN LIANG JOURNAL</span></strong></RouterLink>
   <div class="header-actions"><nav aria-label="主导航"><RouterLink to="/" :class="{active:route.path==='/'||route.path.startsWith('/food')||route.path.startsWith('/scenic')||route.path.startsWith('/place')}"><Map :size="17"/>发现</RouterLink><RouterLink to="/city-trip" :class="{active:route.path.startsWith('/city-trip')||route.path.startsWith('/result')}"><CalendarDays :size="17"/>行程</RouterLink><RouterLink to="/guide" active-class="active"><MapPinned :size="17"/>地图</RouterLink><RouterLink to="/me" :class="{active:['/me','/memory'].includes(route.path)}"><UserRound :size="17"/>我的</RouterLink></nav><BasketEntry/></div>
  </div></header>
  <main><RouterView /></main>
  <AssistantLauncher />
  <div v-if="journey.toast" class="journey-toast" role="status">{{journey.toast}} <RouterLink v-if="journey.choices.length" to="/basket">查看素材篮</RouterLink></div>
 </div>
</template>
