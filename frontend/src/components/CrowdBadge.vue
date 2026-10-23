<script setup lang="ts">
import {computed} from 'vue'
import type {CrowdNode} from '../types'
import {placeName} from '../presentation'
const props=defineProps<{node?:CrowdNode;loading?:boolean}>()
const labels=['宽松','舒适','适中','较拥挤','拥挤']
const grade=computed(()=>{
 const value=props.node?.level
 return value!=null&&Number.isFinite(value)&&value>=0&&value<=1?Math.min(5,Math.floor(value*5)+1):0
})
const label=computed(()=>props.loading?'查看客流中':grade.value?labels[grade.value-1]:'暂无客流')
const basis=computed(()=>props.node?.basis==='historical_estimate'?'历史参考':'观测参考')
const description=computed(()=>grade.value&&props.node?`${placeName(props.node.name)} · ${basis.value}：${label.value}，${grade.value}/5 级；为已知点位较高值，不代表全景区。`:'暂无可靠客流数据')
</script>
<template>
 <span class="crowd-badge" role="img" :class="'crowd-grade-'+grade" :aria-label="description" :title="description">
  <span class="crowd-meter" aria-hidden="true"><i v-for="n in 5" :key="n" :class="{filled:n<=grade}"/></span>
  <span>{{grade?basis+' · ':''}}{{label}}</span><span v-if="grade" class="crowd-grade">{{grade}}/5</span>
 </span>
</template>
