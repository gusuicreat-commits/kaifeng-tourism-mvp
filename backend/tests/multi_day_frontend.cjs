// Execute the actual TypeScript projections without a browser, network or build artifact.
const assert=require('node:assert/strict')
const fs=require('node:fs')
const path=require('node:path')
const vm=require('node:vm')
const {createRequire}=require('node:module')
const root=path.resolve(__dirname,'../..'),frontend=path.join(root,'frontend')
const frontendRequire=createRequire(path.join(frontend,'package.json'))
const ts=frontendRequire('typescript'),vue=frontendRequire('vue')
const moment=Date.parse('2030-05-02T10:00:00+08:00')
class ClockDate extends Date{constructor(...args){super(...(args.length?args:[moment]))}static now(){return moment}}
const storage=new Map(),calls=[]
let apiSnapshot=null
const api={ApiError:class extends Error{},clearPendingMutations(){},api:{memorySession:async()=>({})},
 request:async(url)=>{calls.push({url});return apiSnapshot},mutate:async(url,body)=>{calls.push({url,body});return body}}
const context=vm.createContext({Date:ClockDate,Intl,Math,JSON,Number,String,Boolean,Object,Array,Set,Map,Promise,URLSearchParams,
 setTimeout,clearTimeout,window:{addEventListener(){}},sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
 console,fetch:()=>{throw new Error('network forbidden')}})
const cache=new Map()
function load(file){
 file=path.resolve(file)
 if(cache.has(file))return cache.get(file).exports
 const module={exports:{}};cache.set(file,module)
 const source=fs.readFileSync(file,'utf8')
 const output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},fileName:file}).outputText
 const requireLocal=name=>name==='vue'?vue:name==='./api'?api:name.startsWith('.')?load(path.resolve(path.dirname(file),name.endsWith('.ts')?name:name+'.ts')):frontendRequire(name)
 const wrapper=vm.runInContext('(function(require,module,exports){'+output+'\n})',context,{filename:file})
 wrapper(requireLocal,module,module.exports);return module.exports
}
const city=load(path.join(frontend,'src/cityApi.ts')),exploration=load(path.join(frontend,'src/explorationApi.ts')),amap=load(path.join(frontend,'src/amap.ts'))
const clone=value=>JSON.parse(JSON.stringify(value))
const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value)}return value}
function data(){
 const dates=['2030-05-01','2030-05-02','2030-05-03']
 const stop=(id,place)=>({stop_id:id,place_id:place,kind:'visit',session_id:null,locked:true,duration_seconds:600,progress:'pending',actual_start:null,actual_end:null})
 const activity=(id,place,date)=>({goal_id:id,place_id:place,kind:'visit',session_id:null,required:true,starts_at:date+'T10:30:00+08:00',departs_at:date+'T10:40:00+08:00',duration_seconds:600})
 const leg=(date,from,to)=>({departs_at:date+'T10:00:00+08:00',arrives_at:date+'T10:20:00+08:00',journey:{travel:{origin_place_id:from,destination_place_id:to,mode:'walking'}}})
 const days=dates.map((date,index)=>({day_id:'day:'+date,business_date:date,status:index===0?'closed':index===1?'active':'pending',starts_at:date+'T09:00:00+08:00',ends_at:date+'T20:00:00+08:00',origin_place_id:index===0?'origin':'hotel',destination_place_id:'hotel',interests:[],dish_interest_ids:[],modes:['walking'],stops:[stop('goal:'+date,index===2?'future':'museum')]}))
 const overnights=dates.slice(0,-1).map((date,index)=>({from_day_id:'day:'+date,to_day_id:'day:'+dates[index+1],place_id:'hotel',place_evidence:'synthetic',stay_evidence:'unknown'}))
 const planDays=days.map(d=>({day_id:d.day_id,business_date:d.business_date,status:d.status,original_requirements:{starts_at:d.starts_at,ends_at:d.ends_at,origin_place_id:d.origin_place_id,destination_place_id:d.destination_place_id},itinerary:{objectives:['balanced'],activities:d.stops.map(s=>activity(s.stop_id,s.place_id,d.business_date)),legs:[leg(d.business_date,d.origin_place_id,d.stops[0].place_id)],total_seconds:3600,travel_seconds:1200,walking_distance_m:1000,known_cost_upper_fen:1000,cost_unknown:true,returns_at:d.business_date+'T18:00:00+08:00',evidence_mode:'synthetic'}}))
 const draft={schema_version:'city-draft/2',start_date:dates[0],end_date:dates[2],days,overnights}
 const trip={id:'trip-synthetic',schema_version:'city-state/2',status:'ACTIVE',draft,anchor:null,version:4,route_version:2,updated_at:'2030-05-02T10:00:00+08:00',readiness:{missing_fields:[],issues:[],notice:''}}
 const itinerary={schema_version:'city-itinerary/2',start_date:dates[0],end_date:dates[2],days:planDays,overnights,objectives:['balanced'],active_seconds:10800,elapsed_seconds:205200,travel_seconds:3600,walking_distance_m:3000,known_cost_upper_fen:3000,cost_unknown:true,evidence_mode:'synthetic'}
 const snapshot={trip_id:trip.id,version:4,route_version:2,itinerary,stale:false,days:days.map((d,i)=>({day_id:d.day_id,status:d.status,frozen:i===0,locked:i===2}))}
 const catalog=['origin','hotel','museum','future'].map((id,index)=>({record:{kind:'place',fact_id:id,name:id,longitude:114.3+index/100,latitude:34.8,coordinate_system:'GCJ02',place_type:'poi',is_demo:false,validation_status:'verified',business_status:'active',source_ids:['synthetic-source'],effective_to:'2035-01-01T00:00:00+08:00'},ready:true,issues:[]}))
 return {trip,snapshot,catalog}
}
const tests={
 async adapters(){
  const fixture=data(),before=JSON.stringify(fixture);freeze(fixture)
  const single={...city.emptyCityDraft(),business_date:'2030-05-02',starts_at:'2030-05-02T09:00:00+08:00',ends_at:'2030-05-02T20:00:00+08:00'}
  const legacyBefore=JSON.stringify(single);freeze(single)
  const oldDays=city.draftDays(single)
  assert.equal(oldDays.length,1);assert.equal(oldDays[0].business_date,'2030-05-02')
  assert.equal(city.draftDays(fixture.trip.draft).length,3)
  assert.equal(city.draftDays(fixture.trip.draft)[0].is_overnight,true)
  const legacyPlan=fixture.snapshot.itinerary.days[1].itinerary
  const oldRoute=exploration.routeDays({...fixture.snapshot,itinerary:legacyPlan},single)
  assert.equal(oldRoute.length,1);assert.equal(oldRoute[0].day_id,'day:2030-05-02')
  assert.equal(JSON.stringify(single),legacyBefore);assert.equal(JSON.stringify(fixture),before)
 },
 async route_days(){
  const fixture=data(),before=JSON.stringify(fixture);freeze(fixture)
  const days=exploration.routeDays(fixture.snapshot,fixture.trip.draft)
  assert.equal(days.length,3);assert.equal(days[1].activities[0].goal_id,'goal:2030-05-02')
  assert.equal(days[0].overnight.place_id,'hotel');assert.equal(days[2].overnight,undefined)
  assert.equal(days[0].frozen,true);assert.equal(days[2].locked,true)
  assert.equal(days.reduce((n,d)=>n+d.legs.length,0),3)
  for(const d of days)for(const leg of d.legs){assert.equal(leg.departs_at.slice(0,10),d.business_date);assert.equal(leg.arrives_at.slice(0,10),d.business_date)}
  assert.equal(JSON.stringify(fixture),before)
 },
 async map_today_and_future(){
  const {trip,snapshot,catalog}=data()
  let points=amap.mapPoints(catalog,trip,snapshot)
  assert.equal(points.find(p=>p.id==='museum').state,'下一站')
  assert.equal(points.find(p=>p.id==='future').state,'已有地点')
  points=amap.mapPoints(catalog,trip,snapshot,'day:2030-05-03')
  assert.equal(points.find(p=>p.id==='future').state,'已确认')
  assert.equal(points.some(p=>p.state==='下一站'||p.state==='正在游览'),false)
  const plan=snapshot.itinerary.days[1].itinerary,ongoing=plan.activities[0]
  plan.ongoing={stop_id:ongoing.goal_id,place_id:ongoing.place_id,original_activity:ongoing};plan.activities=[]
  points=amap.mapPoints(catalog,trip,snapshot,'day:2030-05-02')
  assert.equal(points.find(p=>p.id==='museum').state,'正在游览')
 },
 async map_versions(){
  const {trip,snapshot,catalog}=data()
  for(const mismatch of [{trip_id:'another-trip'},{version:3},{route_version:1},{stale:true}]){
   const points=amap.mapPoints(catalog,trip,{...snapshot,...mismatch},'day:2030-05-02')
   assert.equal(points.every(p=>p.state==='已有地点'),true)
  }
  const frozen=amap.mapPoints(catalog,trip,{...snapshot,stale:true},'day:2030-05-01')
  assert.equal(frozen.find(p=>p.id==='museum').state,'已确认')
 },
 async journey_focus(){
  const module=load(path.join(frontend,'src/journey.ts')),{trip,snapshot,catalog}=data()
  apiSnapshot=snapshot;module.journey.trip=trip;module.journey.catalog=catalog
  await vue.nextTick();await vue.nextTick();module.journey.route=snapshot;module.setDefaultDay()
  assert.equal(module.journey.focusedDayId,'day:2030-05-02')
  assert.equal(module.nextCityStop.value.stop_id,'goal:2030-05-02')
  module.focusDay('day:2030-05-03')
  assert.equal(module.nextCityStop.value,undefined);assert.equal(module.usableItinerary().activities[0].place_id,'future')
  module.focusDay('day:2030-05-01')
  assert.equal(module.usableItinerary().business_date,'2030-05-01')
  module.setDefaultDay();assert.equal(module.journey.focusedDayId,'day:2030-05-01')
  module.journey.focusedDayId='';module.setDefaultDay();assert.equal(module.journey.focusedDayId,'day:2030-05-02')
  module.journey.route={...snapshot,stale:true};module.focusDay('day:2030-05-01');assert.equal(module.usableItinerary().business_date,'2030-05-01')
  module.focusDay('day:2030-05-03');assert.equal(module.usableItinerary(),null)
 },
}
Promise.resolve(tests[process.argv[2]]?.()).then(()=>{assert.ok(tests[process.argv[2]],'known test case');console.log(process.argv[2]+' passed')}).catch(error=>{console.error(error);process.exitCode=1})
