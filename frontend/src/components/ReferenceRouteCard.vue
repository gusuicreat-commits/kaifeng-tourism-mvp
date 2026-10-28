<script setup lang="ts">
import type {ReferenceRoute} from '../cityAgentApi'
import {navigationUrl} from '../amap'
defineProps<{route:ReferenceRoute}>()
const modes={walking:'步行',driving:'驾车',transit:'公交'}
</script>
<template><section class="reference-route" aria-label="高德参考路线">
 <header><span>高德参考路线 · {{modes[route.mode]}}</span><small>当前查询估计</small></header>
 <h3>{{route.origin}} → {{route.destination}}</h3>
 <p v-if="route.destination_entrance" class="reference-entrance">终点：最近入口 {{route.destination_entrance.name}}（{{route.destination_entrance.basis==='catalog'?'按资料库位置':`按高德“${route.destination_entrance.poi_name}”定位`}}，未现场核验）</p>
 <a v-if="route.destination_point" class="reference-navigate" :href="navigationUrl(route.destination_point,route.destination,route.mode)" target="_blank" rel="noopener noreferrer">在高德中导航</a>
 <p class="reference-metrics">约 {{Math.ceil(route.duration_seconds/60)}} 分钟 · {{route.distance_m>=1000?(route.distance_m/1000).toFixed(1)+' 公里':Math.round(route.distance_m)+' 米'}}</p>
 <p v-if="route.taxi_cost_fen!=null">打车约 ¥{{Number((route.taxi_cost_fen/100).toFixed(2))}}（高德估计，按车计）</p>
 <p v-if="route.parking?.length">附近停车场：{{route.parking.map(p=>`${p.name}（约${p.straight_m}米）`).join('、')}}（高德资料，收费与车位未核实）</p>
 <ol v-if="route.steps.length"><li v-for="(step,i) in route.steps" :key="i">{{step}}</li></ol>
 <p class="reference-note">以所选地点为两端，出入口尚未核验。此为出行参考，不代表已确认行程或保证到达；临时道路与现场通行以实际为准。</p>
 <small>查询于 {{new Date(route.queried_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit'})}} · 参考结果不会写入正式路线</small>
</section></template>
<style scoped>
.reference-route{padding:16px;margin:20px 0;border:1px solid #c5cdb8;border-radius:3px 16px;background:#f1f4e9;color:#29483b;overflow-wrap:anywhere}.reference-route header{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;font-size:13px}.reference-route h3{font:20px/1.6 'Songti SC',serif;margin:14px 0}.reference-metrics{font-size:18px}.reference-route ol{padding-left:22px;font-size:14px;line-height:1.9}.reference-note,.reference-route small{font-size:12px;line-height:1.8;color:#71806a}.reference-route li{margin:7px 0}.reference-entrance{font-size:14px;line-height:1.8;margin:0 0 8px}.reference-navigate{display:inline-block;margin:0 0 10px;padding:6px 14px;border:1px solid #29483b;border-radius:3px 12px;color:#29483b;font-size:14px;text-decoration:none}
</style>
