import {test,expect,type Page} from '@playwright/test'
import {fileURLToPath} from 'node:url'
async function prepare(page:Page){
 await page.addInitScript(()=>{(window as any).geoCalls=0;navigator.geolocation.getCurrentPosition=()=>{(window as any).geoCalls++;throw new Error('unexpected GPS')}})
 await page.goto('/assistant?context=city')
 await expect(page.getByLabel('告诉小汴梁你的问题或想调整的安排',{exact:true})).toBeEnabled()
}
async function send(page:Page,text:string){
 await page.getByLabel('告诉小汴梁你的问题或想调整的安排',{exact:true}).fill(text)
 await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByLabel('告诉小汴梁你的问题或想调整的安排',{exact:true})).toBeEnabled()
}

test('明确起终点经V3查询参考路线，不依赖GPS模型或正式资料',async({page})=>{
 const tasks:any[]=[];let formalCalls=0
 page.on('response',async r=>{if(r.url().endsWith('/step'))tasks.push(await r.json())})
 page.on('request',r=>{if(r.url().includes('/engine/')&&r.method()==='POST')formalCalls++})
 await prepare(page);await send(page,'从开封站到清明上河园怎么走')
 const card=page.getByLabel('高德参考路线',{exact:true})
 await expect(card).toContainText('开封站 → 清明上河园')
 await expect(card).toContainText('12 分钟')
 await expect(card).toContainText('[合成测试]沿测试路前行')
 await expect(card).toContainText('出入口尚未核验')
 const final=tasks.find(t=>t.status==='answered')
 expect(final.events.map((e:any)=>e.tool)).toEqual(['understand','search_locations','query_reference_route'])
 expect(final.metrics.model_calls).toBe(0);expect(formalCalls).toBe(0)
 expect(await page.evaluate(()=>(window as any).geoCalls)).toBe(0)
 expect((await(await page.request.get('/api/city/v1/trips/current')).json()).trip.route_version).toBe(0)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.screenshot({path:fileURLToPath(new URL('../../docs/screenshots/assistant-reference-synthetic-mobile.png',import.meta.url)),fullPage:true})
})

test('同名地点先点选，选择之前不能发起算路工具',async({page})=>{
 let toolCalls=0;page.on('response',async r=>{if(r.url().endsWith('/step')){const t=await r.json();if(t.events.some((e:any)=>e.tool==='query_reference_route'))toolCalls++}})
 await prepare(page);await send(page,'从模糊站到清明上河园怎么走')
 const choices=page.getByLabel('地点消歧',{exact:true})
 await expect(choices).toBeVisible();expect(toolCalls).toBe(0)
 await expect(choices.getByRole('button').filter({hasText:'模糊站'})).toHaveCount(2)
 await choices.getByRole('button').filter({hasText:'北侧地址'}).click()
 await expect(page.getByLabel('高德参考路线',{exact:true})).toBeVisible()
 expect(await page.evaluate(()=>(window as any).geoCalls)).toBe(0)
})

test('明确说从当前位置出发才补问位置，且不自动请求GPS',async({page})=>{
 await prepare(page);await send(page,'从我这里到清明上河园怎么走')
 await expect(page.locator('.agent-messages')).toContainText('这次起点是“当前位置”')
 await expect(page.getByLabel('高德参考路线',{exact:true})).toHaveCount(0)
 expect(await page.evaluate(()=>(window as any).geoCalls)).toBe(0)
})

test('模型未配置与高德失败分别报告，不回落到GPS或正式规划资料',async({page})=>{
 const results:any[]=[];page.on('response',async r=>{if(r.url().endsWith('/step'))results.push(await r.json())})
 await prepare(page);await send(page,'这个和我的定位又没关系')
 await expect(page.locator('.agent-messages')).toContainText('聊天模型尚未配置')
 expect(results.find(t=>t.result?.code==='MODEL_NOT_CONFIGURED').result.state).toBe('model_unavailable')
 await send(page,'从API故障站到清明上河园怎么走')
 await expect(page.locator('.agent-messages')).toContainText('地点查询暂不可用')
 const failure=results.find(t=>t.result?.code==='POI_LOOKUP_FAILED')
 expect(failure.result.state).toBe('locations_explicit')
 expect(await page.evaluate(()=>(window as any).geoCalls)).toBe(0)
})
