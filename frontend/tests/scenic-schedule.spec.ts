import {test,expect} from '@playwright/test'

for(const state of [
 {status:'review_required',display:'today_unconfirmed',label:'今日尚未确认'},
 {status:'stale_source',display:'stale_reference_only',label:'旧日程仅供参考'},
 {status:'approved_schedule',display:'official_schedule_reviewed',label:'官方日程已核验'},
])test(`清明日程按查询日期区分${state.status}，保留分区与取消标记`,async({page})=>{
 let requestedDate=''
 await page.route('**/api/scenic/parks/qmsy/performances?*',route=>{
  requestedDate=new URL(route.request().url()).searchParams.get('date')||''
  return route.fulfill({json:{park_id:'qmsy',service_date:requestedDate,
   approved_schedule:state.status==='approved_schedule',status:state.status,display_status:state.display,
   latest_review_status:state.status,notice:'合成测试节目表；不等于现场保证演出。',
   source_updated_on:requestedDate,last_fetched_at:null,
   source:{source_ref:'https://example.invalid/synthetic-schedule',source_updated_on:requestedDate,fetched_at:''},
   performances:['东区','西区'].map((area,i)=>({performance_id:i+1,program_name:'大宋·东京保卫战',venue_name:'同名场地',area,
    start_time:'15:00:00',duration_minutes:null,end_time:null,entry_deadline:null,requires_booking:null,
    weather_sensitive:null,schedule_note:'',verified_poi_id:null,eligible_for_solver:false,
    source_freshness:state.status,operating_status:i?'cancelled':'scheduled'}))}})
 })
 await page.goto('/scenic/qingming')
 const section=page.locator('.scenic-schedule')
 await expect(section.locator('.schedule-notice strong')).toHaveText(state.label)
 expect(requestedDate).toBe(await page.evaluate(()=>new Intl.DateTimeFormat('en-CA',{
  timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())))
 await expect(section).toContainText(requestedDate)
 await expect(section.locator('.schedule-program')).toHaveCount(2)
 await expect(section).toContainText('东区 · 同名场地')
 await expect(section).toContainText('西区 · 同名场地')
 await expect(section).toContainText('已取消')
 await expect(section.locator('.schedule-pending')).toHaveCount(0)
})
