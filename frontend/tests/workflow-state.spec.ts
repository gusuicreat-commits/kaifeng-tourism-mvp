import {test,expect} from '@playwright/test'
import {applyIntent,basicQuestion,changeSite,complete,createInterview,fromPlan,nextStep,restoreInterview,toPlan} from '../src/planningWorkflow'

test('必填需求不猜默认值，缺日期和时长不能进入计算',()=>{
 const s=createInterview()
 expect(s.draft.available_minutes).toBeNull();expect(s.draft.visit_time).toBe('')
 changeSite(s,'wansui');s.draft.visit_time='14:15'
 expect(basicQuestion(s.draft,'万岁山武侠城')).not.toContain('几点入园')
 expect(basicQuestion(s.draft,'万岁山武侠城')).toContain('哪天游玩、准备玩多久')
 expect(complete(s,'basics')).toContain('日期');expect(()=>toPlan(s)).toThrow()
 s.draft.visit_date='2026-02-30';expect(complete(s,'basics')).toContain('日期')
 s.draft.visit_date='2026-11-01';s.draft.available_minutes=120
 expect(complete(s,'basics')).toBe('');expect(nextStep(s)).toBe('interests')
})

test('明确完成或跳过可选问题才可确认，游客接口始终读取非模拟数据',()=>{
 const s=createInterview();changeSite(s,'wansui')
 applyIntent(s,{visit_start:'2026-11-01T06:15:00Z',available_minutes:120})
 expect(s.draft.visit_time).toBe('14:15');expect(nextStep(s)).toBe('interests')
 complete(s,'interests');expect(()=>toPlan(s)).toThrow()
 complete(s,'pace');const p=toPlan(s)
 expect(p.mode).toBe('preference');expect(p.walking).toBe('normal')
 expect(p.must_visit).toEqual([]);expect(p.must_attend).toEqual([])
 expect(p.dataset).toBe('reported');expect(p.crowd_overrides).toEqual({})
})

test('自然语言只补充明确字段，日期变化清除原场次并要求重新核对',()=>{
 const s=createInterview();changeSite(s,'wansui')
 applyIntent(s,{available_minutes:90,mode:'quiet',must_visit:['theatre']})
 expect(nextStep(s)).toBe('basics');expect(s.draft.visit_date).toBe('')
 applyIntent(s,{visit_start:'2026-11-01T14:00:00+08:00'})
 expect(nextStep(s)).toBe('review');s.draft.must_attend=['old-session']
 applyIntent(s,{visit_start:'2026-11-02T14:00:00+08:00'})
 expect(s.draft.must_attend).toEqual([]);expect(nextStep(s)).toBe('interests')
 expect(s.draft.must_visit).toEqual(['theatre']);expect(s.draft.mode).toBe('quiet')
})

test('换景区使原地点和场次失效，时间及可复用偏好保留',()=>{
 const s=fromPlan({scenic_id:'wansui',visit_start:'2026-11-01T14:15:00+08:00',available_minutes:120,must_visit:['photo'],must_attend:['a'],interests:['photo'],walking:'relaxed',mode:'shortest',hour:14,dataset:'demo',avoid_crowds:false,crowd_overrides:{photo:0}})
 changeSite(s,'qingming')
 expect(nextStep(s)).toBe('basics');expect(s.answered.interests).toBe(false)
 expect(s.draft.must_visit).toEqual([]);expect(s.draft.must_attend).toEqual([])
 expect(s.draft.visit_time).toBe('14:15');expect(s.draft.interests).toEqual(['photo'])
 expect(s.draft.walking).toBe('relaxed')
})

test('损坏或伪造会话不能绕过必填校验，演示参数不进入新请求',()=>{
 expect(nextStep(restoreInterview('invalid-json'))).toBe('basics')
 const s=restoreInterview(JSON.stringify({version:1,state:{draft:{scenic_id:'wansui',available_minutes:-1,interests:['photo','fake'],mode:'fake',walking:'fake',must_visit:['photo','photo',{},'x'.repeat(200)],dataset:'demo',crowd_overrides:{photo:0}},answered:{basics:true,interests:true,pace:true}}}))
 expect(nextStep(s)).toBe('basics');expect(()=>toPlan(s)).toThrow()
 expect(s.draft.interests).toEqual(['photo']);expect(s.draft.must_visit).toEqual(['photo'])
 expect(s.draft.mode).toBeNull();expect(s.draft.walking).toBeNull()
})
