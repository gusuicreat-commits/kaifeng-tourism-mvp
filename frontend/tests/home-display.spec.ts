import {test,expect} from '@playwright/test'
import type {Page,Locator} from '@playwright/test'
async function swipeLeft(page:Page,rail:Locator){
 await rail.scrollIntoViewIfNeeded();const box=(await rail.boundingBox())!,session=await page.context().newCDPSession(page)
 const start=Math.min(box.x+box.width-25,365),end=box.x+25,y=box.y+90
 await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:start,y}]})
 for(let i=1;i<=12;i++)await session.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:start+(end-start)*i/12,y}]})
 await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await session.detach()
}
test('接口故障时基础探索可用，不显示虚假客流并允许重试',async({page})=>{
 await page.setViewportSize({width:390,height:844});await page.route('**/api/scenics?*',r=>r.abort());await page.goto('/')
 await expect(page.locator('.scenic-card')).toHaveCount(5);await expect(page.locator('.food-card')).toHaveCount(9)
 await expect(page.locator('.home-service-note')).toContainText('服务暂不可用');await expect(page.locator('.crowd-badge')).toHaveCount(0)
 await page.unroute('**/api/scenics?*');await page.getByRole('button',{name:'重试',exact:true}).click();await expect(page.locator('.home-service-note')).toHaveCount(0)
})
test('手机两排独立滑动、搜索、九道菜图片及真实详情入口',async({browser})=>{
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true}),page=await context.newPage()
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('/')
 const scenic=page.getByRole('region',{name:'景点选择',exact:true}),food=page.getByRole('region',{name:'美食选择',exact:true})
 await expect(scenic.locator('.scenic-card')).toHaveCount(5);await expect(food.locator('.food-card')).toHaveCount(9)
 await swipeLeft(page,scenic);await expect.poll(()=>scenic.evaluate(el=>el.scrollLeft)).toBeGreaterThan(100);expect(await food.evaluate(el=>el.scrollLeft)).toBe(0)
 await swipeLeft(page,food);await expect.poll(()=>food.evaluate(el=>el.scrollLeft)).toBeGreaterThan(50)
 for(const [name,file] of [['套四宝','taosibao'],['桶子鸡','tongziji'],['羊肉炕馍','yangroukangmo'],['鲤鱼焙面','liyubeimian'],['四味菜','siweicai']]){
  const card=food.locator('.food-card').filter({hasText:name});await card.scrollIntoViewIfNeeded();await expect(card.locator('img')).toHaveAttribute('src',`/images/foods/${file}.jpg`);await expect.poll(()=>card.locator('img').evaluate(el=>(el as HTMLImageElement).naturalWidth)).toBe(1448)
 }
 await page.getByLabel('搜索景点、美食或演出').fill('四味菜');await expect(food.locator('.food-card')).toHaveCount(1);await food.getByRole('link').click();await expect(page).toHaveURL(/\/food\/siweicai/)
 await expect(page.getByRole('heading',{name:'四味菜',level:1,exact:true})).toBeVisible();await expect(page.locator('.food-detail-hero img')).toHaveAttribute('src','/images/food-workbook/siweicai-r23-dish-1.jpg')
 for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)}
 expect(errors).toEqual([]);await context.close()
})
test('收藏、素材篮和行程各有归属，单纯收集素材不会生成可见行程',async({page})=>{
 await page.goto('/scenic/qingming');await page.getByRole('button',{name:'收藏清明上河园',exact:true}).click()
 await page.getByRole('button',{name:'加入素材篮：清明上河园',exact:true}).click();await expect(page.getByRole('button',{name:'移出素材篮：清明上河园',exact:true})).toBeVisible()
 await page.goto('/me');await expect(page.locator('.favorite-row')).toContainText('清明上河园')
 await expect(page.getByRole('button',{name:'行程',exact:true})).toHaveCount(0);await expect(page.locator('.saved-trip-card')).toHaveCount(0)
 await page.getByRole('navigation',{name:'主导航'}).getByRole('link',{name:'行程',exact:true}).click();await expect(page).toHaveURL(/\/city-trip/)
 await page.goto('/city-trip');await expect(page.locator('.journey-candidates')).toHaveCount(0);await expect(page.locator('.trip-plan-empty')).toBeVisible()
 await page.goto('/');await expect(page.getByRole('region',{name:'当前行程'})).toHaveCount(0)
 await page.getByRole('link',{name:'旅行素材篮',exact:true}).click();await expect(page.locator('.basket-item')).toContainText('清明上河园');await page.reload();await expect(page.locator('.basket-item')).toContainText('清明上河园')
 await page.goto('/');await page.getByLabel('搜索景点、美食或演出').fill('不存在的风景');await expect(page.getByText('没有找到相符的景点')).toBeVisible()
})


test('我的只展示收藏和往期记录，当前行程统一从底部进入',async({page})=>{
 await page.setViewportSize({width:465,height:769})
 await page.request.post('/api/v1/sessions')
 const previous=await(await page.request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft:{business_date:'2026-11-01'}}})).json()
 const current=await(await page.request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft:{business_date:'2026-11-02'}}})).json()
 await page.goto('/me')
 await expect(page.getByRole('button',{name:'行程',exact:true})).toHaveCount(0)
 await expect(page.getByRole('heading',{name:'还没有行程',exact:true})).toHaveCount(0)
 await page.getByText('往期行程',{exact:true}).click()
 await expect(page.locator('.saved-trip-card')).toHaveCount(1)
 await expect(page.locator('.saved-trip-card')).toHaveAttribute('href','/city-trip/'+previous.id)
 await page.screenshot({path:'../docs/screenshots/my-page-simplified-mobile.png',fullPage:true})
 await page.getByRole('navigation',{name:'主导航'}).getByRole('link',{name:'行程',exact:true}).click()
 await expect(page.locator('.journey-page-heading')).toContainText('2026-11-02')
 expect((await(await page.request.get('/api/city/v1/trips/current')).json()).trip.id).toBe(current.id)
})
