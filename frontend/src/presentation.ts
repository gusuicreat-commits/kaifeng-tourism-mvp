/** Visitor-facing wording; source flags and identifiers remain unchanged in the API/database. */
export const placeName=(name:string|undefined)=>(name||'').replace(/^演示·/,'示意·').replace('（示意位置）','')
export const currentHour=()=>Number(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',hourCycle:'h23'}).format(new Date()))
export const shortTime=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false})
