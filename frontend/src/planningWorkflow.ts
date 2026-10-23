/** Deterministic interview. Models may extract fields; this module owns every transition. */
import type {Interest,PlanInput} from './types'
export type QuestionStep='basics'|'interests'|'pace'
export type Step=QuestionStep|'review'
export interface Draft {
 scenic_id:string;visit_date:string;visit_time:string;available_minutes:number|null;
 interests:Interest[];must_visit:string[];must_attend:string[];
 walking:PlanInput['walking']|null;mode:PlanInput['mode']|null
}
export interface Interview {draft:Draft;answered:Record<QuestionStep,boolean>}
export const questions:Record<Step,{title:string;question:string}>={
 basics:{title:'基本安排',question:'你好，我是小汴梁。你准备去哪个景区？哪天、几点开始，大概玩多久？'},
 interests:{title:'喜欢什么',question:'有什么一定想去的地方？你更喜欢演出、美食、拍照，还是文化体验？没有特别要求也可以跳过。'},
 pace:{title:'游玩节奏',question:'这次想少走路、尽量避拥挤，还是按兴趣安排？步行想轻松一些还是正常节奏？'},
 review:{title:'确认出发',question:'你的安排已经整理好了。核对需求卡后，我就按可用道路和日程计算路线。'}
}
const interests:Interest[]=['show','photo','food','culture']
const modes:PlanInput['mode'][]=['shortest','preference','quiet']
const walks:PlanInput['walking'][]=['relaxed','normal','brisk']
export function createInterview():Interview{return {draft:{scenic_id:'',visit_date:'',visit_time:'',available_minutes:null,interests:[],must_visit:[],must_attend:[],walking:null,mode:null},answered:{basics:false,interests:false,pace:false}}}
export function basicsError(d:Draft){
 if(!d.scenic_id)return '先选一个想去的景区。'
 if(!/^\d{4}-\d{2}-\d{2}$/.test(d.visit_date)||!Number.isFinite(Date.parse(d.visit_date+'T12:00:00+08:00'))||new Date(d.visit_date+'T12:00:00+08:00').toISOString().slice(0,10)!==d.visit_date)return '请选择具体的游玩日期。'
 if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(d.visit_time))return '请确定入园时刻，例如 14:30。'
 if(!Number.isInteger(d.available_minutes)||Number(d.available_minutes)<15||Number(d.available_minutes)>720)return '请填写15到720分钟的游玩时长。'
 return ''
}
export function basicQuestion(d:Draft,siteName?:string){
 const missing=[]
 if(!siteName)missing.push('去哪个景区')
 if(!d.visit_date)missing.push('哪天游玩')
 if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(d.visit_time))missing.push('几点入园')
 if(!Number.isInteger(d.available_minutes)||Number(d.available_minutes)<15||Number(d.available_minutes)>720)missing.push('准备玩多久')
 if(!missing.length)return '基本安排已经填好了，核对后继续；想调整的地方也可以直接修改。'
 return `${siteName?'已经选好'+siteName+'。':'你好，我是小汴梁。'}请告诉我：${missing.join('、')}？`
}
export function nextStep(s:Interview):Step{
 if(!s.answered.basics||basicsError(s.draft))return 'basics'
 if(!s.answered.interests)return 'interests'
 if(!s.answered.pace)return 'pace'
 return 'review'
}
export function changeSite(s:Interview,id:string){
 if(s.draft.scenic_id===id)return
 s.draft.scenic_id=id;s.draft.must_visit=[];s.draft.must_attend=[];s.answered.basics=false;s.answered.interests=false
}
export function complete(s:Interview,step:QuestionStep){
 if(step==='basics'&&basicsError(s.draft))return basicsError(s.draft)
 if(step==='pace'){s.draft.walking??='normal';s.draft.mode??='preference'}
 s.answered[step]=true
 return ''
}
export function toPlan(s:Interview):PlanInput{
 const error=basicsError(s.draft)
 if(error||nextStep(s)!=='review')throw new Error(error||'请先完成当前问题。')
 const d=s.draft
 return {scenic_id:d.scenic_id,visit_start:`${d.visit_date}T${d.visit_time}:00+08:00`,hour:Number(d.visit_time.slice(0,2)),
  available_minutes:d.available_minutes!,interests:[...d.interests],must_visit:[...d.must_visit],must_attend:[...d.must_attend],
  mode:d.mode||'preference',walking:d.walking||'normal',avoid_crowds:d.mode==='quiet',dataset:'reported',crowd_overrides:{}}
}
export function applyIntent(s:Interview,patch:Partial<PlanInput>){
 const d=s.draft
 if(patch.visit_start){
  // Server emits Asia/Shanghai; input remains explicit, never inferred by this workflow.
  const local=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(patch.visit_start))
  if(d.visit_date!==local.slice(0,10)&&d.must_attend.length){d.must_attend=[];s.answered.interests=false}
  d.visit_date=local.slice(0,10);d.visit_time=local.slice(11,16)
 }
 if(patch.available_minutes!==undefined)d.available_minutes=patch.available_minutes
 if(patch.interests){d.interests=patch.interests.filter(i=>interests.includes(i));s.answered.interests=true}
 if(patch.must_visit){d.must_visit=[...new Set(patch.must_visit)];s.answered.interests=true}
 if(patch.mode&&modes.includes(patch.mode))d.mode=patch.mode
 if(patch.walking&&walks.includes(patch.walking))d.walking=patch.walking
 if(patch.mode||patch.walking)s.answered.pace=true
 // Only advance when all required slots have actually been supplied.
 s.answered.basics=!basicsError(d)
}
export function fromPlan(p:PlanInput):Interview{
 const s=createInterview();s.draft.scenic_id=p.scenic_id;applyIntent(s,p)
 s.draft.must_attend=[...p.must_attend||[]];s.answered={basics:!basicsError(s.draft),interests:true,pace:true}
 return s
}
export function restoreInterview(raw:string|null):Interview{
 const s=createInterview()
 if(!raw)return s
 try{
  const stored=JSON.parse(raw)
  if(stored.version!==1||!stored.state?.draft)return s
  const d=stored.state.draft
  for(const k of ['scenic_id','visit_date','visit_time'] as const)if(typeof d[k]==='string')s.draft[k]=d[k].slice(0,120)
  if(Number.isInteger(d.available_minutes)&&d.available_minutes>=15&&d.available_minutes<=720)s.draft.available_minutes=d.available_minutes
  for(const k of ['must_visit','must_attend'] as const)if(Array.isArray(d[k]))s.draft[k]=[...new Set<string>(d[k].filter((v:unknown)=>typeof v==='string'&&v.length<=120))].slice(0,6)
  if(Array.isArray(d.interests))s.draft.interests=interests.filter(i=>d.interests.includes(i))
  if(walks.includes(d.walking))s.draft.walking=d.walking
  if(modes.includes(d.mode))s.draft.mode=d.mode
  for(const k of ['basics','interests','pace'] as const)s.answered[k]=stored.state.answered?.[k]===true
 }catch{/* Corrupt or obsolete drafts restart the interview, never bypass validation. */}
 return s
}
