<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {Mic,Square,X,Copy,RotateCcw} from 'lucide-vue-next'
import {api} from '../api'
import {WavRecorder} from '../audio'
import {useRouter,type RouteLocationRaw} from 'vue-router'
import {pendingVoiceText,memoryEpoch} from '../voiceDraft'
const router=useRouter()
const props=defineProps<{destination:RouteLocationRaw;anchor:{x:number;y:number;width:number;height:number};viewport:{width:number;height:number}}>()
const emit=defineEmits<{closed:[]}>()
const bubbleSide=computed(()=>props.anchor.x+props.anchor.width/2>=props.viewport.width/2?'left':'right')
const bubbleStyle=computed(()=>{
 const {anchor:a,viewport:v}=props
 const gap=9
 const space=bubbleSide.value==='left'?a.x-gap-12:v.width-a.x-a.width-gap-12
 const width=Math.min(text.value?240:96,Math.max(56,space))
 const rawLeft=bubbleSide.value==='left'?a.x-width-gap:a.x+a.width+gap
 const left=Math.max(12,Math.min(v.width-width-12,rawLeft))
 const top=Math.max(12,Math.min(v.height-240,a.y+18))
 return {width:`${width}px`,left:`${left}px`,right:'auto',top:`${top}px`,bottom:'auto',maxHeight:'none'}
})
let opener:HTMLElement|null=null,compactSession=false,compactDestination:RouteLocationRaw|undefined

const bubble=ref<HTMLElement>(),dialog=ref<HTMLDialogElement>(),editor=ref<HTMLTextAreaElement>()
const text=ref(''),error=ref(''),message=ref(''),available=ref(false),checking=ref(false),seconds=ref(0),compact=ref(false),autoStart=ref(false)
const phase=ref<'idle'|'requesting'|'recording'|'transcribing'>('idle')
const active=computed(()=>phase.value!=='idle')
const compactActive=computed(()=>compact.value)
const compactRecording=computed(()=>compact.value&&active.value)
let recorder:WavRecorder|undefined,request:AbortController|undefined,lastAudio:Blob|undefined
let timer:ReturnType<typeof setInterval>|undefined,noticeTimer:ReturnType<typeof setTimeout>|undefined,version=0

async function checkService(){
 const v=version;checking.value=true;available.value=false;error.value=''
 try{const result=await api.speechStatus();if(v!==version)return;available.value=result.available;if(!available.value)error.value='语音输入暂未开通，请稍后再试。'}
 catch(e){if(v===version)error.value=(e as Error).message}
 finally{if(v===version){checking.value=false;const shouldStart=autoStart.value&&available.value;autoStart.value=false;if(shouldStart)void start()}}
}
function open(){opener=document.activeElement as HTMLElement;cleanup();compact.value=false;dialog.value?.showModal();void checkService()}
function startCompact(){if(compact.value||active.value)return;cleanup();compactSession=true;compactDestination=props.destination;compact.value=true;dialog.value?.close();error.value='';message.value='';text.value='';autoStart.value=true;void checkService()}
function clearTimer(){if(timer)clearInterval(timer);timer=undefined}
function cleanup(){
 version++;clearTimer();if(noticeTimer)clearTimeout(noticeTimer);noticeTimer=undefined;recorder?.cancel();recorder=undefined;request?.abort();request=undefined
 phase.value='idle';checking.value=false;autoStart.value=false;lastAudio=undefined;compactSession=false;compactDestination=undefined
}
function close(){cleanup();dialog.value?.close();if(opener&&getComputedStyle(opener).display!=='none')opener.focus();else emit('closed')}
function closeCompact(){const wasOpen=compact.value;cleanup();compact.value=false;text.value='';message.value='';error.value='';if(wasOpen)emit('closed')}
async function deliverCompact(){
 if(!text.value.trim()||!compactDestination)return
 const destination=compactDestination
 pendingVoiceText.value=text.value.trim();text.value='';compactSession=false;compactDestination=undefined
 await router.push(destination)
}
function releaseCompact(){
 if(!compact.value)return
 compact.value=false
 if(phase.value==='recording')void finish()
 else if(phase.value==='idle'&&text.value.trim())void deliverCompact()
 else if(phase.value!=='transcribing')cleanup()
}
function failRecording(){
 cleanup();error.value='录音已中断，请检查麦克风后重试。'
}
async function start(){
 if(active.value||!available.value)return
 if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){error.value='当前页面无法使用麦克风，请通过 HTTPS 或本机地址打开。';return}
 if(!window.AudioContext||!window.AudioWorkletNode){error.value='当前浏览器不支持录音，请使用较新的 Safari 或 Chrome。';return}
 error.value='';message.value='';seconds.value=0;lastAudio=undefined;phase.value='requesting'
 const v=++version
 const current=new WavRecorder(failRecording);recorder=current
 try{
  if(!await current.start()||v!==version)return
  phase.value='recording'
  const started=performance.now()
  timer=setInterval(()=>{seconds.value=Math.min(60,Math.floor((performance.now()-started)/1000));if(seconds.value>=60)void finish()},200)
 }catch(e){
  if(v!==version)return
  phase.value='idle';recorder=undefined
  const name=e instanceof Error?e.name:''
  error.value=name==='NotAllowedError'?'麦克风权限未开启，请在浏览器中允许后重试。':name==='NotFoundError'?'没有找到麦克风，请连接后重试。':name==='NotReadableError'?'麦克风正被占用，请关闭其他录音后重试。':'无法开始录音，请检查麦克风和浏览器后重试。'
 }
}
async function recognize(audio:Blob,v:number){
 phase.value='transcribing';error.value='';request=new AbortController()
 try{
  const result=await api.transcribe(audio,request.signal)
  if(v!==version)return
  text.value=[text.value.trim(),result.text].filter(Boolean).join('\n')
  lastAudio=undefined;message.value='已转成文字，可以修改或复制。'
  if(compactSession&&!compact.value)await deliverCompact()
 }catch(e){if(v===version)error.value=(e as Error).message}
 finally{if(v===version){phase.value='idle';request=undefined}}
}
async function finish(){
 if(phase.value!=='recording'||!recorder)return
 phase.value='transcribing';clearTimer()
 const current=recorder,v=version
 try{
  const audio=await current.stop()
  if(v!==version)return
  recorder=undefined;lastAudio=audio
  await recognize(audio,v)
 }catch(e){if(v===version){phase.value='idle';recorder=undefined;error.value=(e as Error).message}}
}
function retry(){if(lastAudio&&!active.value)void recognize(lastAudio,version)}
async function copy(){
 try{await navigator.clipboard.writeText(text.value);message.value='已复制。'}
 catch{editor.value?.focus();editor.value?.select();message.value='已选中文字，请长按或使用复制快捷键。'}
}
async function sendToAssistant(){if(!text.value.trim()||active.value)return;pendingVoiceText.value=text.value.trim();if(compact.value)closeCompact();else close();await router.push(props.destination)}
watch([compact,error,active,checking],()=>{if(noticeTimer)clearTimeout(noticeTimer);noticeTimer=undefined;if(compact.value&&error.value&&!active.value&&!checking.value&&!text.value&&!lastAudio)noticeTimer=setTimeout(closeCompact,2200)})
defineExpose({open,startCompact,releaseCompact,closeCompact,compactActive,compactRecording})
onBeforeUnmount(cleanup)
watch(memoryEpoch,()=>{closeCompact();dialog.value?.close()})
</script>

<template>
 <button type="button" class="voice-launcher" aria-label="打开语音转文字" @click="open"><Mic :size="19"/><span>语音输入</span></button>
 <section v-if="compactActive" ref="bubble" tabindex="-1" class="voice-live-bubble" :style="bubbleStyle" :data-side="bubbleSide" @keydown.esc.prevent="closeCompact" :class="{'is-recording':active,'voice-brief-notice':!active&&!checking&&!text}" aria-label="小汴梁语音输入">
  <div v-if="checking||active" class="voice-capture-status" role="status">
   <span class="voice-dots" aria-hidden="true"><i/><i/><i/></span>
   <span class="voice-sr-only">{{phase==='recording'?`正在录音 · ${seconds} / 60 秒`:phase==='requesting'?'等待麦克风授权':phase==='transcribing'?'正在转写':'正在准备语音输入'}}</span>
  </div>
  <textarea v-if="!active&&!checking&&text" v-model="text" aria-label="核对语音转写文字" rows="2"/>
  <div v-if="error" class="voice-failure" role="status" :aria-label="available?'暂时无法录音':'语音暂不可用'"><span class="voice-dots" aria-hidden="true"><i/><i/><i/></span></div>
 </section>
 <dialog ref="dialog" class="voice-dialog" aria-labelledby="voice-title" @cancel.prevent="close">
  <div class="voice-heading"><div><span class="eyebrow">SPEAK YOUR MIND</span><h2 id="voice-title">语音转文字</h2></div><button type="button" class="voice-close" aria-label="关闭语音转文字" @click="close"><X :size="21"/></button></div>
  <p class="voice-intro">转写仅临时保留。核对后可复制，或发送到小汴梁；不会自动修改行程。</p>
  <div class="voice-capture" :class="{'is-recording':phase==='recording'}">
   <Mic :size="30" aria-hidden="true"/>
   <p aria-live="polite">{{phase==='recording'?`正在录音 · ${seconds} / 60 秒`:phase==='requesting'?'请允许使用麦克风':phase==='transcribing'?'正在转成文字…':checking?'正在准备…':'每次最多录制 60 秒'}}</p>
   <button v-if="phase==='recording'" type="button" class="button primary" @click="finish"><Square :size="15"/>结束并转文字</button>
   <button v-else type="button" class="button primary" :disabled="active||checking||!available" @click="start"><span v-if="phase==='transcribing'" class="spinner"/><Mic v-else :size="17"/>{{phase==='transcribing'?'正在识别':'开始录音'}}</button>
   <button v-if="active" type="button" class="voice-cancel" @click="cleanup">取消{{phase==='transcribing'?'识别':'录音'}}</button>
  </div>
  <div v-if="error" class="error-state" role="alert">{{error}} <button v-if="lastAudio&&!active" type="button" @click="retry"><RotateCcw :size="12"/>重试识别</button><button v-else-if="!available&&!checking" type="button" @click="checkService">重新检查</button></div>
  <label for="voice-text">转写文字</label>
  <textarea id="voice-text" ref="editor" v-model="text" rows="5" :disabled="active" placeholder="说完后，文字会显示在这里。"/>
  <p class="voice-message" aria-live="polite">{{message}}</p>
  <div class="voice-actions"><button type="button" class="button secondary" :disabled="!text||active" @click="text='';message=''">清空文字</button><button type="button" class="button primary" :disabled="!text.trim()||active" @click="copy"><Copy :size="15"/>复制文字</button></div>
  <button type="button" class="button secondary voice-send" :disabled="!text.trim()||active" @click="sendToAssistant">发送到小汴梁核对</button>
  <small class="voice-privacy">录音将发送至阿里云用于转文字，本应用不保存录音。</small>
 </dialog>
</template>

<style scoped>
.voice-launcher{position:fixed;right:24px;bottom:24px;z-index:20;display:flex;align-items:center;gap:8px;padding:13px 17px;border:1px solid #d9e3d2;border-radius:28px;background:#fff;color:var(--green);font-size:13px;box-shadow:0 4px 20px #243e3315}.voice-launcher:hover{background:#edf2e8}.voice-dialog{width:min(480px,calc(100% - 28px));max-height:calc(100dvh - 36px);overflow-y:auto;border:1px solid var(--line);border-radius:18px;padding:26px;color:var(--dark);background:#fbfcf9;box-shadow:0 20px 80px #243e3320}.voice-dialog::backdrop{background:#172b2399}.voice-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.voice-heading .eyebrow{font-size:9px;margin-bottom:8px}.voice-heading h2{font-size:24px;margin-bottom:12px}.voice-close{padding:5px;border-radius:8px}.voice-close:hover{background:var(--pale)}.voice-intro{font-size:13px;color:var(--muted);margin-bottom:16px}.voice-capture{text-align:center;border:1px solid var(--line);border-radius:12px;background:#f0f4e9;padding:20px;margin-bottom:18px;color:var(--green)}.voice-capture>svg{margin-bottom:6px}.voice-capture p{font-size:13px;margin-bottom:13px;min-height:24px}.voice-capture.is-recording{background:#edf5e8;border-color:#9cb78b}.voice-cancel{display:block;margin:12px auto 0;font-size:12px;text-decoration:underline}.voice-dialog textarea{display:block;resize:vertical;min-height:110px;width:100%;padding:12px;border:1px solid var(--line);border-radius:9px;background:#fff;color:var(--dark);font:inherit;font-size:15px;line-height:1.7}.voice-dialog textarea:focus-visible{outline:3px solid #b9cb84;outline-offset:2px}.voice-message{font-size:12px;color:var(--green);min-height:21px;margin:8px 0}.voice-actions{display:flex;justify-content:space-between;gap:12px}.voice-privacy{display:block;margin-top:18px;font-size:11px}.error-state svg{vertical-align:middle}.voice-dialog .button{min-height:44px}.voice-live-bubble{position:fixed;z-index:70;width:min(240px,calc(100vw - 24px));padding:12px 14px;border:1px solid #d9c7a5;border-radius:15px;background:#fffaf1;color:#594b39;box-shadow:0 10px 28px #4d3b2520;outline:none;overflow:visible}.voice-live-bubble:after{content:"";position:absolute;top:26px;width:10px;height:10px;background:#fffaf1;border-top:1px solid #d9c7a5;border-right:1px solid #d9c7a5;transform:rotate(45deg)}.voice-live-bubble[data-side="left"]:after{right:-6px}.voice-live-bubble[data-side="right"]:after{left:-6px;transform:rotate(225deg)}.voice-live-bubble.voice-brief-notice{width:56px;min-height:38px;padding:8px}.voice-capture-status{display:flex;align-items:center;justify-content:center;min-height:22px;margin:2px 4px}.voice-dots{display:inline-flex;align-items:center;gap:4px}.voice-dots i{width:5px;height:5px;border-radius:50%;background:#789278;animation:voice-dot 1s ease-in-out infinite}.voice-dots i:nth-child(2){animation-delay:.16s}.voice-dots i:nth-child(3){animation-delay:.32s}.voice-live-bubble textarea{display:block;width:100%;box-sizing:border-box;margin:0;padding:0;border:0;border-radius:0;background:transparent;font:inherit;font-size:16px;line-height:1.7;color:inherit;resize:none}.voice-live-bubble textarea:focus-visible{outline:1px solid #b6c6ad;outline-offset:4px}.voice-failure{display:flex;align-items:center;justify-content:center;min-height:20px;margin-top:4px;color:#918471}.voice-sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}@keyframes voice-dot{0%,80%,100%{opacity:.25;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}@media(max-width:760px){.voice-launcher{display:none!important}}
@media(max-width:520px){.voice-launcher{right:16px;bottom:max(16px,env(safe-area-inset-bottom));font-size:12px;padding:12px 15px}.voice-dialog{padding:21px}.voice-heading h2{font-size:22px}.voice-dialog textarea{font-size:16px}}
</style>

<style scoped>
@media(prefers-reduced-motion:reduce){.voice-dots i{animation:none}}
</style>
