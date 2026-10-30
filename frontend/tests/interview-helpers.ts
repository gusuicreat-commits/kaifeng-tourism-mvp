import {expect,type Page} from '@playwright/test'
export async function openInterview(page:Page,scenic='wansui'){
 await page.goto('/assistant?scenic='+scenic)
 await expect(page.getByRole('heading',{name:'一起安排这一程',exact:true})).toBeVisible()
 await page.getByRole('button',{name:'重新开始',exact:true}).click()
 await page.getByRole('button',{name:scenic==='qingming'?'清明上河园':'万岁山武侠城',exact:true}).click()
}
export async function fillBasics(page:Page,minutes=120,date='2026-11-01',time='13:00'){
 await page.getByLabel('游玩日期',{exact:true}).fill(date)
 await page.getByLabel('入园时间',{exact:true}).fill(time)
 await page.getByLabel('游玩时长（分钟）',{exact:true}).fill(String(minutes))
 await page.getByRole('button',{name:'记下了，继续',exact:true}).click()
 await expect(page.getByRole('group',{name:'兴趣偏好'})).toBeVisible()
 await expect(page.locator('.memory-status')).not.toContainText('正在保存')
}
export async function finishInterests(page:Page){
 await page.getByRole('button',{name:'继续，安排节奏',exact:true}).click()
 await expect(page.getByRole('group',{name:'路线重点'})).toBeVisible()
 await expect(page.locator('.memory-status')).not.toContainText('正在保存')
}
export async function setPace(page:Page,quiet=false){
 await page.getByRole('group',{name:'路线重点'}).getByRole('button',{name:quiet?'尽量避拥挤':'按兴趣安排',exact:true}).click()
 await page.getByRole('group',{name:'步行节奏'}).getByRole('button',{name:'正常步速',exact:true}).click()
 await page.getByRole('button',{name:'查看我的需求',exact:true}).click()
 await expect(page.getByRole('button',{name:'确认，生成我的路线',exact:true})).toBeVisible()
 await expect(page.locator('.memory-status')).not.toContainText('正在保存')
}
export async function submitPlan(page:Page){
 const response=page.waitForResponse(r=>/\/api\/v1\/trips\/[^/]+\/replan$/.test(r.url())&&r.request().method()==='POST')
 await page.getByRole('button',{name:'确认，生成我的路线',exact:true}).click()
 const body=await (await response).json();return body.plan||body
}
export async function finishCityPlanningMode(page:Page,mode:'deep'|'quick'='deep'){
 const form=page.locator('.planning-question')
 await expect(form).toContainText('几个人一起去？')
 await form.getByRole('button',{name:'1 人',exact:true}).click()
 await form.getByRole('group',{name:'选择互动模式',exact:true}).getByRole('button',{name:mode==='deep'?'深度互动':'快速安排',exact:false}).click()
 if(mode==='deep')await form.getByRole('button',{name:'继续',exact:true}).click()
}
