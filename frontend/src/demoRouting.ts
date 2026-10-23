import type {Network} from './types'
// Only used by the explicitly marked schematic park-map interaction demo.
export function demoRoute(network:Network,start:string,end:string){
 const lines=network.features.filter(f=>f.geometry.type==='LineString')
 const edges=lines.map(f=>({f,from:String(f.properties.from),to:String(f.properties.to),distance:Number(f.properties.distance_m)})).filter(e=>Number.isFinite(e.distance)&&e.distance>0)
 const distances=new Map<string,number>([[start,0]]),previous=new Map<string,{id:string;edge:typeof edges[number]}>(),open=new Set([start])
 while(open.size){const id=[...open].sort((a,b)=>distances.get(a)!-distances.get(b)!)[0];open.delete(id);if(id===end)break
  for(const edge of edges){const reverse=edge.to===id&&edge.f.properties.one_way!==true&&edge.f.properties.directed!==true;const target=edge.from===id?edge.to:reverse?edge.from:null;if(!target)continue;const distance=distances.get(id)!+edge.distance;if(distance<(distances.get(target)??Infinity)){distances.set(target,distance);previous.set(target,{id,edge});open.add(target)}}
 }
 if(!distances.has(end))return null
 const path:typeof lines=[];let cursor=end;const seen=new Set<string>();while(cursor!==start){if(seen.has(cursor))return null;seen.add(cursor);const prev=previous.get(cursor);if(!prev)return null;path.unshift(prev.edge.f);cursor=prev.id}
 const distance=distances.get(end)!;return {network:{...network,features:path},distance,minutes:Math.ceil(distance/70)}
}
