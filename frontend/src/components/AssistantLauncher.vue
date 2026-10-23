<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import VoiceInput from './VoiceInput.vue'
import {assistantLink,pageFocus} from '../assistantHandoff'
import {journey} from '../journey'
const route=useRoute(),router=useRouter()
const voice=ref<InstanceType<typeof VoiceInput>|null>(null),button=ref<HTMLButtonElement>()
const position=ref<{x:number;y:number}|null>(null),viewport=ref({width:window.innerWidth,height:window.innerHeight})
const compact=computed(()=>route.path==='/assistant'&&viewport.value.width<=760)
const anchor=computed(()=>({x:position.value?.x??viewport.value.width-(compact.value?80:viewport.value.width<=760?100:108),y:position.value?.y??viewport.value.height-(compact.value?178:viewport.value.width<=760?208:148),width:compact.value?64:84,height:compact.value?94:124}))
const style=computed(()=>position.value?{left:`${position.value.x}px`,top:`${position.value.y}px`,right:'auto',bottom:'auto'}:{})
const destination=computed(()=>route.path==='/assistant'?route.fullPath:route.path==='/guide'&&route.query.scenic?{path:'/assistant',query:{context:'map',scenic:route.query.scenic}}:assistantLink(pageFocus(route),journey.trip?.id))
let timer:ReturnType<typeof setTimeout>|undefined
let gesture:{id:number;x:number;y:number;originX:number;originY:number;dragged:boolean;held:boolean}|undefined
let suppressClick=false
function clearTimer(){if(timer)clearTimeout(timer);timer=undefined}
function clamp(x:number,y:number){return {x:Math.max(8,Math.min(viewport.value.width-92,x)),y:Math.max(8,Math.min(viewport.value.height-(viewport.value.width<=760?208:132),y))}}
function resize(){viewport.value={width:window.visualViewport?.width??innerWidth,height:window.visualViewport?.height??innerHeight};if(position.value)position.value=clamp(position.value.x,position.value.y)}
function startVoice(){voice.value?.startCompact()}
function keyboardStart(event:KeyboardEvent){if(!event.repeat)startVoice()}
function cancelVoice(){clearTimer();gesture=undefined;voice.value?.closeCompact()}
function down(event:PointerEvent){
 if(event.button!==0||gesture)return
 const bounds=button.value!.getBoundingClientRect()
 gesture={id:event.pointerId,x:event.clientX,y:event.clientY,originX:bounds.x,originY:bounds.y,dragged:false,held:false};suppressClick=false
 button.value?.setPointerCapture(event.pointerId)
 timer=setTimeout(()=>{if(gesture&&!gesture.dragged){gesture.held=true;suppressClick=true;startVoice()}},550)
}
function move(event:PointerEvent){
 if(!gesture||gesture.id!==event.pointerId||gesture.held)return
 const dx=event.clientX-gesture.x,dy=event.clientY-gesture.y
 if(Math.hypot(dx,dy)>10){gesture.dragged=true;suppressClick=true;clearTimer()}
 if(gesture.dragged)position.value=clamp(gesture.originX+dx,gesture.originY+dy)
}
function end(event:PointerEvent){
 if(!gesture||gesture.id!==event.pointerId)return
 clearTimer();if(event.type!=='pointerup')suppressClick=true
 if(gesture.held){if(event.type==='pointerup')voice.value?.releaseCompact();else voice.value?.closeCompact()}
 if(gesture.dragged&&position.value)try{sessionStorage.setItem('bianyou:assistant-position',JSON.stringify(position.value))}catch{}
 gesture=undefined
}
function click(event:MouseEvent){
 if(event.detail!==0&&suppressClick){suppressClick=false;return}
 if(voice.value?.compactActive)return
 if(route.path!=='/assistant')void router.push(destination.value)
}
watch(()=>route.fullPath,()=>{clearTimer();gesture=undefined;voice.value?.closeCompact()})
onMounted(()=>{try{const saved=JSON.parse(sessionStorage.getItem('bianyou:assistant-position')||'null');if(Number.isFinite(saved?.x)&&Number.isFinite(saved?.y))position.value=clamp(saved.x,saved.y)}catch{};resize();window.addEventListener('resize',resize);window.visualViewport?.addEventListener('resize',resize)})
onBeforeUnmount(()=>{clearTimer();window.removeEventListener('resize',resize);window.visualViewport?.removeEventListener('resize',resize)})
</script>
<template>
 <button ref="button" type="button" class="global-assistant-launcher" :class="{'assistant-is-writing':voice?.compactRecording,'compact-chat-launcher':compact}" :style="style" :aria-label="route.path==='/assistant'?'小汴梁语音输入':'打开小汴梁智能助手'" aria-keyshortcuts="Shift+Enter" @pointerdown="down" @pointermove="move" @pointerup="end" @pointercancel="end" @lostpointercapture="end" @contextmenu.prevent @dragstart.prevent @click="click" @keydown.shift.enter.prevent="keyboardStart" @keyup.enter.prevent="voice?.releaseCompact()" @keydown.esc.prevent="cancelVoice" @blur="cancelVoice">
  <img src="/images/assistant/xiaobianliang-character.png" alt="" draggable="false"><span v-if="voice?.compactRecording" class="assistant-writing-pen" aria-hidden="true">✎</span><small>小汴梁</small>
 </button>
 <VoiceInput ref="voice" :destination="destination" :anchor="anchor" :viewport="viewport" @closed="button?.focus({preventScroll:true})"/>
</template>
<style scoped>
.global-assistant-launcher.compact-chat-launcher{width:64px;height:94px;right:16px;bottom:calc(84px + env(safe-area-inset-bottom,0px))}.compact-chat-launcher img{width:62px;height:76px}.compact-chat-launcher small{font-size:10px}.compact-chat-launcher:before{width:40px;left:12px}
.global-assistant-launcher{touch-action:none;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;cursor:grab;height:124px;padding:0;transition:none}.global-assistant-launcher:hover{transform:none}.global-assistant-launcher:active{cursor:grabbing}.global-assistant-launcher:focus-visible{outline:2px solid #946b3c;outline-offset:4px;border-radius:12px}.assistant-writing-pen{position:absolute;right:0;top:55px;font-size:30px;color:#946b3c;animation:assistant-pen .7s ease-in-out infinite}.assistant-is-writing img{transform-origin:50% 85%;animation:assistant-writing 1.1s ease-in-out infinite}@keyframes assistant-writing{0%,100%{transform:rotate(-2deg) translateY(0)}50%{transform:rotate(2deg) translateY(-3px)}}@keyframes assistant-pen{0%,100%{transform:translate(0,0) rotate(-15deg)}50%{transform:translate(5px,-4px) rotate(5deg)}}@media(prefers-reduced-motion:reduce){.assistant-is-writing img,.assistant-writing-pen{animation:none}}
</style>
