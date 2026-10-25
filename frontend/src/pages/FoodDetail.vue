<script setup lang="ts">
import {computed,onMounted} from 'vue'
import {useRoute} from 'vue-router'
import {ArrowLeft,MapPin,Utensils,ChevronRight,Clock3} from 'lucide-vue-next'
import FoodCover from '../components/FoodCover.vue'
import DestinationActions from '../components/DestinationActions.vue'
import DetailMedia from '../components/DetailMedia.vue'
import DetailAsk from '../components/DetailAsk.vue'
import {foodCatalog} from '../data/foods'
import {journey,loadJourney,vendorsFor,localDate,clock} from '../journey'
import {useDetailObject} from '../detailObject'
import {foodProfiles} from '../data/detailContent'
const route=useRoute(),food=computed(()=>foodCatalog.find(f=>f.id===route.params.id))
const {object}=useDetailObject('food',()=>String(route.params.id))
const profile=computed(()=>foodProfiles[String(route.params.id)])
const vendors=computed(()=>food.value&&object.value?.fact_id?vendorsFor(food.value.name,object.value.fact_id):[])
const opening=(id:string)=>{const rule=journey.catalog.find(x=>x.record.kind==='open_rule'&&x.record.place_id===id&&x.record.business_date===localDate()&&x.ready);return rule?.record.opens_at&&rule.record.closes_at?clock(rule.record.opens_at)+'–'+clock(rule.record.closes_at):'营业时间待添加'}
onMounted(()=>loadJourney())
</script>
<template><div v-if="food" class="page food-detail-page editorial-detail"><RouterLink to="/" class="back-link"><ArrowLeft :size="17"/>返回发现</RouterLink><figure class="food-detail-hero"><FoodCover :name="food.name" :photo-src="profile?.photos[0]"/><figcaption>{{profile?'团队资料实拍':'主题插画'}}</figcaption></figure><section class="food-detail-body"><div class="food-detail-title"><div><span class="tiny-caption">开封风味</span><h1>{{food.name}}</h1></div><DestinationActions kind="food" :id="food.id" :name="food.name"/></div><p class="food-intro">{{food.intro}}</p><dl class="detail-fact-grid food-detail-facts"><div><dt><Utensils :size="16"/>味道与特色</dt><dd>{{profile?.intro||'详细介绍待添加'}}</dd></div></dl><h2>对应商家</h2><p v-if="profile" class="source-note">团队资料整理 · 商家信息与供应关系待复核</p><article v-for="vendor in profile?.vendors||[]" :key="vendor.id" class="vendor-card workbook-vendor"><RouterLink :to="'/food-vendor/'+vendor.id" class="workbook-vendor-main"><img v-if="vendor.photos.length" :src="vendor.photos[0]" :alt="vendor.name+'门头或环境'" loading="lazy" decoding="async"><div><span class="tiny-caption">团队资料商家</span><h2>{{vendor.name}} <ChevronRight :size="18"/></h2><p><MapPin :size="14"/>{{vendor.address||'详细地址待添加'}}</p><small><Clock3 :size="13"/>{{vendor.hours||'营业时间待添加'}}<span v-if="vendor.perCapita">人均 {{vendor.perCapita}}</span></small></div></RouterLink></article><template v-if="!profile||vendors.length"><p v-if="journey.loading" class="empty-copy" role="status">正在查看店家资料…</p><div v-else-if="!vendors.length" class="merchant-pending"><div class="merchant-pending-photo"><Utensils :size="26" :stroke-width="1.2"/></div><div><h2>店家资料待添加</h2><p>供应菜品、地址与营业信息待添加</p><RouterLink class="quiet-link" to="/vendor/pending">查看商家详情框架 <ChevronRight :size="13"/></RouterLink></div><button v-if="journey.error" type="button" class="text-link" @click="loadJourney(true)">重试加载</button></div><article v-for="vendor in vendors" :key="vendor.record.fact_id" class="vendor-card"><RouterLink :to="'/place/'+encodeURIComponent(vendor.record.fact_id)"><span class="tiny-caption">已核验供应关系</span><h2>{{vendor.record.name}} <ChevronRight :size="18"/></h2><p><MapPin :size="14"/>{{vendor.record.address||'详细地址待添加'}}</p><small>{{opening(vendor.record.fact_id)}}</small></RouterLink><DestinationActions kind="place" :id="vendor.record.fact_id" :name="vendor.record.name||'餐饮地点'"/></article></template></section><DetailMedia kind="food" :id="food.id" :name="food.name"/><DetailAsk kind="food" :id="food.id" :name="food.name"/><RouterLink class="detail-basket-link" to="/basket">整理旅行素材篮 <ChevronRight :size="17"/></RouterLink></div><div v-else class="page journey-empty"><h1>暂未收录这道美食</h1><RouterLink to="/">返回发现</RouterLink></div></template>
