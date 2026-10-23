import {test,expect} from '@playwright/test'

test('书笺聊天独立滚动，翻旧记录保留位置且输入区不被遮挡',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 await page.setViewportSize({width:465,height:769})
 await page.route('**/assistant/messages*',async route=>{
  const older=new URL(route.request().url()).searchParams.has('before')
  await route.fulfill({json:{messages:Array.from({length:older?4:40},(_,i)=>({id:String(i),role:i%2?'assistant':'user',text:`第${older?i:i+4}条合成记录。一起商量开封的行旅安排，地点与时间尚需核验。`})),before:older?null:5}})
 })
 await page.goto('/assistant?context=city')
 const input=page.locator('#city-agent-input'),scroll=page.locator('.chat-scroll')
 await expect(input).toBeEnabled();await expect(page.locator('.chat-message')).toHaveCount(40)
 await expect.poll(()=>scroll.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeLessThan(4)
 await scroll.hover();await page.mouse.wheel(0,-100000)
 await expect(page.getByRole('button',{name:'回到最新'})).toBeVisible()
 await page.getByRole('button',{name:'查看更早的记录'}).click()
 await expect(page.locator('.chat-message')).toHaveCount(44)
 expect(await scroll.evaluate(el=>el.scrollTop)).toBeLessThan(1000)
 await expect(input).toBeInViewport()
 await page.getByRole('button',{name:'回到最新'}).click()
 await expect.poll(()=>scroll.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeLessThan(4)
 for(const width of [320,465,1200]){
  await page.setViewportSize({width,height:769})
  await expect(input).toBeInViewport()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  if(width<760){
   const nav=await page.getByRole('navigation',{name:'主导航'}).boundingBox()
   const composer=await page.locator('.chat-composer').boundingBox()
   const mascot=await page.locator('.global-assistant-launcher').boundingBox()
   const log=await scroll.boundingBox()
   expect(composer!.y+composer!.height).toBeLessThanOrEqual(nav!.y)
   expect(mascot!.y).toBeGreaterThanOrEqual(log!.y+log!.height)
  }
 }
 await page.setViewportSize({width:465,height:769})
 if(await page.getByRole('button',{name:'回到最新'}).isVisible())await page.getByRole('button',{name:'回到最新'}).click()
 await expect.poll(()=>scroll.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeLessThan(4)
 await page.screenshot({path:'../docs/screenshots/assistant-chat-scroll-mobile.png'})
 expect(errors).toEqual([])
})

test('同地往返话语进入现有规划表单，刷新后仍保留两个地点意向',async({page})=>{
 await page.goto('/assistant?context=city');const input=page.locator('#city-agent-input')
 await expect(input).toBeEnabled();await input.fill('从开封站出发和返回')
 await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByRole('button',{name:'从开封站',exact:true})).toBeVisible()
 await expect(page.getByRole('button',{name:'回到开封站',exact:true})).toBeVisible()
 await expect(input).toBeEnabled()
 await page.reload();await expect(input).toBeEnabled()
 const trip=(await(await page.request.get('/api/city/v1/trips/current')).json()).trip
 const saved=(await(await page.request.get('/api/city/v1/trips/'+trip.id+'/exploration/constraints')).json()).constraints
 expect(saved.origin_text).toBe('开封站');expect(saved.destination_text).toBe('开封站')
 expect(saved.origin_place_id).toBeNull();expect(saved.destination_place_id).toBeNull()
 expect(trip.route_version).toBe(0)
})

test('手机连续问答走对话分支，刷新恢复且不改写规划条件',async({page})=>{
 await page.setViewportSize({width:465,height:769})
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 await page.goto('/assistant?context=city')
 const input=page.locator('#city-agent-input')
 await expect(input).toBeEnabled()
 const trip=(await(await page.request.get('/api/city/v1/trips/current')).json()).trip
 const path='/api/city/v1/trips/'+trip.id
 const workspace=await(await page.request.get(path+'/exploration/constraints')).json()
 for(const [text,reply] of [
  ['你能干嘛','可以帮你整理'],
  ['什么叫补齐候选要求？','尚未保存的行程草案'],
  ['你刚才说的草案是什么意思？','确认后才会保存'],
 ]){
  await input.fill(text)
  const response=page.waitForResponse(async r=>r.url().includes('/agent/tasks/')&&r.url().endsWith('/step')&&(await r.json()).status==='answered')
  await page.getByRole('button',{name:'发送',exact:true}).click()
  const task=await(await response).json()
  expect(task.mode).toBe('conversation');expect(task.interpretation.source).toBe('mock_model')
  await expect(page.locator('.agent-messages p').last()).toContainText(reply)
  await expect(input).toBeEnabled()
 }
 expect(await(await page.request.get(path)).json()).toEqual(trip)
 expect(await(await page.request.get(path+'/exploration/constraints')).json()).toEqual(workspace)
 await page.reload();await expect(page.locator('.agent-messages p').last()).toContainText('确认后才会保存')
 await expect(page.locator('.agent-messages')).not.toContainText('请先补齐要求并生成候选')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.locator('.agent-messages').scrollIntoViewIfNeeded()
 await page.screenshot({path:'../docs/screenshots/assistant-dialogue-mobile.png'})
 expect(errors).toEqual([])
})

for(const cancel of [false,true])test(`首次模型等待${cancel?'可以取消':'到时继续'}，不重复提交或提前终止`,async({page})=>{
 await page.setViewportSize({width:465,height:769})
 let steps=0
 await page.route('**/agent/tasks/*/step',async route=>{
  steps++
  if(steps===1){
   const url=route.request().url().replace(/\/step$/,'')
   const task=await(await page.request.get(url)).json()
   await route.fulfill({json:{...task,phase:'waiting_model',waiting:{retry_after_seconds:cancel?5:0.15}}})
  }else await route.continue()
 })
 await page.goto('/assistant?context=city');const input=page.locator('#city-agent-input')
 await expect(input).toBeEnabled();await input.fill('你能干嘛')
 await page.getByRole('button',{name:'发送',exact:true}).click()
 if(cancel){
  await expect(page.getByRole('status')).toContainText('小汴梁正在思考')
  await page.getByRole('button',{name:'停止处理',exact:true}).click()
  await expect(page.locator('.agent-messages p').last()).toContainText('已取消')
  await expect(input).toBeEnabled();await page.waitForTimeout(400);expect(steps).toBe(1)
 }else{
  await expect(page.locator('.agent-messages p').last()).toContainText('可以帮你整理')
  await expect(input).toBeEnabled();expect(steps).toBe(2)
 }
})
