import type {Feature, Network, Interest} from '../types'

export type GuidePerformanceStatus = 'upcoming'|'live'|'ended'|'cancelled'
export interface GuidePerformance { id:string; scenicId:string; nodeId:string; name:string; venue:string; startsAt:string; endsAt:string; sourceType:'mock'|'reported'; sourceLabel:string; isSimulated:boolean; status:GuidePerformanceStatus }
export interface GuideCrowd {nodeId:string;level:'comfortable'|'busy'|'crowded'|'unknown';sourceType:'mock'|'reported'|'prediction';updatedAt:string;isPrediction:boolean}
export const scenicNames:Record<string,string>={wansui:'万岁山大宋武侠城',qingming:'清明上河园'}
const nodeNames:Record<string,Record<string,string>>={wansui:{gate:'示意入口',plaza:'江湖广场',theatre:'武侠剧场',photo:'古风照相点',culture:'民俗体验区',food:'小吃休憩点',garden:'林荫休憩点',exit:'示意出口'},qingming:{gate:'示意入口',plaza:'虹桥水岸',theatre:'景龙湖演出区',photo:'上善门',culture:'勾栏瓦肆',food:'小吃休憩点',garden:'宋园花径',exit:'示意出口'}}
const makePoint=(id:string,name:string,x:number,y:number,interests:Interest[]=[],stay=0):Feature=>({type:'Feature',geometry:{type:'Point',coordinates:[x,y]},properties:{id,name,interests,stay_minutes:stay,kind:id==='gate'||id==='exit'?'entrance':'poi',is_simulated:true,position_status:'schematic'}})
const makeEdge=(from:string,to:string,distance_m:number,positions:Record<string,[number,number]>):Feature=>({type:'Feature',geometry:{type:'LineString',coordinates:[positions[from],positions[to]]},properties:{id:`${from}-${to}`,from,to,distance_m,is_simulated:true}})
export function fallbackNetwork(scenicId:string):Network{
 const n=nodeNames[scenicId]||nodeNames.wansui
 const specs:[string,string,number,number,Interest[],number][]=[['gate',n.gate,80,420,[],0],['plaza',n.plaza,250,320,['culture','photo'],15],['theatre',n.theatre,420,170,['show'],35],['photo',n.photo,620,130,['photo'],20],['culture',n.culture,610,320,['culture'],30],['food',n.food,420,460,['food'],25],['garden',n.garden,230,130,['photo','culture'],15],['exit',n.exit,750,430,[],0]]
 const positions:Record<string,[number,number]>=Object.fromEntries(specs.map(s=>[s[0],[s[2],s[3]]]))
 const features:Feature[]=specs.map(s=>makePoint(s[0],s[1],s[2],s[3],s[4],s[5]))
 const edges:[string,string,number][]=[['gate','plaza',200],['gate','garden',330],['plaza','theatre',230],['plaza','culture',370],['plaza','food',240],['garden','theatre',210],['theatre','photo',220],['photo','culture',210],['culture','exit',200],['food','exit',360],['food','culture',250],['garden','photo',450]]
 features.push(...edges.map(e=>makeEdge(e[0],e[1],e[2],positions)))
 return {type:'FeatureCollection',is_simulated:true,notice:'演示路线，非实际导航；示意位置与距离待地理配准。',features}
}
function currentRelative(offsetMinutes:number){return new Date(Date.now()+offsetMinutes*60_000)}
function statusOf(start:Date,end:Date,now=Date.now()):GuidePerformanceStatus{return now>=end.getTime()?'ended':now>=start.getTime()?'live':'upcoming'}
function makePerformance(scenicId:string,id:string,nodeId:string,name:string,venue:string,offset:number,duration:number):GuidePerformance{const start=currentRelative(offset),end=new Date(start.getTime()+duration*60_000);return {id,scenicId,nodeId,name,venue,startsAt:start.toISOString(),endsAt:end.toISOString(),sourceType:'mock',sourceLabel:'演示日程 · 非官方实时场次',isSimulated:true,status:statusOf(start,end)}}
export function guidePerformances(scenicId:string):GuidePerformance[]{
 const prefix=scenicId==='qingming'?'qm':'ws',n=nodeNames[scenicId]||nodeNames.wansui
 return [makePerformance(scenicId,`${prefix}-past`,'plaza',scenicId==='qingming'?'虹桥水岸小戏':'江湖快闪',n.plaza,-42,22),makePerformance(scenicId,`${prefix}-live`,'theatre',scenicId==='qingming'?'大宋·东京梦华':'武侠实景演艺',n.theatre,-7,28),makePerformance(scenicId,`${prefix}-next`,'culture',scenicId==='qingming'?'杨志卖刀':'王婆说媒',n.culture,24,30),makePerformance(scenicId,`${prefix}-later`,'theatre',scenicId==='qingming'?'民俗巡游':'梁山好汉巡游',n.theatre,70,26)]
}
export function refreshPerformance(performance:GuidePerformance):GuidePerformance{return performance.status==='cancelled'?performance:{...performance,status:statusOf(new Date(performance.startsAt),new Date(performance.endsAt))}}
export function guideCrowds(scenicId:string):GuideCrowd[]{
 const now=new Date().toISOString()
 return scenicId==='qingming'?[{nodeId:'plaza',level:'busy',sourceType:'mock',updatedAt:now,isPrediction:true},{nodeId:'theatre',level:'crowded',sourceType:'mock',updatedAt:now,isPrediction:true},{nodeId:'culture',level:'comfortable',sourceType:'mock',updatedAt:now,isPrediction:true},{nodeId:'food',level:'unknown',sourceType:'mock',updatedAt:now,isPrediction:false}]:[{nodeId:'plaza',level:'crowded',sourceType:'mock',updatedAt:now,isPrediction:true},{nodeId:'theatre',level:'busy',sourceType:'mock',updatedAt:now,isPrediction:true},{nodeId:'culture',level:'comfortable',sourceType:'mock',updatedAt:now,isPrediction:true},{nodeId:'food',level:'unknown',sourceType:'mock',updatedAt:now,isPrediction:false}]
}
export function performanceCountdown(performance:GuidePerformance,now=Date.now()):string{const diff=Math.max(0,Math.round((new Date(performance.startsAt).getTime()-now)/1000));if(performance.status==='live')return '正在演出';if(performance.status==='ended')return '已结束';const m=Math.floor(diff/60),s=diff%60;return m>0?`距开演 ${m} 分 ${String(s).padStart(2,'0')} 秒`:`距开演 ${s} 秒`}
export function performanceProgress(performance:GuidePerformance,now=Date.now()):number{const start=new Date(performance.startsAt).getTime(),end=new Date(performance.endsAt).getTime();return Math.max(0,Math.min(1,(now-start)/(end-start)))}
