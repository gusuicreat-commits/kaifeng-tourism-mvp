import type {ObjectKind} from '../basketApi'
import foodWorkbook from './foodWorkbook.generated.json'
import programWorkbook from './programWorkbook.generated.json'
import scenicMedia from './scenicMedia.generated.json'
import scenicVideoAssets from './scenicVideoAssets.generated.json'
export interface MediaAsset{
 id:string;objectKind:ObjectKind;objectId:string;kind:'illustration'|'photo'|'video';src:string;poster?:string
 source:string|null;author:string|null;rights:'provided'|'permitted'|'pending';reviewed:boolean;caption:string;alt:string;order:number;updatedAt:string|null
}
// Illustrations stay separate from the supplied detail photography.
const illustration=(objectKind:ObjectKind,objectId:string):MediaAsset=>({id:objectKind+':'+objectId+':illustration',objectKind,objectId,kind:'illustration',src:'/images/'+(objectKind==='food'?'foods/':'scenics/')+objectId+'.jpg',source:'用户提供',author:null,rights:'provided',reviewed:false,caption:'主题插画',alt:'主题展示插画',order:0,updatedAt:null})
export const detailMedia:MediaAsset[]=[...['wansui','qingming','longting','tieta','kaifengfu'].map(id=>illustration('scenic',id)),...['xingrentea','huashenggao','chaoliangfen','xiaolongbao','taosibao','tongziji','yangroukangmo','liyubeimian','siweicai'].map(id=>illustration('food',id))]
export function mediaFor(kind:ObjectKind,id:string){return detailMedia.filter(m=>m.objectKind===kind&&m.objectId===id&&m.reviewed&&m.rights==='permitted').sort((a,b)=>a.order-b.order)}
// User-supplied workbook photos are authorized for this local display. This is
// separate from the business-fact verification state used by the city engine.
const workbookMedia:MediaAsset[]=foodWorkbook.foods.flatMap(food=>food.photos.map((src,index)=>({
 id:'food:'+food.id+':workbook:'+index,objectKind:'food' as const,objectId:food.id,kind:'photo' as const,
 src,source:'团队资料整理',author:null,rights:'permitted' as const,reviewed:true,
 caption:food.vendors.find(v=>v.dishPhotos.includes(src))?.name||'美食实拍',alt:'美食实拍',order:index+1,updatedAt:'2026-10-24',
})))
// Explicit local-display sources; keep their original review/rights state separate
// from mediaFor(), which still requires permission for the curated public manifest.
const scenicWorkbookMedia:MediaAsset[]=scenicMedia.records.map(media=>({
 id:'scenic:'+media.placeId+':workbook:'+media.mediaId,objectKind:'scenic' as const,objectId:media.scenicId||media.placeId,kind:'photo' as const,
 src:media.url,source:media.source+' · 仅本地演示',author:media.author,rights:'provided' as const,reviewed:false,
 caption:media.title,alt:media.title,order:media.order,updatedAt:'2026-10-24',
}))
const programWorkbookMedia:MediaAsset[]=programWorkbook.records.flatMap(program=>program.photos.map((src,index)=>({
 id:'scenic:'+program.scenicId+':program:'+program.number+':'+index,objectKind:'scenic' as const,objectId:program.scenicId,kind:'photo' as const,
 src,source:'开封景区节目清单.xlsx · 仅本地演示',author:null,rights:'provided' as const,reviewed:false,
 caption:program.name,alt:program.name+'现场照片',order:100+program.number*10+index,updatedAt:'2026-10-24',
})))
export function photosFor(kind:ObjectKind,id:string){
 return [...mediaFor(kind,id).filter(m=>m.kind==='photo'),...workbookMedia.filter(m=>m.objectKind===kind&&m.objectId===id),...scenicWorkbookMedia.filter(m=>m.objectKind===kind&&m.objectId===id),...programWorkbookMedia.filter(m=>m.objectKind===kind&&m.objectId===id)]
}
const workbookVideos:MediaAsset[]=scenicVideoAssets.records.filter(video=>video.scenicId).map((video,index)=>({
 id:'scenic:'+video.scenicId+':workbook-video:'+video.mediaId,objectKind:'scenic' as const,objectId:video.scenicId!,kind:'video' as const,
 src:video.url,source:'工作簿媒体资产 · 待复核',author:video.publisher||null,rights:'pending' as const,reviewed:false,
 caption:video.title,alt:video.title,order:index+1,updatedAt:'2026-10-24',
}))
export function videosFor(kind:ObjectKind,id:string){
 return [...mediaFor(kind,id).filter(m=>m.kind==='video'),...workbookVideos.filter(m=>m.objectKind===kind&&m.objectId===id)]
}
export const foodProfiles=Object.fromEntries(foodWorkbook.foods.map(food=>[food.id,food]))
export type FoodProfile=(typeof foodWorkbook.foods)[number]
export const foodWorkbookStatus=foodWorkbook.status
export interface DetailEditorial{summary:string|null;highlights:{id:string;name:string;body:string|null;factId:string|null}[];story:string|null;duration:string|null;mealTime:string|null;taste:string|null}
export const emptyEditorial=():DetailEditorial=>({summary:null,highlights:[],story:null,duration:null,mealTime:null,taste:null})
// Add reviewed, sourced copy per stable business ID here. Null deliberately renders 待添加.
export const detailEditorial:Record<string,DetailEditorial>={}
