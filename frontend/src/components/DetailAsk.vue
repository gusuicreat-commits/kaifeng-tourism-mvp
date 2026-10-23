<script setup lang="ts">
import {ref} from 'vue'
import {useRouter} from 'vue-router'
import {ArrowUpRight,Send} from 'lucide-vue-next'
import type {ObjectKind} from '../basketApi'
import {assistantLink,handoffQuestion} from '../assistantHandoff'
import {journey} from '../journey'
const props=defineProps<{kind:ObjectKind;id:string;name:string}>(),router=useRouter(),text=ref('')
const prompts=props.kind==='food'?['这道菜有什么特色？','附近哪里可以吃到？']:props.kind==='vendor'||props.kind==='place'?['这家店目前是否营业？','能安排到午餐时间吗？']:['这里最值得看什么？','有哪些演出？','这里适合游玩多久？']
function ask(question:string){if(!question.trim())return;const focus={kind:props.kind,object_id:props.id};handoffQuestion(focus,question);void router.push(assistantLink(focus,journey.trip?.id))}
</script>
<template><section class="detail-ask" :aria-label="'询问小汴梁：'+name"><div class="detail-ask-heading"><img src="/images/assistant/xiaobianliang-character.png" alt=""><div><h2>问问小汴梁</h2><p>正在聊 · {{name}}</p></div><ArrowUpRight :size="19"/></div><div class="detail-question-chips"><button v-for="q in prompts" :key="q" type="button" @click="ask(q)">{{q}}</button></div><form @submit.prevent="ask(text)"><label class="visually-hidden" :for="'ask-'+id">向小汴梁提问</label><input :id="'ask-'+id" v-model="text" placeholder="关于这里，你想知道…" maxlength="2000"><button type="submit" :disabled="!text.trim()" aria-label="发送问题给小汴梁"><Send :size="18"/></button></form></section></template>
