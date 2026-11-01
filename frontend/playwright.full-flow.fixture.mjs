import {mkdirSync,readFileSync,writeFileSync} from 'node:fs'
import {fileURLToPath} from 'node:url'

export function datedFacts(now=new Date()){
 const tomorrow=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(now.getTime()+86400000))
 return readFileSync(new URL('../backend/tests/fixtures/city_facts.json',import.meta.url),'utf8').replaceAll('2026-10-21',tomorrow)
}
export const scenic={id:'kaifengfu',name:'开封府',fact_id:'test:park'}
export const expensiveScenic={id:'tieta',name:'铁塔景区',fact_id:'zz-expensive-park'}
export const food={id:'chaoliangfen',name:'炒凉粉',fact_id:'test:food',place_id:'test:restaurant'}
export const morning={id:'full-flow:morning',name:'[合成测试]上午展馆',fact_id:'full-flow:morning'}
export const endpoints={origin_place_id:'test:gate',destination_place_id:'test:hotel'}
export const spicy={id:'full-flow:hot-vendor',name:'[合成测试]偏辣美食馆',place_id:'full-flow:hot-place'}
export const bland={id:'full-flow:bland-vendor',name:'[合成测试]清淡美食馆',place_id:'full-flow:bland-place'}
export const reference={id:'full-flow:reference',name:'[合成测试]来源展馆'}
export function fullFlowFixtures(now=new Date()){
 const facts=JSON.parse(datedFacts(now)),records=facts.records
 const rule=records.find(r=>r.kind==='open_rule')
 records.push({...rule,fact_id:'full-flow:open-park',place_id:scenic.fact_id,reentry:'allowed',last_entry_at:rule.closes_at})
 const park=records.find(r=>r.kind==='place'&&r.fact_id==='test:park')
 const gate=records.find(r=>r.kind==='place'&&r.fact_id==='test:gate')
 const hotel=records.find(r=>r.kind==='place'&&r.fact_id==='test:hotel')
 const entrance=records.find(r=>r.kind==='entrance'&&r.place_id==='test:park')
 records.push({...park,fact_id:expensiveScenic.fact_id,name:expensiveScenic.name,longitude:114.306})
 records.push({...gate,fact_id:'zz-expensive-gate',parent_place_id:expensiveScenic.fact_id,longitude:114.306})
 records.push({...hotel,fact_id:'pl_mvp_kaifengfu_meeting',name:'开封府府门广场集合点'})
 records.push({...entrance,fact_id:'zz-expensive-entry',place_id:expensiveScenic.fact_id,entrance_place_id:'zz-expensive-gate'})
 records.push({...rule,fact_id:'full-flow:open-expensive-park',place_id:expensiveScenic.fact_id,reentry:'allowed',last_entry_at:rule.closes_at})
 records.push({...records.find(r=>r.kind==='place'&&r.fact_id==='test:restaurant'),fact_id:morning.fact_id,name:morning.name,place_type:'poi'})
 records.push({...rule,fact_id:'full-flow:open-morning',place_id:morning.fact_id,opens_at:rule.business_date+'T08:00:00+08:00',closes_at:rule.business_date+'T12:00:00+08:00'})
 for(const [item,spiciness] of [[spicy,'hot'],[bland,'none']]){
  records.push({...records.find(r=>r.kind==='place'&&r.fact_id==='test:restaurant'),fact_id:item.place_id,name:item.name})
  records.push({...records.find(r=>r.kind==='vendor'),fact_id:item.id,place_id:item.place_id,spiciness})
  records.push({...rule,fact_id:'full-flow:open-'+spiciness,place_id:item.place_id})
 }
 const referenceMeta={validation_status:'unverified',verified_at:null,reviewer:null,verification_method:null,rights_status:'internal_only',planning_status:'reference',planning_note:'SYNTHETIC TEST ONLY - reference admission, not real verified data'}
 records.push({...records.find(r=>r.kind==='place'&&r.fact_id==='test:restaurant'),...referenceMeta,fact_id:reference.id,name:reference.name,place_type:'poi'})
 records.push({...rule,...referenceMeta,fact_id:'full-flow:open-reference',place_id:reference.id})
 const dated=records.filter(r=>r.business_date),first=rule.business_date
 for(let offset=1;offset<3;offset++){
  const date=new Date(first+'T12:00:00+08:00');date.setUTCDate(date.getUTCDate()+offset)
  const next=date.toISOString().slice(0,10)
  for(const record of dated)records.push({...JSON.parse(JSON.stringify(record).replaceAll(first,next)),fact_id:record.fact_id+':day'+(offset+1)})
 }
 // The progress test uses the real server clock and must record an already elapsed time.
 const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(now)
 for(const record of dated.filter(r=>r.kind==='open_rule'&&[scenic.fact_id,spicy.place_id,bland.place_id].includes(r.place_id)))records.push({...JSON.parse(JSON.stringify(record).replaceAll(first,today)),fact_id:record.fact_id+':today'})
 const bindings=JSON.parse(readFileSync(new URL('../backend/tests/fixtures/object_bindings.json',import.meta.url),'utf8'))
 bindings.push(['scenic',scenic.id,'place',scenic.fact_id])
 bindings.push(['scenic',expensiveScenic.id,'place',expensiveScenic.fact_id])
 return {facts,bindings,operations:journeys(facts)}
}
export function journeys(facts){
 const places=facts.records.filter(r=>r.kind==='place'&&Number.isFinite(r.longitude)&&Number.isFinite(r.latitude))
 const source=facts.records.find(r=>r.kind==='source')
 const until=facts.records.filter(r=>r.kind==='open_rule').map(r=>r.closes_at).sort().at(-1)
 const evidence={evidence_id:'full-flow:synthetic',revision:'1',kind:'estimate',source:'synthetic full-flow coordinate estimate; NOT real transport evidence',checked_at:source.collected_at,valid_from:source.effective_from,valid_until:until}
 const ticketEvidence={evidence_id:'full-flow:ticket-price',revision:'1',kind:'estimate',source:'synthetic full-flow ticket price; NOT real ticket evidence',checked_at:source.collected_at,valid_from:source.effective_from,valid_until:until}
 const radians=n=>n*Math.PI/180
 const parks=place=>{
  const ids=[]
  for(let p=place;p;p=places.find(r=>r.fact_id===p.parent_place_id))if(p.place_type==='scenic')ids.push(p.fact_id)
  return ids
 }
 const output=[]
 for(const origin of places)for(const destination of places){
  const dlat=radians(destination.latitude-origin.latitude),dlon=radians(destination.longitude-origin.longitude)
  const h=Math.sin(dlat/2)**2+Math.cos(radians(origin.latitude))*Math.cos(radians(destination.latitude))*Math.sin(dlon/2)**2
  const distance=6371000*2*Math.asin(Math.sqrt(Math.min(1,h)))*1.3
  const from=parks(origin),to=parks(destination),boundaries=[]
  for(const [ids,other,direction] of [[from,to,'exit'],[to,from,'entry']])for(const park of ids.filter(id=>!other.includes(id))){
   const gate=facts.records.find(r=>r.kind==='entrance'&&r.place_id===park&&[direction,'both'].includes(r.direction))
   if(!gate)throw new Error('Synthetic boundary needs an entrance: '+park)
   boundaries.push({park_id:park,gate_place_id:gate.entrance_place_id,direction,seconds:0,evidence})
  }
  for(const [mode,speed] of [['walking',1.2],['transit',5],['driving',8]]){
   const seconds=Math.ceil(distance/speed),id=`full-flow:${origin.fact_id}:${destination.fact_id}:${mode}`
   // HTTP planning admits estimate envelopes; the source explicitly identifies the synthetic estimator.
   output.push({journey_id:id,travel:{origin_place_id:origin.fact_id,destination_place_id:destination.fact_id,mode,lower_seconds:seconds,upper_seconds:seconds,evidence,path_status:'estimated',path_ref:id,coordinate_system:origin.coordinate_system},distance_m:distance,source_ids:[source.fact_id],time_basis:'provider_estimate',estimated_seconds:seconds,quote_expires_at:until,boundaries})
  }
 }
 return {schema_version:'city-operations/1',journeys:output,costs:[{place_id:expensiveScenic.fact_id,lower_fen:30000,upper_fen:30000,unit:'per_person',evidence:ticketEvidence}]}
}
export function writeFacts(){
 const directory=new URL('../.test-results/full-flow/',import.meta.url)
 mkdirSync(directory,{recursive:true})
 const target=new URL('city_facts.json',directory)
 const {facts,bindings,operations}=fullFlowFixtures()
 for(const [name,value] of [['city_facts.json',facts],['object_bindings.json',bindings],['journeys.json',operations]])writeFileSync(new URL(name,directory),JSON.stringify(value))
 return fileURLToPath(target)
}
if(process.argv[1]===fileURLToPath(import.meta.url))writeFacts()
