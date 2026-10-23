<script setup lang="ts">
import { computed,ref } from 'vue'
import { ZoomIn,ZoomOut,Maximize2,MapPin } from 'lucide-vue-next'
import type {Network,CrowdNode,Feature} from '../types'
import {category} from '../types'
import {placeName} from '../presentation'
const props=defineProps<{network:Network;route?:Network;crowds?:CrowdNode[];selected?:string}>()
const emit=defineEmits<{select:[id:string]}>()
const zoom=ref(1)
const local=ref('')
const nodes=computed(()=>props.network.features.filter(f=>f.geometry.type==='Point'))
const roads=computed(()=>props.network.features.filter(f=>f.geometry.type==='LineString'))
const active=computed(()=>nodes.value.find(n=>n.properties.id===(props.selected||local.value)))
const points=(f:Feature)=>(f.geometry.coordinates as number[][]).map(p=>p.join(',')).join(' ')
const coordinates=(f:Feature)=>f.geometry.coordinates as number[]
const hasCrowds=computed(()=>props.crowds?.some(n=>!n.is_simulated&&n.level!=null))
const color=(id:string)=>({low:'#6d9c83',medium:'#c6a45b',high:'#cc795f',unknown:'#a3aaa6'}[category(props.crowds?.find(c=>!c.is_simulated&&c.node_id===id)?.level)])
function select(id:string){local.value=id;emit('select',id)}
</script>
<template>
 <div class="internal-map">
  <div class="map-topline"><span>{{network.is_simulated?'景区示意图 · 位置与距离待核实，请以现场指引为准':'景区地图'}}</span></div>
  <div class="map-viewport">
   <svg viewBox="0 0 840 540" role="group" aria-label="景区内部示意图，可点击地点查看详情">
    <defs><pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" fill="#d3dfd2"/></pattern></defs>
    <rect width="840" height="540" fill="#eef2e8"/><rect width="840" height="540" fill="url(#dots)"/>
    <g :transform="`translate(${420*(1-zoom)},${270*(1-zoom)}) scale(${zoom})`">
     <path d="M300 230Q440 200 490 280T510 400Q390 420 325 350T300 230" fill="#cfe0d7" stroke="#b6d3c6" stroke-width="2"/><text x="390" y="300" fill="#85aa97" font-size="14" letter-spacing="6">示意水域</text>
     <g fill="#d9e3cd" opacity=".8"><circle cx="110" cy="160" r="56"/><circle cx="730" cy="170" r="65"/><circle cx="170" cy="460" r="42"/></g>
     <polyline v-for="r in roads" :key="r.properties.id" :points="points(r)" fill="none" stroke="#d3d8c5" stroke-width="16" stroke-linecap="round"/>
     <polyline v-for="r in roads" :key="r.properties.id+'inner'" :points="points(r)" fill="none" stroke="#fcfcf3" stroke-width="10" stroke-linecap="round"/>
     <polyline v-for="(r,i) in route?.features" :key="i" :points="points(r)" fill="none" stroke="#397863" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
     <g v-for="n in nodes" :key="n.properties.id" class="map-node" role="button" tabindex="0" :aria-label="placeName(n.properties.name)" @click="select(n.properties.id)" @keydown.enter="select(n.properties.id)" @keydown.space.prevent="select(n.properties.id)">
      <circle :cx="coordinates(n)[0]" :cy="coordinates(n)[1]" :r="active?.properties.id===n.properties.id?20:15" fill="white" :stroke="route?'#397863':color(n.properties.id)" stroke-width="3"/>
      <circle :cx="coordinates(n)[0]" :cy="coordinates(n)[1]" r="5" :fill="route?'#397863':color(n.properties.id)"/>
      <text :x="coordinates(n)[0]" :y="coordinates(n)[1]!+37" text-anchor="middle" fill="#35483e" font-size="13" font-weight="600" stroke="#f6f8f0" stroke-width="4" paint-order="stroke">{{placeName(n.properties.name)}}</text>
     </g>
    </g>
    <g transform="translate(790 65)" fill="#748477"><path d="M0-23l-9 23 9-4 9 4Z"/><text x="0" y="20" text-anchor="middle" font-size="12">示意</text></g>
   </svg>
   <div class="map-tools"><button aria-label="放大地图" @click="zoom=Math.min(1.5,zoom+.15)"><ZoomIn :size="18"/></button><button aria-label="缩小地图" @click="zoom=Math.max(.7,zoom-.15)"><ZoomOut :size="18"/></button><button aria-label="重置地图" @click="zoom=1"><Maximize2 :size="17"/></button></div>
  </div>
  <div v-if="active" class="map-popup"><MapPin :size="19"/><div><strong>{{placeName(active.properties.name)}}</strong><p v-if="active.properties.stay_minutes">建议停留约 {{active.properties.stay_minutes}} 分钟</p></div></div>
  <div v-if="hasCrowds" class="map-legend"><span><i class="dot low"/>较舒适</span><span><i class="dot medium"/>适中</span><span><i class="dot high"/>较拥挤</span><span><i class="dot unknown"/>未知</span></div>
 </div>
</template>
