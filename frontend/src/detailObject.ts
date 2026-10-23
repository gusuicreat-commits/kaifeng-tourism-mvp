import {ref,watch} from 'vue'
import {request} from './api'
import type {ObjectKind,BasketChoice} from './basketApi'
export function useDetailObject(kind:ObjectKind,id:()=>string){
 const object=ref<BasketChoice|null>(null),error=ref('');let sequence=0
 watch(id,async value=>{const version=++sequence;object.value=null;error.value='';try{const r=await request<BasketChoice>('/city/v1/objects/'+kind+'/'+encodeURIComponent(value));if(sequence===version)object.value=r}catch(e){if(sequence===version)error.value=(e as Error).message}},{immediate:true})
 return {object,error}
}
