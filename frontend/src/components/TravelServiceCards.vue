<script setup lang="ts">
import {ref,watch,onMounted} from 'vue'
import {request,mutate} from '../api'
import {yuanToFen,yuan} from '../money'
import {memoryEpoch} from '../voiceDraft'
interface Rate{room_id:string;rate_id:string;currency:string;quote?:{total_fen:number;price_unit?:string}|null}
interface Item {rate_plans?:Rate[];room_types?:{room_id:string;name:string}[];price_selection?:{amount_fen:number;unit:string;rooms:number;basis:string};id:string;name:string;address?:string;image?:string;detail_url?:string;price_display?:string;checked_at:string;expires_at:string;unverified_conditions?:string[];condition_evidence?:{field:string;label:string;strength:string;status:string}[];attributes?:Record<string,{label:string;status:string}>}
interface Workspace {version:number;requirements:Record<string,unknown>;result:{status:string;items:Item[];system_message?:string;questions?:string[];provider_id?:string;capability?:string;provider_trace?:{provider_id:string;provider_status:string;attempted:boolean;fallback_reason?:string|null}[]}|null;selection:Record<string,Item>|null;previous:unknown}
interface Preview {preview_id:string;before:unknown;after:Item;expected_version:number;blockers:string[];affected:string[]}
const props=defineProps<{tripId:string;revision?:unknown}>()
const w=ref<Workspace|null>(null),preview=ref<Preview|null>(null),busy=ref(false),error=ref(''),notice=ref('')
const prices=ref<Record<string,{mode:string;rate:string;amount:string;unit:string;rooms:number}>>({})
const priceForm=(item:Item)=>prices.value[item.id]||(prices.value[item.id]={mode:'none',rate:'',amount:'',unit:'per_room_night',rooms:1})
const unitLabel=(unit:string)=>({per_room_night:'每间每晚',per_room_stay:'每间整段入住',party_stay:'全部房间整段入住'}[unit]||'单位待核对')
let generation=0
const path=()=>`/city/v1/trips/${encodeURIComponent(props.tripId)}/services`
async function load(){const g=++generation,id=props.tripId,e=memoryEpoch.value;try{const value=await request<Workspace>(path());if(g===generation&&id===props.tripId&&e===memoryEpoch.value){w.value=value;preview.value=null;prices.value={}}}catch{/* Chat remains available when the service workspace cannot load. */}}
async function choose(item:Item){if(!w.value||busy.value)return;busy.value=true;error.value='';const g=generation
 try{const f=priceForm(item);let price={};if(f.mode==='manual'){const amount=yuanToFen(f.amount);if(amount===null)throw new Error('请填写已核对的住宿价格');price={manual_price:{amount_fen:amount,unit:f.unit,rooms:f.rooms}}}else if(f.mode==='quote'){const r=item.rate_plans?.[Number(f.rate)];if(f.rate===''||!r)throw new Error('请选择房型价格计划');price={rate_id:r.rate_id,room_id:r.room_id,rooms:f.rooms}}const value=await mutate<Preview>(path()+'/preview',{expected_version:w.value.version,candidate_id:item.id,...price});if(g===generation)preview.value=value}
 catch(e){if(g===generation)error.value=(e as Error).message}finally{busy.value=false}}
async function confirm(){if(!w.value||!preview.value||busy.value)return;busy.value=true;error.value='';const g=generation
 try{const value=await mutate<{workspace:Workspace;reply:string}>(path()+'/confirm',{expected_version:preview.value.expected_version,preview_id:preview.value.preview_id,confirmed:true});if(g===generation){w.value=value.workspace;notice.value=value.reply;preview.value=null}}
 catch(e){if(g===generation)error.value=(e as Error).message}finally{busy.value=false}}
async function undo(){if(!w.value||busy.value)return;busy.value=true;const g=generation
 try{const value=await request<Workspace>(path()+'/undo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_version:w.value.version})});if(g===generation){w.value=value;preview.value=null}}
 catch(e){if(g===generation)error.value=(e as Error).message}finally{busy.value=false}}
watch(()=>[props.tripId,props.revision],()=>void load());watch(memoryEpoch,()=>{generation++;w.value=null;preview.value=null;notice.value='';error.value=''})
onMounted(()=>void load())
const time=(s:string)=>new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})
</script>
<template>
<section v-if="w?.result" class="travel-services" aria-label="住宿与周边候选">
 <h3>{{w.requirements.category==='hotels'?'住宿候选':w.requirements.category==='parking'?'停车候选':'餐饮商家候选'}}</h3>
 <p v-if="w.requirements.room_type||w.requirements.location_text">已记录：{{w.requirements.room_type||'房型不限'}}<span v-if="w.requirements.location_text"> · {{w.requirements.location_text}}附近</span>；匹配情况待核实。</p>
 <p v-if="w.requirements.nights">住宿 {{w.requirements.nights}} 晚<span v-if="w.requirements.stay_budget_fen"> · 住宿合计预算 {{Number(w.requirements.stay_budget_fen)/100}} 元</span></p>
 <p v-if="w.requirements.check_in">{{w.requirements.check_in}} → {{w.requirements.check_out||'退房日期待补充'}} · {{w.requirements.guests||'待补充'}}人 · {{w.requirements.rooms||'待补充'}}间</p>
 <p v-if="w.requirements.max_price">每晚预算上限 {{w.requirements.max_price}} 元；展示价不等于所选房型报价。</p>
 <p v-if="w.result.status!=='needs_clarification'">所选报价或手填价格可用于全程预算；展示起价仅供参考。</p>
 <p v-for="q in w.result.questions||[]" :key="q">{{q}}</p>
 <p v-if="w.selection&&Object.keys(w.selection).length">候选意向：{{Object.values(w.selection).map(i=>i.name).join('、')}}。正式路线待核验。</p>
 <button v-if="w.previous!==null" type="button" :disabled="busy" @click="undo">撤销／恢复上次候选选择</button>
 <div class="service-grid">
  <article v-for="item in w.result.items" :key="item.id" class="service-card">
   <img v-if="item.image" :src="item.image" :alt="item.name" loading="lazy" referrerpolicy="no-referrer"/>
   <h4>{{item.name}}</h4><p>{{item.address||'地址尚未核实'}}</p>
   <strong>{{item.price_display?item.price_display+' 起／晚':'费用未知'}}</strong>
   <p>查询：{{time(item.checked_at)}}</p>
   <p v-if="item.condition_evidence?.length">{{item.condition_evidence.map(c=>`${c.label}（${c.strength==='hard'?'必须':c.strength==='soft'?'偏好':'可选'}，待核实）`).join('、')}}</p>
   <p v-else-if="item.unverified_conditions?.length">所提条件尚未核实：{{item.unverified_conditions.join('、')}}</p>
   <p v-if="w.requirements.category==='hotels'">房型设施、入住人数、评价与取消政策尚未核实。</p>
   <p v-else>费用、营业状态、空位及菜品供应尚未核实。附近停车不代表酒店免费停车。</p>
   <div v-if="w.requirements.category==='hotels'" class="stay-price"><label>住宿计价方式<select v-model="priceForm(item).mode"><option value="none">仅选酒店，费用待补充</option><option v-if="item.rate_plans?.length" value="quote">选择房型价格计划</option><option value="manual">填写已核对的价格</option></select></label><label v-if="priceForm(item).mode==='quote'">房型价格计划<select v-model="priceForm(item).rate"><option value="">请选择</option><option v-for="(rate,index) in item.rate_plans||[]" :key="rate.room_id+':'+rate.rate_id" :value="String(index)" :disabled="!rate.quote?.price_unit||rate.currency!=='CNY'">{{item.room_types?.find(r=>r.room_id===rate.room_id)?.name||rate.room_id}} · {{rate.quote?yuan(rate.quote.total_fen):'无报价'}} · {{unitLabel(rate.quote?.price_unit||'')}}</option></select></label><template v-if="priceForm(item).mode==='manual'"><label>住宿价格（元）<input v-model="priceForm(item).amount" inputmode="decimal" placeholder="手填金额，不会当成实时报价"></label><label>价格单位<select v-model="priceForm(item).unit"><option value="per_room_night">每间每晚</option><option value="per_room_stay">每间整段入住</option><option value="party_stay">全部房间整段入住</option></select></label></template><label v-if="priceForm(item).mode!=='none'">房间数<input v-model.number="priceForm(item).rooms" type="number" min="1" max="20"></label></div>
   <button type="button" :disabled="busy||Date.now()>=Date.parse(item.expires_at)" @click="choose(item)">选择候选并预览影响</button>
   <a v-if="item.detail_url" :href="item.detail_url" target="_blank" rel="noopener noreferrer">查看飞猪详情／预订</a>
  </article>
 </div>
 <p v-if="!w.result.items.length&&w.result.status==='needs_clarification'">已保留查询条件，补齐上方缺项后继续查询。</p>
 <p v-else-if="!w.result.items.length">本次未取得候选，可稍后重试。</p>
 <section v-if="preview" class="service-preview" aria-label="候选意向预览">
  <h4>选择 {{preview.after.name}}</h4>
  <p v-if="preview.after.price_selection">住宿金额：{{yuan(preview.after.price_selection.amount_fen)}} · {{unitLabel(preview.after.price_selection.unit)}} · {{preview.after.price_selection.rooms}}间（{{preview.after.price_selection.basis==='reported'?'手填':'接口报价'}}）</p><p>受影响：{{preview.affected.join('、')}}</p>
  <p>需补齐：{{preview.blockers.join('；')}}</p>
  <p>本次确认只保存候选意向。当前正式行程和已完成活动不会改变。</p>
  <button type="button" :disabled="busy" @click="confirm">确认候选意向</button>
  <button type="button" :disabled="busy" @click="preview=null">放弃</button>
 </section>
 <p v-if="notice" role="status">{{notice}}</p><p v-if="error" role="alert">{{error}}</p>
 <p class="provider-note">{{w.result.system_message||'候选来自当前已启用的查询 Provider；价格与库存以第三方详情页为准。'}}<span v-if="w.result.provider_id"> 本轮来源：{{w.result.provider_id}}（{{w.result.capability}}）。</span></p>
</section>
</template>
<style scoped>
.travel-services{border:1px solid #d9cdb8;border-radius:4px 16px;margin-block:16px;padding:14px;color:#304e43;background:#fffcf6;overflow-wrap:anywhere}.travel-services h3,.travel-services h4{font-family:"Songti SC",serif}.travel-services p{font-size:13px;line-height:1.7}.service-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px}.service-card{min-width:0;border:1px solid #e4d8c4;border-radius:4px 12px;padding:12px}.service-card img{width:100%;height:130px;object-fit:cover;border-radius:6px}.service-card h4{margin:8px 0}.travel-services button,.travel-services a{display:block;min-height:44px;padding:10px 8px;box-sizing:border-box;color:#29483b}.travel-services button{border:1px solid #c9baa1;border-radius:5px;margin-block:8px}.service-preview{border-top:1px solid #c9baa1;margin-top:16px}.provider-note{color:#8e704c}
.stay-price{display:grid;gap:10px;margin-top:12px}.stay-price label{font-size:12px}.stay-price select,.stay-price input{display:block;width:100%;box-sizing:border-box;min-height:44px;margin-top:5px;background:#fffcf6;border:1px solid #c9baa1;border-radius:5px;padding:6px}
</style>
