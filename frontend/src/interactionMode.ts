import {ref} from 'vue'

export type InteractionMode='deep'|'quick'
export const interactionModes=ref<Record<string,InteractionMode|null>>({})
const key=(tripId:string)=>'bianliang:interaction-mode:'+tripId
export function readMode(tripId:string):InteractionMode|null{
 try{const value=localStorage.getItem(key(tripId));return interactionModes.value[tripId]=value==='deep'||value==='quick'?value:null}catch{return null}
}
export function writeMode(tripId:string,mode:InteractionMode):InteractionMode|null{
 interactionModes.value[tripId]=mode
 try{localStorage.setItem(key(tripId),mode);return mode}catch{return null}
}
