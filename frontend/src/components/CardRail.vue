<script setup lang="ts">
import {onMounted,onBeforeUnmount,ref} from 'vue'
import {ChevronLeft,ChevronRight} from 'lucide-vue-next'
defineProps<{label:string}>()
const track=ref<HTMLDivElement>(),canBack=ref(false),canForward=ref(false)
let observer:ResizeObserver|undefined
function update(){
 const el=track.value
 if(!el)return
 canBack.value=el.scrollLeft>2
 canForward.value=el.scrollLeft+el.clientWidth<el.scrollWidth-2
}
function move(direction:number){
 const el=track.value
 if(!el)return
 const card=el.firstElementChild as HTMLElement|null
 el.scrollBy({left:direction*((card?.offsetWidth||el.clientWidth)+16),behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'})
}
onMounted(()=>{observer=new ResizeObserver(update);if(track.value)observer.observe(track.value);update()})
onBeforeUnmount(()=>observer?.disconnect())
</script>
<template>
 <div class="card-rail-wrap">
  <div ref="track" class="card-rail" role="region" :aria-label="label" tabindex="0" @scroll.passive="update" @keydown.left.prevent="move(-1)" @keydown.right.prevent="move(1)"><slot/></div>
  <div v-if="canBack||canForward" class="rail-controls"><button type="button" :aria-label="'上一项'+label" :disabled="!canBack" @click="move(-1)"><ChevronLeft :size="17"/></button><button type="button" :aria-label="'下一项'+label" :disabled="!canForward" @click="move(1)"><ChevronRight :size="17"/></button></div>
 </div>
</template>
