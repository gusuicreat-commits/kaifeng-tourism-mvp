import {reactive} from 'vue'
import {request} from './api'

// 景区页节目名 → 资料库节目编号。匹配不到时不显示加入按钮。
const matches=reactive<Record<string,Record<string,string|null>>>({})

export function programIdFor(scenicId:string,name:string){return matches[scenicId]?.[name]??null}

export async function matchPrograms(scenicId:string,names:string[]){
 const known=matches[scenicId]||{}
 const missing=[...new Set(names.filter(Boolean))].filter(name=>!(name in known))
 if(!missing.length)return
 const query=missing.map(name=>'name='+encodeURIComponent(name)).join('&')
 try{
  const result=await request<{matches:Record<string,string|null>}>(`/city/v1/scenic/${encodeURIComponent(scenicId)}/programs/match?${query}`)
  matches[scenicId]={...(matches[scenicId]||{}),...result.matches}
 }catch{/* 匹配失败只是不显示按钮 */}
}
