<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {useRoute} from 'vue-router'
import {journey} from '../journey'

// 页头常驻的旅行素材篮入口：竹编小篮 + 已收藏数量。
const route=useRoute()
const count=computed(()=>journey.choices.length)
const active=computed(()=>route.path==='/basket')
const label=computed(()=>count.value?`旅行素材篮，已收藏 ${count.value} 项`:'旅行素材篮，还没有收藏')
const bump=ref(false)
let timer:ReturnType<typeof setTimeout>|undefined
watch(count,(now,before)=>{
 if(now<=before)return
 bump.value=false;clearTimeout(timer)
 requestAnimationFrame(()=>{bump.value=true;timer=setTimeout(()=>{bump.value=false},700)})
})
</script>

<template>
 <RouterLink to="/basket" class="basket-entry" :class="{active,bump}" :aria-label="label" :title="label" :aria-current="active?'page':undefined">
  <svg class="basket-art" viewBox="0 0 32 32" width="24" height="24" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
   <path d="M9.5 13.2C9.5 7.6 12.4 4.6 16 4.6s6.5 3 6.5 8.6" stroke-width="1.6"/>
   <path d="M5.2 13.4h21.6" stroke-width="1.8"/>
   <path d="M6.6 13.6l2.3 11.2c.2 1.1 1.1 1.8 2.2 1.8h9.8c1.1 0 2-.7 2.2-1.8l2.3-11.2" stroke-width="1.6"/>
   <path d="M7.7 18.2h16.6M8.7 22.4h14.6" stroke-width="1.1" opacity=".75"/>
   <path d="M12.4 13.8l1 12.6M16 13.8v12.6M19.6 13.8l-1 12.6" stroke-width="1.1" opacity=".75"/>
  </svg>
  <span v-if="count" class="basket-count" aria-hidden="true">{{count>99?'99+':count}}</span>
 </RouterLink>
</template>

<style scoped>
.basket-entry{position:relative;display:inline-flex;align-items:center;justify-content:center;width:40px;height:40px;flex:none;border:1px solid #c9b48f;border-radius:50%;background:#f3ecdd;color:#816c4e;text-decoration:none;transition:background .2s,color .2s,border-color .2s}
.basket-entry:hover,.basket-entry.active{background:#ece0c8;border-color:#a99877;color:#5f4c33}
.basket-entry:focus-visible{outline:2px solid #b18a58;outline-offset:3px}
.basket-count{position:absolute;top:-5px;right:-6px;min-width:18px;height:18px;padding:0 5px;border:1.5px solid #f8f4ec;border-radius:9px;background:#b18a58;color:#fffaf0;font:600 10px/15px Inter,"PingFang SC",sans-serif;text-align:center;letter-spacing:0}
.basket-entry.bump .basket-art{animation:basket-bump .6s ease}
.basket-entry.bump .basket-count{animation:count-pop .6s ease}
@keyframes basket-bump{0%,100%{transform:rotate(0)}25%{transform:rotate(-9deg)}55%{transform:rotate(7deg)}80%{transform:rotate(-3deg)}}
@keyframes count-pop{0%,100%{transform:scale(1)}40%{transform:scale(1.3)}}
@media(prefers-reduced-motion:reduce){.basket-entry.bump .basket-art,.basket-entry.bump .basket-count{animation:none}}
@media(max-width:760px){.basket-entry{width:36px;height:36px}.basket-art{width:21px;height:21px}}
</style>
