<script setup lang="ts">
import {computed} from 'vue'
import {Heart,Plus,Check} from 'lucide-vue-next'
import type {Choice} from '../journey'
import {journey,choice,toggleFavorite,isFavorite,addChoice,removeChoice} from '../journey'
const props=defineProps<{kind:Choice['kind'];id:string;name:string;source?:Choice['source']}>()
const item=()=>({...choice(props.kind,props.id,props.name),source:props.source||'detail' as const})
const selected=computed(()=>journey.choices.find(x=>x.key===item().key))
async function toggle(){if(selected.value)await removeChoice(selected.value.key);else await addChoice(item())}
</script>
<template><div class="destination-actions"><button type="button" class="save-place" :aria-label="(isFavorite(item())?'取消收藏':'收藏')+name" :aria-pressed="isFavorite(item())" @click.stop="toggleFavorite(item())"><Heart :size="16" :fill="isFavorite(item())?'currentColor':'none'"/></button><button type="button" class="add-place" :aria-label="(selected?'移出素材篮：':'加入素材篮：')+name" :aria-pressed="!!selected" :disabled="journey.basketBusy" @click.stop="toggle"><Check v-if="selected" :size="15"/><Plus v-else :size="15"/>{{selected?'已加入素材篮':'加入素材篮'}}</button></div></template>
