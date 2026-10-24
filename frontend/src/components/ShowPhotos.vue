<script setup lang="ts">
import {ref,watch} from 'vue'
import {ArrowUpRight,X} from 'lucide-vue-next'

const props=defineProps<{name:string;photos:string[]}>()
const active=ref<string>(),dialog=ref<HTMLDialogElement>()
function open(src:string){active.value=src;dialog.value?.showModal()}
function close(){dialog.value?.close();active.value=undefined}
watch(()=>props.photos,close)
</script>

<template>
 <div class="show-photo-gallery">
  <div class="show-photo-heading"><span>现场掠影</span><small>{{photos.length}} 张 · 左右滑动</small></div>
  <div class="show-photos" :aria-label="name+'现场图集'" tabindex="0">
   <button v-for="(photo,index) in photos" :key="photo" type="button" :aria-label="'放大查看：'+name+'现场照片 '+(index+1)" @click="open(photo)">
    <img :src="photo" :alt="name+'现场照片 '+(index+1)" loading="lazy" decoding="async"/>
    <span class="show-photo-index">{{String(index+1).padStart(2,'0')}}</span><ArrowUpRight :size="18"/>
   </button>
  </div>
  <dialog ref="dialog" class="media-dialog" :aria-label="name+'现场大图'" @click="($event.target===dialog)&&close()" @close="active=undefined">
   <button type="button" aria-label="关闭大图" @click="close"><X :size="23"/></button>
   <figure v-if="active"><img :src="active" :alt="name+'现场照片'"/><figcaption>{{name}} · 开封景区节目清单.xlsx</figcaption></figure>
  </dialog>
 </div>
</template>
