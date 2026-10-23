<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import FoodArt from './FoodArt.vue'
const props=defineProps<{name:string}>()
const failed=ref(false)
const fileByName:Record<string,string>={
 '宫廷杏仁茶':'xingrentea',
 '开封花生糕':'huashenggao',
 '炒凉粉':'chaoliangfen',
 '开封小笼包':'xiaolongbao',
 '套四宝':'taosibao',
 '桶子鸡':'tongziji',
 '羊肉炕馍':'yangroukangmo',
 '鲤鱼焙面':'liyubeimian',
 '四味菜':'siweicai',
}
const src=computed(()=>fileByName[props.name]?`/images/foods/${fileByName[props.name]}.jpg`:undefined)
watch(()=>props.name,()=>{failed.value=false})
</script>
<template>
 <img v-if="src&&!failed" class="food-art food-cover" :src="src" :alt="name+'主题展示图'" width="1448" height="1086" loading="lazy" decoding="async" @error="failed=true">
 <FoodArt v-else :name="name"/>
</template>
