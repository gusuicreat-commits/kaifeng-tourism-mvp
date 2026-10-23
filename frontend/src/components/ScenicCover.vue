<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import ScenicArt from './ScenicArt.vue'
const props=withDefaults(defineProps<{id:string;name:string;theme?:string;eager?:boolean}>(),{eager:false})
const available=new Set(['qingming','longting','tieta','kaifengfu','wansui'])
const failed=ref(false)
const src=computed(()=>available.has(props.id)?`/images/scenics/${props.id}.jpg`:undefined)
watch(()=>props.id,()=>{failed.value=false})
</script>
<template>
 <img v-if="src&&!failed" class="scenic-art scenic-cover" :src="src" :alt="name+'主题展示图'" width="1448" height="1086" :loading="eager?'eager':'lazy'" decoding="async" :fetchpriority="eager?'high':'auto'" @error="failed=true">
 <ScenicArt v-else :theme="theme"/>
</template>
