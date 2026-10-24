<script setup lang="ts">
import {computed,ref} from 'vue'
import {useRoute} from 'vue-router'
import {ArrowLeft,MapPin,Clock3,Coins,Utensils,X,ChevronRight} from 'lucide-vue-next'
import ExternalMap from '../components/ExternalMap.vue'
import {foodProfiles,foodWorkbookStatus} from '../data/detailContent'
import {foodCatalog} from '../data/foods'
const route=useRoute(),dialog=ref<HTMLDialogElement>(),active=ref('')
const entry=computed(()=>Object.entries(foodProfiles).find(([,food])=>food.vendors.some(v=>v.id===route.params.id)))
const food=computed(()=>entry.value?.[1]),vendor=computed(()=>food.value?.vendors.find(v=>v.id===route.params.id))
const photos=computed(()=>[...(vendor.value?.photos||[]),...(vendor.value?.dishPhotos||[])])
const foodName=computed(()=>foodCatalog.find(item=>item.id===food.value?.id)?.name)
function open(src:string){active.value=src;dialog.value?.showModal()}
function close(){dialog.value?.close()}
</script>
<template><div v-if="vendor&&food" class="page food-vendor-page editorial-detail"><RouterLink :to="'/food/'+food.id" class="back-link"><ArrowLeft :size="16"/>返回{{foodName}}</RouterLink><div class="workbook-vendor-gallery"><button v-for="(src,index) in vendor.photos" :key="src" type="button" @click="open(src)"><img :src="src" :alt="vendor.name+'门头或环境照片'+(index+1)"></button></div><header class="editorial-title"><span class="tiny-caption">团队资料商家</span><h1>{{vendor.name}}</h1><p>{{foodWorkbookStatus}}</p></header><dl class="detail-fact-grid"><div><dt><MapPin :size="16"/>详细地址</dt><dd>{{vendor.address||'待添加'}}</dd></div><div><dt><Clock3 :size="16"/>营业时间</dt><dd>{{vendor.hours||'待添加'}}</dd></div><div><dt><Coins :size="16"/>人均</dt><dd>{{vendor.perCapita||'待添加'}}</dd></div><div><dt><Utensils :size="16"/>表内菜品价格</dt><dd>{{vendor.dishPrice||'待添加'}}</dd></div></dl><section class="editorial-section"><div class="detail-section-title"><h2>门店与美食实拍</h2><span class="pending-caption">{{photos.length}} 张</span></div><div class="photo-strip"><button v-for="src in photos" :key="src" type="button" class="detail-photo" @click="open(src)"><img :src="src" :alt="vendor.name+'团队资料实拍'" loading="lazy"><small>点击查看大图</small></button></div></section><section class="editorial-section"><h2>到店指引</h2><ExternalMap v-if="vendor.address" :name="vendor.name" :address="vendor.address"/><p v-else class="content-pending">具体位置待添加</p></section><RouterLink class="detail-basket-link" :to="'/food/'+food.id">查看对应美食 <ChevronRight :size="17"/></RouterLink><dialog ref="dialog" class="media-dialog" @click="($event.target===dialog)&&close()"><button type="button" aria-label="关闭大图" @click="close"><X :size="23"/></button><figure><img :src="active" :alt="vendor.name+'实拍大图'"><figcaption>团队资料整理 · 点击空白处关闭</figcaption></figure></dialog></div><div v-else class="page journey-empty"><h1>商家资料待添加</h1><RouterLink to="/">返回发现</RouterLink></div></template>
