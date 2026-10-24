// Visitor-facing selection, independent of the full planning/catalog data.
// Names are editorial choices, not popularity or schedule verification claims.
const featured:Record<string,readonly string[]>={
 wansui:['王婆说媒','打铁花','三打祝家庄','武松大闹飞云浦'],
 qingming:['大宋·东京保卫战','岳飞枪挑小梁王','岳飞·郾城大捷','打铁花之盛世大宋','铁花庆归航','包公迎宾'],
 kaifengfu:['开衙迎宾','包公断案（铡美案）','太子迎驾','包公巡衙之巧遇苏轼','包公断案（铡赵王）','一座府·天下首府'],
}
const normalize=(name:string)=>name.replace(/[·•\s]/g,'')
export const hasProgramTime=(text:string)=>/(?:[01]?\d|2[0-3]):[0-5]\d/.test(text)
export function isFeaturedProgram(parkId:string,name:string){
 const key=parkId==='qmsy'?'qingming':parkId
 return (featured[key]||[]).some(value=>normalize(value)===normalize(name))
}
