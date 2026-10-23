<script setup lang="ts">
import GuidedPlanner from '../components/GuidedPlanner.vue'
import CityAgentChat from '../components/CityAgentChat.vue'
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {journey,loadJourney,ensureJourney,loadBasket} from '../journey'
import {useRoute} from 'vue-router'
import type {ObjectRef} from '../basketApi'
import {takeQuestion} from '../assistantHandoff'
import {cityApi} from '../cityApi'
import {memoryEpoch} from '../voiceDraft'
const route=useRoute()
const ready=ref(false),error=ref(''),initialQuestion=ref('')
const focus=computed<ObjectRef|null>(()=>typeof route.query.focus_id==='string'&&['scenic','food','place','poi','show','vendor','program'].includes(String(route.query.focus_kind))?{kind:route.query.focus_kind as ObjectRef['kind'],object_id:route.query.focus_id}:null)
const cityMode=computed(()=>!!route.query.city_trip||!!focus.value||['trip','city'].includes(String(route.query.context))||route.query.plan==='basket')
const cityTripId=computed(()=>ready.value&&cityMode.value?journey.trip?.id:null)
let sequence=0,disposed=false
async function load(){const request=++sequence,guard=memoryEpoch.value;ready.value=false;error.value='';const valid=()=>!disposed&&request===sequence&&guard===memoryEpoch.value
 try{await loadJourney();if(!valid())return;if(cityMode.value){if(typeof route.query.city_trip==='string'){const trip=await cityApi.activate(route.query.city_trip);if(!valid())return;journey.trip=trip;await loadBasket()}else await ensureJourney();if(!valid())return;initialQuestion.value=takeQuestion(focus.value)}if(valid())ready.value=true}catch(e){if(valid())error.value=(e as Error).message}}
watch(memoryEpoch,()=>{sequence++;ready.value=false;initialQuestion.value='';error.value='记录已清除，请重新打开助手'})
onBeforeUnmount(()=>{disposed=true;sequence++})
onMounted(load);watch(()=>route.fullPath,load)
</script>
<template>
 <div class="assistant-page guided-assistant-page">
  <div class="assistant-shell">
   <header class="assistant-topbar"><div class="assistant-brand"><span class="assistant-seal">汴</span><div><strong>小汴梁</strong><small>开封智能旅游助手</small></div></div><RouterLink v-if="cityTripId" :to="'/city-trip/'+cityTripId" class="assistant-back">返回行程</RouterLink><RouterLink v-else to="/" class="assistant-back">返回发现</RouterLink></header>
   <p v-if="error" class="error-state" role="alert">{{error}}<button @click="load">重试</button></p>
   <CityAgentChat v-if="cityTripId" :key="cityTripId+':'+focus?.object_id" :trip-id="cityTripId" :focus="focus" :initial-question="initialQuestion" :planning="route.query.plan==='basket'"/>
   <p v-else-if="route.query.context==='trip'"><RouterLink to="/city-trip">{{journey.loaded?'请先选择或保存城市行程':'正在读取当前城市行程…'}}</RouterLink></p>
   <p v-else-if="cityMode&&!error" role="status">正在读取旅行上下文…</p>
   <GuidedPlanner v-else-if="!cityMode"/>
  </div>
 </div>
</template>
