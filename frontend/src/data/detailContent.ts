import type {ObjectKind} from '../basketApi'
export interface MediaAsset{
 id:string;objectKind:ObjectKind;objectId:string;kind:'illustration'|'photo'|'video';src:string;poster?:string
 source:string|null;author:string|null;rights:'provided'|'permitted'|'pending';reviewed:boolean;caption:string;alt:string;order:number;updatedAt:string|null
}
// Curated manifest only. Real photos/video remain empty until assets and usage rights are supplied.
const illustration=(objectKind:ObjectKind,objectId:string):MediaAsset=>({id:objectKind+':'+objectId+':illustration',objectKind,objectId,kind:'illustration',src:'/images/'+(objectKind==='food'?'foods/':'scenics/')+objectId+'.jpg',source:'用户提供',author:null,rights:'provided',reviewed:false,caption:'主题插画',alt:'主题展示插画',order:0,updatedAt:null})
export const detailMedia:MediaAsset[]=[...['wansui','qingming','longting','tieta','kaifengfu'].map(id=>illustration('scenic',id)),...['xingrentea','huashenggao','chaoliangfen','xiaolongbao','taosibao','tongziji','yangroukangmo','liyubeimian','siweicai'].map(id=>illustration('food',id))]
export function mediaFor(kind:ObjectKind,id:string){return detailMedia.filter(m=>m.objectKind===kind&&m.objectId===id&&m.reviewed&&m.rights==='permitted').sort((a,b)=>a.order-b.order)}
export interface DetailEditorial{summary:string|null;highlights:{id:string;name:string;body:string|null;factId:string|null}[];story:string|null;duration:string|null;mealTime:string|null;taste:string|null}
export const emptyEditorial=():DetailEditorial=>({summary:null,highlights:[],story:null,duration:null,mealTime:null,taste:null})
// Add reviewed, sourced copy per stable business ID here. Null deliberately renders 待添加.
export const detailEditorial:Record<string,DetailEditorial>={}
