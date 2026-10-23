<script setup lang="ts">
import {computed,ref} from 'vue'
import {LocateFixed,Minus,Plus,RotateCcw} from 'lucide-vue-next'
import type {Feature,Network} from '../types'
import type {GuideCrowd,GuidePerformance} from '../data/guide'
const props=withDefaults(defineProps<{network:Network;route?:Network;performances:GuidePerformance[];crowds:GuideCrowd[];performanceLayer?:boolean;crowdLayer?:boolean;selectedNode?:string;selectedPerformance?:string;currentNode?:string}>(),{performanceLayer:true,crowdLayer:true})
const emit=defineEmits<{selectNode:[id:string];selectPerformance:[performance:GuidePerformance];setCurrent:[]}>()
const zoom=ref(1),pan=ref({x:0,y:0}),dragging=ref(false),dragStart=ref({x:0,y:0}),panStart=ref({x:0,y:0})
const nodes=computed(()=>props.network.features.filter(f=>f.geometry.type==='Point'))
const roads=computed(()=>props.network.features.filter(f=>f.geometry.type==='LineString'))
const routeLines=computed(()=>props.route?.features.filter(f=>f.geometry.type==='LineString')||[])
const position=(feature:Feature)=>feature.geometry.coordinates as number[]
const points=(feature:Feature)=>(feature.geometry.coordinates as number[][]).map(point=>point.join(',')).join(' ')
const nodeMap=computed(()=>Object.fromEntries(nodes.value.map(node=>[node.properties.id,position(node)])))
const crowdMap=computed(()=>Object.fromEntries(props.crowds.map(crowd=>[crowd.nodeId,crowd])))
const performancePosition=(performance:GuidePerformance)=>nodeMap.value[performance.nodeId]||[0,0]
const crowdClass=(level:string)=>`guide-crowd-${level}`
function zoomBy(delta:number){zoom.value=Math.max(.75,Math.min(1.8,Number((zoom.value+delta).toFixed(2))))}
function reset(){zoom.value=1;pan.value={x:0,y:0}}
function startDrag(event:PointerEvent){dragging.value=true;dragStart.value={x:event.clientX,y:event.clientY};panStart.value={...pan.value};(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)}
function drag(event:PointerEvent){if(!dragging.value)return;pan.value={x:panStart.value.x+(event.clientX-dragStart.value.x),y:panStart.value.y+(event.clientY-dragStart.value.y)}}
function endDrag(){dragging.value=false}
function selectNode(id:string){if(!dragging.value)emit('selectNode',id)}
function selectPerformance(event:GuidePerformance){if(!dragging.value)emit('selectPerformance',event)}
</script>
<template>
 <div class="guide-map-card">
  <div class="guide-map-canvas" :class="{dragging}" @pointerdown="startDrag" @pointermove="drag" @pointerup="endDrag" @pointercancel="endDrag" @pointerleave="endDrag">
   <svg viewBox="0 0 840 540" role="img" aria-label="宋风浅立体景区示意地图，可拖动、缩放并点击点位" @click.self="reset">
    <defs>
     <pattern id="guide-paper" width="28" height="28" patternUnits="userSpaceOnUse"><rect width="28" height="28" fill="#f2eadc"/><circle cx="4" cy="5" r="1" fill="#dbcdb7" opacity=".55"/><circle cx="19" cy="21" r=".8" fill="#d7c7ad" opacity=".42"/></pattern>
     <filter id="guide-shadow" x="-30%" y="-30%" width="160%" height="170%"><feDropShadow dx="0" dy="4" stdDeviation="4" flood-color="#806b4d" flood-opacity=".22"/></filter>
    </defs>
    <rect width="840" height="540" fill="url(#guide-paper)"/>
    <g :transform="`translate(${420+pan.x} ${270+pan.y}) scale(${zoom}) translate(-420 -270)`">
     <path d="M300 225 C360 205 424 216 462 255 C501 295 521 347 496 397 C442 425 372 405 335 359 C303 320 274 266 300 225Z" fill="#a9c8c3" opacity=".8" stroke="#87aaa5" stroke-width="3"/>
     <path d="M300 245 C359 225 418 242 450 278 C480 311 488 347 473 379" fill="none" stroke="#d9ebe1" stroke-width="5" opacity=".8"/>
     <text x="390" y="312" fill="#6f9c96" font-size="13" letter-spacing="5" opacity=".8">汴河水系 · 示意</text>
     <g opacity=".8" fill="#d6ddc5" stroke="#bfcaaf" stroke-width="2"><circle cx="111" cy="158" r="57"/><circle cx="736" cy="166" r="67"/><circle cx="173" cy="461" r="44"/><path d="M670 450q40-75 103-30q-23 70-103 30Z"/></g>
     <g><polyline v-for="road in roads" :key="road.properties.id" :points="points(road)" fill="none" stroke="#bcae95" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/><polyline v-for="road in roads" :key="road.properties.id+'inner'" :points="points(road)" fill="none" stroke="#fffaf0" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/></g>
     <g v-if="routeLines.length" class="guide-route-lines"><polyline v-for="(line,index) in routeLines" :key="`route-${index}`" :points="points(line)" fill="none" stroke="#a85b4b" stroke-width="9" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="1 18"/><polyline v-for="(line,index) in routeLines" :key="`route-core-${index}`" :points="points(line)" fill="none" stroke="#f7d7c9" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></g>
     <g v-for="node in nodes" :key="`crowd-${node.properties.id}`"><circle v-if="crowdLayer&&crowdMap[node.properties.id]" :cx="position(node)[0]" :cy="position(node)[1]" r="32" :class="['guide-crowd-halo',crowdClass(crowdMap[node.properties.id].level)]"/><circle v-if="crowdLayer&&crowdMap[node.properties.id]" :cx="position(node)[0]" :cy="position(node)[1]" r="20" :class="['guide-crowd-halo-inner',crowdClass(crowdMap[node.properties.id].level)]"/></g>
     <g v-for="node in nodes" :key="node.properties.id" class="guide-node" :class="{selected:selectedNode===node.properties.id,current:currentNode===node.properties.id}" role="button" tabindex="0" :aria-label="String(node.properties.name||node.properties.id)" @click.stop="selectNode(node.properties.id)" @keydown.enter="selectNode(node.properties.id)">
      <template v-if="node.properties.kind==='poi'"><path :d="`M${position(node)[0]-19} ${position(node)[1]+14}h38v-26l-19-14-19 14Z`" class="guide-building"/><path :d="`M${position(node)[0]-25} ${position(node)[1]-12}h50`" class="guide-roof"/></template>
      <circle :cx="position(node)[0]" :cy="position(node)[1]" :r="selectedNode===node.properties.id?19:14" class="guide-node-dot"/>
      <circle :cx="position(node)[0]" :cy="position(node)[1]" r="4" class="guide-node-core"/>
      <text :x="position(node)[0]" :y="position(node)[1]+45" text-anchor="middle" class="guide-node-label">{{node.properties.name}}</text>
     </g>
     <g v-if="performanceLayer" v-for="performance in performances.filter(item=>item.status!=='ended')" :key="performance.id" class="guide-performance-marker" :class="[`status-${performance.status}`,{selected:selectedPerformance===performance.id}]" role="button" tabindex="0" :aria-label="performance.name" @click.stop="selectPerformance(performance)" @keydown.enter="selectPerformance(performance)">
      <circle :cx="performancePosition(performance)[0]" :cy="performancePosition(performance)[1]-26" r="12" class="guide-performance-pin"/><path :d="`M${performancePosition(performance)[0]-7} ${performancePosition(performance)[1]-17}L${performancePosition(performance)[0]} ${performancePosition(performance)[1]-8}L${performancePosition(performance)[0]+7} ${performancePosition(performance)[1]-17}`" class="guide-performance-tail"/><circle :cx="performancePosition(performance)[0]" :cy="performancePosition(performance)[1]-26" r="4" class="guide-performance-dot"/>
     </g>
     <g v-if="currentNode&&nodeMap[currentNode]" class="guide-location-pin"><circle :cx="nodeMap[currentNode][0]" :cy="nodeMap[currentNode][1]" r="23"/><circle :cx="nodeMap[currentNode][0]" :cy="nodeMap[currentNode][1]" r="7"/></g>
    </g>
    <g class="guide-compass" transform="translate(778 58)"><circle r="25"/><path d="M0-17 6 8 0 4-6 8Z"/><text y="37" text-anchor="middle">北</text></g>
   </svg>
   <div class="guide-map-tools"><button type="button" aria-label="放大地图" @click="zoomBy(.15)"><Plus :size="16"/></button><button type="button" aria-label="缩小地图" @click="zoomBy(-.15)"><Minus :size="16"/></button><button type="button" aria-label="重置地图" @click="reset"><RotateCcw :size="15"/></button><button type="button" aria-label="设置当前位置" @click="emit('setCurrent')"><LocateFixed :size="16"/></button></div>
   <span class="guide-map-notice">{{network.notice||'宋风示意图 · 非地理配准地图'}}</span>
  </div>
  <div class="guide-map-foot"><span><i class="guide-key road"/>示意道路</span><span><i class="guide-key route"/>演示路线</span><span><i class="guide-key event"/>演出点</span><span v-if="crowdLayer"><i class="guide-key crowd"/>客流光晕</span></div>
 </div>
</template>
