import {test,expect} from '@playwright/test'
test('景区地图默认没有模拟场次或热力，显式演示才出现示例路线',async({page})=>{
 await page.goto('/guide?scenic=wansui');await expect(page.locator('.city-map-workspace')).toBeVisible()
 await expect(page.locator('.guide-event-card,.guide-crowd-halo')).toHaveCount(0)
 await page.getByRole('link',{name:'查看景区示意交互演示'}).click();await expect(page.locator('.demo-mode-notice')).toContainText('模拟日程')
 await expect(page.getByRole('img',{name:/宋风浅立体景区示意地图/})).toBeVisible()
 await page.getByRole('button',{name:'切换地图图层'}).click();await page.getByLabel('演出点位').uncheck()
 await page.getByRole('tab',{name:'清明上河园',exact:true}).click();await expect(page).toHaveURL(/scenic=qingming/)
 await page.locator('.guide-event-card.status-upcoming').first().click();await page.getByRole('button',{name:'查看示意路线'}).click()
 await expect(page.locator('.guide-route-summary')).toContainText('非实际导航')
})
test('地图与行程共享入口，小汴梁保留景区上下文',async({page})=>{
 await page.setViewportSize({width:390,height:844});await page.goto('/guide?scenic=qingming')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.getByRole('button',{name:'打开小汴梁智能助手',exact:true}).click();await expect(page).toHaveURL(/scenic=qingming/)
 await expect(page.getByRole('group',{name:'游玩景区'}).getByRole('button',{name:'清明上河园',exact:true})).toHaveAttribute('aria-pressed','true')
})
