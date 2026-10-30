<script setup lang="ts">
import {computed,ref} from 'vue'

const emit=defineEmits<{
 submit:[sentence:string]
 dismiss:[]
}>()

const avoidOptions=[
 {label:'走太多路',value:'少走路'},
 {label:'行程太赶',value:'节奏轻松一点'},
 {label:'花太多钱',value:'尽量省钱'},
]
const modeOptions=[
 {label:'走路为主',value:'走路就行'},
 {label:'公交地铁',value:'坐公交'},
 {label:'打车',value:'打车'},
]
const avoid=ref<string[]>([]),mode=ref('')
const sentence=computed(()=>[...avoidOptions.filter(option=>avoid.value.includes(option.value)).map(option=>option.value),mode.value].filter(Boolean).join('，'))
const canSubmit=computed(()=>Boolean(sentence.value))

function toggleAvoid(value:string){avoid.value=avoid.value.includes(value)?avoid.value.filter(item=>item!==value):[...avoid.value,value]}
function chooseMode(value:string){mode.value=mode.value===value?'':value}
</script>

<template>
 <section class="preference-card" aria-label="出发前问你几个问题">
  <div class="preference-card-heading"><h2>出发前问你几个问题</h2><p>点选就行，也可以跳过。选好的会作为排程条件，做不到的会在方案里说明。</p></div>
  <div class="preference-card-field">
   <h3>这次旅行，你不想遇到什么？</h3>
   <div class="interview-choices" role="group" aria-label="这次旅行，你不想遇到什么">
    <button v-for="option in avoidOptions" :key="option.value" type="button" :aria-pressed="avoid.includes(option.value)" @click="toggleAvoid(option.value)">{{option.label}}</button>
   </div>
  </div>
  <div class="preference-card-field">
   <h3>你想怎么在景点之间移动？</h3>
   <div class="interview-choices" role="group" aria-label="你想怎么在景点之间移动">
    <button v-for="option in modeOptions" :key="option.value" type="button" :aria-pressed="mode===option.value&&!!mode" @click="chooseMode(option.value)">{{option.label}}</button>
   </div>
  </div>
  <p class="preference-card-note">口味偏好可以直接问小汴梁“推荐吃什么”。</p>
  <div class="interview-actions preference-card-actions">
   <button type="button" class="button primary" :disabled="!canSubmit" @click="emit('submit',sentence)">按这些安排</button>
   <button type="button" class="button secondary" @click="emit('dismiss')">跳过</button>
  </div>
 </section>
</template>

<style scoped>
.preference-card{width:100%;box-sizing:border-box;margin-top:20px;padding:18px 20px;border:1px solid #c7cfbd;border-radius:3px 16px 3px 16px;background:#f4f7ee;color:#304e43;overflow-wrap:anywhere}
.preference-card-heading h2{margin:0;font:20px/1.5 "Songti SC","STSong",serif;color:#29483b}.preference-card-heading p{margin:4px 0 18px;color:#6d7968;font-size:13px;line-height:1.7}
.preference-card-field{margin-top:16px}.preference-card-field h3{margin:0 0 8px;font:15px/1.6 "Songti SC","STSong",serif;color:#3d6252}.preference-card .interview-choices{display:flex;flex-wrap:wrap;gap:9px}.preference-card .interview-choices button{display:flex;align-items:center;gap:6px;border:1px solid #dfd2bd;padding:10px 13px;background:#fffaf1;border-radius:3px;font-size:12px;color:#776b5b;line-height:1.6;min-height:44px}.preference-card .interview-choices button[aria-pressed=true]{background:#e9efe3;border-color:#77917a;color:#3d6252}
.preference-card-note{margin:18px 0 0;color:#8a806f;font-size:12px;line-height:1.7}.preference-card-actions{justify-content:flex-end;margin-top:16px}.preference-card-actions .button{min-height:44px}.preference-card-actions .button:disabled{cursor:not-allowed;opacity:.5}
@media(max-width:760px){.preference-card{padding:16px 14px}.preference-card-heading h2{font-size:18px}.preference-card .interview-choices{gap:6px}.preference-card .interview-choices button{padding:9px 10px;font-size:11px}.preference-card-actions{justify-content:flex-start;gap:6px}.preference-card-actions .button{padding:8px 16px}}
</style>
