<script setup lang="ts">
import {ref,onMounted,onBeforeUnmount,computed,watch} from 'vue'
import {MapPin,Navigation,ExternalLink} from 'lucide-vue-next'
import {loadAmap,plugin} from '../amap'
const props=defineProps<{name:string;address:string}>(),container=ref<HTMLDivElement>(),status=ref(''),loaded=ref(false)
const link=computed(()=>`https://uri.amap.com/search?keyword=${encodeURIComponent(props.name)}&city=${encodeURIComponent('开封')}&view=map`)
let map:any,marker:any,dead=false,sequence=0
async function search(){const seq=++sequence;loaded.value=false;marker?.setMap(null);try{const A=await loadAmap();if(dead||seq!==sequence)return;if(!map)map=new A.Map(container.value,{zoom:12,center:[114.307,34.799]});await plugin(A,'AMap.PlaceSearch');if(dead||seq!==sequence)return;await new Promise<void>((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('地点查询超时')),10000);new A.PlaceSearch({city:'开封',citylimit:true,pageSize:5}).search(props.name,(s:string,r:any)=>{clearTimeout(timer);if(dead||seq!==sequence){resolve();return}const p=r?.poiList?.pois?.find((p:any)=>p.name.includes(props.name));if(s!=='complete'||!p?.location){reject(new Error('暂未找到匹配地点'));return}marker?.setMap(null);marker=new A.Marker({position:p.location,title:p.name});map.add(marker);map.setZoomAndCenter(15,p.location);loaded.value=true;status.value='高德搜索地点，非已核验入口；前往请核对目的地。';resolve()})})}catch(e){if(!dead&&seq===sequence)status.value=(e as Error).message}}
onMounted(()=>void search());watch(()=>props.name,()=>void search());onBeforeUnmount(()=>{dead=true;sequence++;map?.destroy()})
</script>
<template><div class="external-map"><div ref="container" class="amap-container"/><div v-if="!loaded" class="external-fallback"><div class="location-ring"><MapPin :size="30"/></div><h3>{{name}}</h3><p>{{address}}</p><span class="pill">位置与入口以高德搜索结果为准</span></div><div class="external-bottom"><p>{{status}}</p><a class="button secondary" :href="link" target="_blank" rel="noopener noreferrer"><Navigation :size="16"/> 高德位置 / 前往路线 <ExternalLink :size="14"/></a></div></div></template>
