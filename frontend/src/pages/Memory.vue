<script setup lang="ts">
import {onMounted,ref,watch} from 'vue'
import {ArrowLeft,Download,Trash2} from 'lucide-vue-next'
import {api} from '../api'
import type {MemorySession} from '../memoryTypes'
import {clearMemoryCaches,memoryEpoch} from '../voiceDraft'
const session=ref<MemorySession>(),busy=ref(false),error=ref(''),confirmDelete=ref(false),receipt=ref('')
async function load(){busy.value=true;error.value='';const epoch=memoryEpoch.value;try{const value=await api.memorySession();if(epoch===memoryEpoch.value)session.value=value}catch(e){if(epoch===memoryEpoch.value)error.value=(e as Error).message}finally{busy.value=false}}
async function toggleHistory(event:Event){busy.value=true;error.value='';try{const checked=(event.target as HTMLInputElement).checked;await api.history(checked);if(session.value)session.value.history_enabled=checked}catch(e){error.value=(e as Error).message;await load()}finally{busy.value=false}}
async function download(){busy.value=true;error.value='';try{const data=await api.exportMemory();const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='我的本次游览记录.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function remove(){busy.value=true;error.value='';try{const result=await api.deleteMemory();clearMemoryCaches();receipt.value=result.receipt_id;session.value=undefined;confirmDelete.value=false}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
watch(memoryEpoch,()=>{session.value=undefined;confirmDelete.value=false})
onMounted(load)
</script>
<template><div class="page memory-page settings-page">
 <RouterLink to="/me" class="back-link"><ArrowLeft :size="16"/>返回我的游记</RouterLink><h1>设置</h1>
 <div v-if="error" role="alert" class="error-state">{{error}} <button @click="load">重试</button></div>
 <p v-if="busy" role="status">正在处理…</p>
 <div v-if="receipt" class="settings-receipt"><p role="status">本次记录已清除</p><RouterLink to="/me" class="button secondary">返回我的游记</RouterLink><details><summary>查看删除回执</summary><small>{{receipt}}</small></details></div>
 <template v-if="session"><section class="settings-section"><h2>聊天记录</h2><label class="settings-toggle"><span>保留聊天记录</span><input type="checkbox" :checked="session.history_enabled" :disabled="busy" @change="toggleHistory"></label><p>关闭会清空聊天，行程仍保留。</p></section>
 <section class="settings-section"><h2>数据管理</h2><button class="settings-action" :disabled="busy" @click="download"><Download :size="18"/>导出本次记录</button><button class="settings-action settings-delete" :disabled="busy" @click="confirmDelete=true"><Trash2 :size="18"/>删除我的本次记录</button><div v-if="confirmDelete" class="memory-proposal"><p>将删除收藏、行程和聊天记录，无法恢复。</p><button class="button primary" :disabled="busy" @click="remove">确认删除本次记录</button><button class="button secondary" :disabled="busy" @click="confirmDelete=false">取消</button></div></section>
 <details class="settings-about"><summary>关于数据保留</summary><p>记录关联当前浏览器会话，连续24小时未使用后失效。</p><p>语音通过阿里云转写，本应用不保存原始录音。</p></details></template>
</div></template>
<style scoped>
.settings-page{max-width:720px;padding-bottom:120px}.settings-page h1{font:32px "Songti SC",serif;margin:24px 0 30px}.settings-section{padding:20px 0;border-bottom:1px solid #e0d3bf}.settings-section h2{font:20px "Songti SC",serif;margin:0 0 14px}.settings-section p{font-size:12px;color:#98856a;line-height:1.7;margin:8px 0}.settings-toggle{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:44px;font-size:15px}.settings-toggle input{width:21px;height:21px;accent-color:#315642}.settings-action{display:flex;align-items:center;gap:12px;width:100%;min-height:52px;font-size:14px;text-align:left;color:#5f604d}.settings-delete{color:#a16550}.settings-about{margin-top:24px;color:#9b8d76;font-size:12px}.settings-about summary{min-height:44px;cursor:pointer}.settings-about p{font-size:12px;line-height:1.8;margin:10px 0}.settings-receipt{padding:24px 0}.settings-receipt details{margin-top:24px;font-size:12px;color:#9b8d76}.settings-receipt small{overflow-wrap:anywhere}.memory-proposal .button{margin:8px 8px 0 0}
</style>
