<script setup lang="ts">
import type {WeatherReport} from '../cityAgentApi'
const props=defineProps<{report:WeatherReport;busy?:boolean}>()
const emit=defineEmits<{adjust:[]}>()
const clock=(value:string)=>new Date(value).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'})
const day=(value:string)=>new Date(value).toLocaleDateString('zh-CN',{month:'numeric',day:'numeric',timeZone:'Asia/Shanghai'})
const wind=(value:string)=>({n:'北',ne:'东北',e:'东',se:'东南',s:'南',sw:'西南',w:'西',nw:'西北'}[value]||' variable')
</script>

<template>
 <section class="weather-card" aria-label="和风天气行程提醒">
  <header><span>开封 · 天气小笺</span><small>和风天气</small></header>
  <p v-if="report.current" class="weather-now"><strong>{{report.current.temperature_c}}°</strong><span>{{report.current.condition}}</span><small>体感 {{report.current.feels_like_c}}° · {{wind(report.current.wind_direction)}}风 {{report.current.wind_speed_mps}} m/s</small></p>
  <div v-if="report.impacts.length" class="weather-impacts">
   <article v-for="impact in report.impacts.slice(0,3)" :key="impact.activity_id">
    <p><strong>{{impact.name}}</strong><span>{{day(impact.starts_at)}} {{clock(impact.starts_at)}}—{{clock(impact.departs_at)}}</span></p>
    <p>{{impact.conditions.join('、')}} · 降水概率最高 {{impact.max_precipitation_probability_percent}}% · 预报降水约 {{impact.precipitation_mm}} mm</p>
    <small v-if="impact.basis==='daily_period_forecast'">白天/晚间时段预报，不能推断具体小时。</small>
   </article>
  </div>
  <div v-else class="weather-forecast">
   <p v-for="row in report.hourly.hours.slice(0,3)" :key="row.starts_at"><span>{{clock(row.starts_at)}} {{row.condition}}</span><strong>{{row.temperature_c}}° · {{row.precipitation_probability_percent}}%</strong></p>
  </div>
  <p v-if="report.coverage_unknown.length" class="weather-unknown">有 {{report.coverage_unknown.length}} 项行程超出现有预报覆盖，天气影响未知。</p>
  <p class="weather-boundary">{{report.location_name}}参考预报，不代表景区停演、室内适配或实际到场保证。</p>
  <button v-if="report.adjustment_offered" type="button" class="button" :disabled="busy" @click="emit('adjust')">重新核算行程</button>
  <footer><span>天气数据来自 QWeather / 和风天气</span><a v-for="url in report.attributions" :key="url" :href="url" target="_blank" rel="noopener noreferrer">来源与署名</a></footer>
 </section>
</template>

<style scoped>
.weather-card{margin:18px 0;padding:16px;border:1px solid #b8c9c7;border-radius:3px 16px;background:linear-gradient(135deg,#f4f8f4,#f9f5e9);color:#304e43;overflow-wrap:anywhere}.weather-card header{display:flex;align-items:center;justify-content:space-between;gap:8px;font:14px "Songti SC",serif}.weather-card header small,.weather-card footer{font-size:11px;color:#78897c}.weather-now{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:16px 0}.weather-now strong{font:36px "Songti SC",serif}.weather-now small{width:100%;font-size:12px}.weather-impacts article{padding:10px 0;border-top:1px solid #d4dfd4}.weather-impacts p{margin:5px 0;font-size:13px;line-height:1.7}.weather-impacts p:first-child{display:flex;justify-content:space-between;gap:10px}.weather-impacts p span{font-size:12px}.weather-impacts small,.weather-unknown{font-size:12px;color:#8b6847}.weather-forecast p{display:flex;justify-content:space-between;gap:12px;margin:8px 0;font-size:13px}.weather-forecast strong{font-weight:500}.weather-boundary{font-size:12px;line-height:1.8;color:#6d7d70}.weather-card button{min-height:44px;padding:10px 18px;border-radius:6px;background:#315f4d;color:#fff9ec}.weather-card footer{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;margin-top:14px}.weather-card footer a{text-decoration:underline;text-underline-offset:3px;min-height:32px;align-content:center}@media(max-width:500px){.weather-impacts p:first-child{display:block}.weather-impacts p span{display:block;margin-top:4px}}
</style>
