<script setup lang="ts">
import {onBeforeUnmount,ref,watch} from 'vue'
import {locationApi,type LocationCandidate,type LocationSearch} from '../locationApi'
import {memoryEpoch} from '../voiceDraft'
const props=defineProps<{query:string;label:string;disabled?:boolean}>()
const emit=defineEmits<{confirmed:[name:string,factId:string|null]}>()
const results=ref<LocationSearch|null>(null),selected=ref<LocationCandidate|null>(null),busy=ref(false),message=ref(''),matched=ref(false)
let controller:AbortController|null=null,sequence=0
function reset(){sequence++;controller?.abort();results.value=null;selected.value=null;message.value='';matched.value=false;busy.value=false}
watch(()=>props.query,()=>{if(props.query.trim()!==selected.value?.name)reset()})
watch(memoryEpoch,reset)
onBeforeUnmount(reset)
async function search(){
 reset();const query=props.query.trim();if(!query)return
 const id=sequence,epoch=memoryEpoch.value;busy.value=true;controller=new AbortController()
 try{const result=await locationApi.search(query,controller.signal);if(id!==sequence||epoch!==memoryEpoch.value)return;results.value=result
  if(result.status!=='candidates')message.value=result.provider_status==='disabled'?'地点查询尚未启用，已保留你填写的地名。':'暂未查到可供确认的地点，请换用全称或补充地址。'
 }catch(e){if(id===sequence)message.value=(e as Error).message}finally{if(id===sequence)busy.value=false}
}
async function confirm(candidate:LocationCandidate){
 if(!results.value?.resolution_id||busy.value)return
 const id=sequence,epoch=memoryEpoch.value;busy.value=true;controller=new AbortController()
 try{const result=await locationApi.confirm(results.value.resolution_id,candidate.candidate_id,controller.signal);if(id!==sequence||epoch!==memoryEpoch.value)return
  selected.value=result.selected;results.value=null;matched.value=!!result.formal_match;emit('confirmed',result.selected.name,result.formal_match?.fact_id||null)
 }catch(e){if(id===sequence)message.value=(e as Error).message}finally{if(id===sequence)busy.value=false}
}
</script>
<template><section class="location-picker" :aria-label="label+'地点核对'">
 <button type="button" class="text-link" :disabled="disabled||busy||!query.trim()" @click="search">{{busy?'正在查阅地点…':'查找并核对'+label}}</button>
 <p v-if="message" role="status">{{message}}</p>
 <template v-if="results?.status==='candidates'">
  <p>请选择你所指的地点。地图位置尚不等于可通行的出入口。</p>
  <ul><li v-for="candidate in results.candidates" :key="candidate.candidate_id">
   <strong>{{candidate.name}}</strong><small>{{candidate.address||'地址待补充'}}</small>
   <button type="button" :disabled="busy||disabled" @click="confirm(candidate)">确认此地点</button>
  </li></ul>
 </template>
 <p v-if="selected&&matched" role="status">已选定「{{selected.name}}」并匹配已有核验地点，接下来核对本次日期、出入口与交通条件。</p>
 <p v-else-if="selected" role="status">已选定「{{selected.name}}」。出入口、开放时间与交通依据仍待核验，暂不能据此生成正式路线。</p>
</section></template>
<style scoped>
.location-picker{padding:8px 0;color:#6c7d69;font-size:13px;line-height:1.7}.location-picker p{margin:8px 0}.location-picker ul{list-style:none;margin:8px 0;padding:0;display:grid;gap:8px}.location-picker li{display:grid;gap:6px;padding:12px;border:1px solid #d8cfbb;border-radius:8px;background:#fffcf5}.location-picker strong{color:#29483b}.location-picker small{font-size:12px}.location-picker button{justify-self:start;min-height:44px;border:1px solid #d8cfbb;padding:8px 12px;border-radius:6px;color:#29483b}.location-picker .text-link{border:0;padding-left:0}
</style>
