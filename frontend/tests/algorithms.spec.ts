import {test,expect} from '@playwright/test'
import {openInterview,fillBasics,finishInterests,setPace,submitPlan} from './interview-helpers'

test('日期分钟进入实际求解，地图与行程刷新保持同一结果',async({page})=>{
 await openInterview(page);await fillBasics(page,120,'2026-11-01','13:30')
 await page.getByRole('checkbox',{name:'示意·武侠剧场',exact:true}).check()
 await finishInterests(page);await setPace(page)
 const p=await submitPlan(page)
 expect(p.request.visit_start).toBe('2026-11-01T13:30:00+08:00');expect(p.algorithm).toContain('OR-Tools')
 expect(p.stops.find((s:{id:string})=>s.id==='theatre').session_id).toBeNull()
 expect(p.total_minutes).toBeLessThanOrEqual(p.request.available_minutes);expect(p.route_edges.length).toBeGreaterThan(0)
 await expect(page.locator('.itinerary')).toContainText('仅安排场地游览')
 await page.getByText('行程依据与提醒',{exact:true}).click();await expect(page.locator('.route-evidence')).toContainText('演出时间表未核实')
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.reload();await expect(page.locator('.itinerary')).toContainText('仅安排场地游览')
})

test('自然语言只产生待确认建议，服务失败仍能选择回答',async({page})=>{
 await openInterview(page)
 await page.getByLabel('也可以直接告诉小汴梁',{exact:true}).fill('今天少走路')
 await page.getByRole('button',{name:'发送安排',exact:true}).click()
 await expect(page.getByRole('button',{name:'确认填入需求卡',exact:true})).toBeVisible()
 await expect(page.getByRole('complementary',{name:'我的游览需求'})).not.toContainText('少走路')
 await page.getByRole('button',{name:'确认填入需求卡',exact:true}).click()
 await expect(page.getByRole('complementary',{name:'我的游览需求'})).toContainText('少走路')
 await fillBasics(page);await page.getByRole('button',{name:'继续，安排节奏',exact:true}).click()
 // Explicit confirmation supplied the pace, so only unanswered fields remain.
 await expect(page.getByRole('button',{name:'确认，生成我的路线',exact:true})).toBeVisible()
 const p=await submitPlan(page);expect(p.request.mode).toBe('shortest');await expect(page).toHaveURL(/\/result\//)
 await openInterview(page)
 await page.route('**/api/v1/chat',r=>r.fulfill({status:503,json:{detail:'自然语言服务暂不可用'}}))
 await page.getByLabel('也可以直接告诉小汴梁',{exact:true}).fill('测试服务失败')
 await page.getByRole('button',{name:'发送安排',exact:true}).click()
 await expect(page.getByRole('status')).toContainText('自然语言服务暂不可用')
 await fillBasics(page);await page.getByRole('button',{name:'暂时没有特别要求',exact:true}).click();await page.getByRole('button',{name:'按常规节奏安排',exact:true}).click()
 await submitPlan(page);await expect(page).toHaveURL(/\/result\//)
})
