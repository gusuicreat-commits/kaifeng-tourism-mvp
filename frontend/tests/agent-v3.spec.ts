import {test,expect,type Page} from '@playwright/test'

async function prepare(page:Page){
 const request=page.request
 await request.post('/api/v1/sessions')
 const draft={business_date:'2030-05-01',starts_at:'2030-05-01T09:00:00+08:00',ends_at:'2030-05-01T20:00:00+08:00',
  origin_place_id:'origin',destination_place_id:'hotel',modes:['walking'],stops:[
   {stop_id:'show',place_id:'stage2',kind:'show',session_id:'s2',locked:true},
   {stop_id:'lunch',place_id:'restaurant',kind:'dining',locked:false,duration_seconds:2700,window_start:'2030-05-01T12:00:00+08:00',window_end:'2030-05-01T14:00:00+08:00'}]}
 let response=await request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft}})
 expect(response.ok()).toBeTruthy();let trip=await response.json();const path='/api/city/v1/trips/'+trip.id
 for(const action of [{operation:'transition',status:'ACTIVE'},{operation:'set_anchor',anchor:{place_id:'museum',confirmed_at:'2030-05-01T09:40:00+08:00'}}]){
  response=await request.patch(path,{data:{request_id:crypto.randomUUID(),expected_version:trip.version,action}})
  expect(response.ok()).toBeTruthy();trip=await response.json()
 }
 await page.goto('/assistant?city_trip='+trip.id)
 await expect(page.getByLabel('告诉小汴梁你的问题或想调整的安排')).toBeEnabled()
 return {trip,path}
}

test('city task runs through actual APIs and confirms the shared route version',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 const {trip,path}=await prepare(page)
 await page.getByLabel('告诉小汴梁你的问题或想调整的安排').fill('附近找个吃饭的地方，下午演出必须保留，最好少走一点。')
 await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByRole('heading',{name:'请核对新安排'})).toBeVisible()
 expect(await (await page.request.get(path)).json()).toEqual(trip)
 await expect(page.getByLabel('待确认修改')).toContainText('[合成测试]restaurant')
 await expect(page.getByLabel('待确认修改')).toContainText('12:00')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 await page.screenshot({path:'../docs/screenshots/agent-v3-synthetic-preview-mobile.png',fullPage:true})
 await page.getByRole('button',{name:'确认修改',exact:true}).click()
 await expect(page.getByLabel('城市旅行对话')).toContainText('确认成功，已读取最新正式路线。')
 const latest=await (await page.request.get(path)).json();expect(latest.route_version).toBe(1)
 await page.getByRole('link',{name:'返回行程'}).click()
 await expect(page.getByRole('heading',{name:'我的开封游记'})).toBeVisible()
 expect(await (await page.request.get(path)).json()).toEqual(latest)
 expect(errors).toEqual([])
})

test('discard candidate leaves official city state unchanged',async({page})=>{
 const {trip,path}=await prepare(page)
 await page.getByLabel('告诉小汴梁你的问题或想调整的安排').fill('附近找个吃饭的地方，演出必须看。')
 await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByRole('button',{name:'放弃修改',exact:true})).toBeVisible()
 await page.getByRole('button',{name:'放弃修改',exact:true}).click()
 await expect(page.getByLabel('城市旅行对话')).toContainText('已取消本次修改，正式行程保持不变。')
 expect(await (await page.request.get(path)).json()).toEqual(trip)
})

test('vague return time asks for clarification without a route candidate',async({page})=>{
 const {trip,path}=await prepare(page)
 const input=page.getByLabel('告诉小汴梁你的问题或想调整的安排')
 await input.fill('晚上别太晚回酒店。');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByText(/希望最晚几点/)).toBeVisible()
 expect(await (await page.request.get(path)).json()).toEqual(trip)
 await expect(page.getByRole('button',{name:'确认修改',exact:true})).toHaveCount(0)
})

test('unknown food supply never creates a restaurant or executable route',async({page})=>{
 const {trip,path}=await prepare(page)
 await page.getByLabel('告诉小汴梁你的问题或想调整的安排').fill('附近找一家必须能吃灌汤包的店，演出必须看。')
 await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByLabel('城市旅行对话')).toContainText('现有资料还排不出')
 expect(await (await page.request.get(path)).json()).toEqual(trip)
 await expect(page.getByRole('button',{name:'确认修改',exact:true})).toHaveCount(0)
})

test('basket planning uses the existing engine, blocks stale preview, then confirms one official route',async({page})=>{
 await page.request.post('/api/v1/sessions')
 const draft={business_date:'2030-05-01',starts_at:'2030-05-01T10:00:00+08:00',ends_at:'2030-05-01T20:00:00+08:00',origin_place_id:'origin',destination_place_id:'hotel',modes:['walking'],stops:[]}
 const trip=await(await page.request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft}})).json(),path='/api/city/v1/trips/'+trip.id
 await page.goto('/place/museum');await page.getByRole('button',{name:'加入素材篮：[合成测试]museum',exact:true}).click();await expect(page.getByRole('button',{name:'移出素材篮：[合成测试]museum',exact:true})).toBeVisible()
 await page.goto('/basket');await page.getByRole('button',{name:'必去',exact:true}).click();await expect(page.getByRole('button',{name:'必去',exact:true})).toHaveAttribute('aria-pressed','true')
 await page.getByRole('link',{name:'交给小汴梁安排',exact:true}).click();await page.getByRole('button',{name:'生成安排',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('已有经过校验的候选')
 await page.getByLabel('告诉小汴梁你的问题或想调整的安排').fill('就这样');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.locator('.agent-messages')).toContainText('方案已准备好。请点“确认方案”保存。')
 expect((await(await page.request.get(path)).json()).route_version).toBe(0)
 const count=await page.locator('.plan-option').count();expect(count).toBeGreaterThan(0);expect(count).toBeLessThanOrEqual(3)
 await page.getByRole('button',{name:'10:00—20:00',exact:true}).click();await page.getByLabel('最晚结束',{exact:true}).fill('19:00');await expect(page.getByRole('button',{name:/确认方案/})).toHaveCount(0)
 await page.getByRole('button',{name:'继续',exact:true}).click();await expect(page.getByRole('button',{name:/确认方案/})).toBeEnabled()
 await page.getByRole('button',{name:/确认方案/}).click();await expect(page.getByRole('status').filter({hasText:'已确认并更新正式路线'})).toBeVisible()
 expect((await(await page.request.get(path)).json()).route_version).toBe(1)
 expect((await(await page.request.get(path+'/basket')).json()).items[0].status).toBe('arranged')
 await page.getByRole('link',{name:'查看行程',exact:true}).click();await expect(page.locator('.journey-timeline')).toContainText('[合成测试]museum')
 const official=(await(await page.request.get(path+'/engine/current')).json()).itinerary
 const start=new Date(official.activities[0].starts_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false})
 await expect(page.locator('.flow-activity .timeline-clock').first()).toHaveText(start)
 await expect(page.locator('.journey-next-card')).toContainText('预计转场')
})

test('conflict negotiation persists a structured choice across refresh, recomputes, and only confirms on request',async({page})=>{
 await page.request.post('/api/v1/sessions')
 const draft={business_date:'2030-05-01',starts_at:'2030-05-01T10:45:00+08:00',ends_at:'2030-05-01T11:45:00+08:00',origin_place_id:'origin',destination_place_id:'hotel',modes:['walking'],stops:[]}
 const trip=await(await page.request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft}})).json(),path='/api/city/v1/trips/'+trip.id
 let version=0
 for(const [kind,id] of [['place','museum'],['show','s1']]){
  const response=await page.request.patch(path+'/basket',{data:{request_id:crypto.randomUUID(),expected_version:version,item:{kind,object_id:id,priority:'must'}}})
  expect(response.ok()).toBeTruthy();version=(await response.json()).version
 }
 await page.goto('/assistant?city_trip='+trip.id+'&plan=basket')
 await page.getByRole('button',{name:'生成安排',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('需要你决定取舍')
 await page.getByLabel('规划检测结果').scrollIntoViewIfNeeded()
 await page.screenshot({path:'../docs/screenshots/basket-negotiation-mobile.png'})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 await page.reload();await expect(page.getByLabel('规划检测结果')).toContainText('需要你决定取舍')
 await page.getByLabel('告诉小汴梁你的问题或想调整的安排',{exact:true}).fill('[合成测试]museum改为想去')
 await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByLabel('待确认的素材篮变更')).toBeVisible()
 expect((await(await page.request.get(path+'/basket')).json()).items[0].priority).toBe('must')
 await page.getByRole('button',{name:'确认加入',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('已有经过校验的候选')
 expect((await(await page.request.get(path+'/basket')).json()).items[0].priority).toBe('want')
 expect((await(await page.request.get(path)).json()).route_version).toBe(0)
 await page.getByRole('button',{name:/确认方案/}).scrollIntoViewIfNeeded()
 await page.screenshot({path:'../docs/screenshots/basket-candidate-mobile.png'})
 await page.setViewportSize({width:1440,height:1000});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 await page.getByLabel('规划检测结果').scrollIntoViewIfNeeded()
 await page.screenshot({path:'../docs/screenshots/basket-candidate-desktop.png'})
 await page.reload();await expect(page.getByRole('button',{name:/确认方案/})).toBeEnabled()
 await page.getByRole('button',{name:'保留原行程',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toHaveCount(0)
 await page.reload();await expect(page.getByLabel('规划检测结果')).toHaveCount(0)
 expect((await(await page.request.get(path)).json()).route_version).toBe(0)
 await page.getByRole('button',{name:'生成安排',exact:true}).click()
 await page.getByRole('button',{name:/确认方案/}).click()
 await expect(page.getByRole('status').filter({hasText:'已确认并更新正式路线'})).toBeVisible()
 expect((await(await page.request.get(path)).json()).route_version).toBe(1)
})

test('chat proposes a basket addition and only saves it after confirmation before continuing planning',async({page})=>{
 await page.request.post('/api/v1/sessions')
 const draft={business_date:'2030-05-01',starts_at:'2030-05-01T10:00:00+08:00',ends_at:'2030-05-01T20:00:00+08:00',origin_place_id:'origin',destination_place_id:'hotel',modes:['walking'],stops:[]}
 const trip=await(await page.request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft}})).json(),path='/api/city/v1/trips/'+trip.id
 await page.goto('/assistant?city_trip='+trip.id)
 const input=page.getByLabel('告诉小汴梁你的问题或想调整的安排')
 await expect(input).toBeEnabled();await input.fill('想去[合成测试]museum')
 await page.getByRole('button',{name:'发送',exact:true}).click()
 const card=page.getByLabel('待确认的素材篮变更')
 await expect(card).toContainText('[合成测试]museum · 加入素材篮')
 await expect(card.getByLabel('[合成测试]museum优先级')).toHaveValue('want')
 await card.getByLabel('[合成测试]museum优先级').selectOption('must')
 expect((await(await page.request.get(path+'/basket')).json()).items).toEqual([])
 await page.getByRole('button',{name:'确认加入',exact:true}).click()
 await expect(card).toHaveCount(0)
 await expect(page.getByLabel('规划检测结果')).toContainText('已有经过校验的候选')
 const basket=await(await page.request.get(path+'/basket')).json()
 expect(basket.items).toHaveLength(1);expect(basket.items[0]).toMatchObject({kind:'place',id:'museum',priority:'must',source:'assistant'})
 expect((await(await page.request.get(path)).json()).route_version).toBe(0)
})

async function proposeBasket(page:Page){
 const {path}=await prepare(page),input=page.getByLabel('告诉小汴梁你的问题或想调整的安排')
 let starts=0,patches=0
 page.on('request',request=>{
  if(request.method()==='POST'&&request.url().endsWith(path+'/agent/tasks'))starts++
  if(request.method()==='PATCH'&&request.url().endsWith(path+'/basket'))patches++
 })
 await input.fill('想去[合成测试]museum');await page.getByRole('button',{name:'发送',exact:true}).click()
 const card=page.getByLabel('待确认的素材篮变更')
 await expect(card).toBeVisible();await expect(input).toBeEnabled()
 expect(starts).toBe(1);expect(patches).toBe(0)
 return {path,input,card,counts:()=>({starts,patches})}
}

test('typed acceptance confirms the basket without starting another agent task',async({page})=>{
 const {path,input,card,counts}=await proposeBasket(page)
 await card.getByLabel('[合成测试]museum优先级').selectOption('must')
 await input.fill('好的，加进去');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.locator('.agent-messages')).toContainText('素材篮已更新')
 await expect(card).toHaveCount(0);await expect(input).toBeEnabled();await expect(input).toHaveValue('')
 expect(counts()).toEqual({starts:1,patches:1})
 const basket=await(await page.request.get(path+'/basket')).json()
 expect(basket.items).toHaveLength(1);expect(basket.items[0]).toMatchObject({kind:'place',id:'museum',priority:'must',source:'assistant'})
 expect((await(await page.request.get(path)).json()).route_version).toBe(0)
})

test('typed refusal discards the basket proposal without a write or agent task',async({page})=>{
 const {path,input,card,counts}=await proposeBasket(page)
 await input.fill('算了');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.locator('.agent-messages')).toContainText('已放弃本次素材篮变更')
 await expect(card).toHaveCount(0);await expect(input).toBeEnabled();await expect(input).toHaveValue('')
 expect(counts()).toEqual({starts:1,patches:0})
 expect((await(await page.request.get(path+'/basket')).json()).items).toEqual([])
})

test('other typed text keeps the pending basket and asks for an answer first',async({page})=>{
 const {path,input,card,counts}=await proposeBasket(page)
 await input.fill('好的，再加个包公祠');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByRole('alert')).toContainText('请先回答是否加入素材篮')
 // Nothing is sent, the text stays for editing, and the proposal is still there to answer.
 await expect(input).toHaveValue('好的，再加个包公祠');await expect(card).toBeVisible()
 expect(counts()).toEqual({starts:1,patches:0})
 expect((await(await page.request.get(path+'/basket')).json()).items).toEqual([])
})

test('short replies still go to the agent when there is no basket proposal',async({page})=>{
 const {path}=await prepare(page),input=page.getByLabel('告诉小汴梁你的问题或想调整的安排')
 for(const text of ['好的，加进去','算了']){
  const start=page.waitForRequest(request=>request.method()==='POST'&&request.url().endsWith(path+'/agent/tasks'))
  await input.fill(text);await page.getByRole('button',{name:'发送',exact:true}).click()
  expect((await start).postDataJSON().text).toBe(text)
  await expect(input).toBeEnabled();await expect(page.getByLabel('待确认的素材篮变更')).toHaveCount(0)
 }
 expect((await(await page.request.get(path+'/basket')).json()).items).toEqual([])
})

test('short acceptance waits for the remaining basket choices',async({page})=>{
 await page.route('**/agent/tasks/*/step',async route=>{
  const response=await route.fetch(),task=await response.json(),item=task.result?.basket_proposal?.items[0]
  if(item)task.result.basket_choices=[{query:item.name,quote:item.quote,priority:item.priority,candidates:[
   {kind:'place',object_id:'museum',name:'[合成测试]museum'}]}]
  await route.fulfill({response,json:task})
 })
 const {path,input,counts}=await proposeBasket(page)
 await expect(page.getByRole('button',{name:'确认加入',exact:true})).toBeDisabled()
 await input.fill('好的');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByRole('alert')).toContainText('请先在上面的候选里选定')
 await expect(input).toHaveValue('好的');expect(counts()).toEqual({starts:1,patches:0})
 expect((await(await page.request.get(path+'/basket')).json()).items).toEqual([])
})

test('chat basket confirmation refuses a changed basket version without overwriting it',async({page})=>{
 const {path}=await prepare(page)
 const input=page.getByLabel('告诉小汴梁你的问题或想调整的安排')
 await input.fill('想去[合成测试]museum');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByLabel('待确认的素材篮变更')).toBeVisible()
 const changed=await page.request.patch(path+'/basket',{data:{request_id:crypto.randomUUID(),expected_version:0,item:{kind:'place',object_id:'restaurant',source:'detail'}}})
 expect(changed.ok()).toBeTruthy();const before=await changed.json()
 await page.getByRole('button',{name:'确认加入',exact:true}).click()
 await expect(page.getByRole('alert')).toContainText('素材篮已变化，请刷新')
 expect(await(await page.request.get(path+'/basket')).json()).toEqual(before)
})

test('ambiguous basket candidates require selection and then confirmation',async({page})=>{
 const {path}=await prepare(page)
 await page.route('**/agent/tasks/*/step',async route=>{
  const response=await route.fetch(),task=await response.json()
  const item=task.result?.basket_proposal?.items[0]
  if(item){
   delete task.result.basket_proposal
   task.result.basket_choices=[{query:item.name,quote:item.quote,priority:item.priority,candidates:[
    {kind:'place',object_id:'museum',name:'[合成测试]museum',address:'[合成测试]博物馆'},
    {kind:'place',object_id:'restaurant',name:'[合成测试]restaurant',address:'[合成测试]餐厅'}]}]
  }
  await route.fulfill({response,json:task})
 })
 const input=page.getByLabel('告诉小汴梁你的问题或想调整的安排')
 await input.fill('想去[合成测试]museum');await page.getByRole('button',{name:'发送',exact:true}).click()
 const candidates=page.getByLabel('素材篮候选选择')
 await expect(candidates).toBeVisible();await expect(candidates.getByRole('button')).toHaveCount(3)
 await candidates.getByRole('button',{name:'[合成测试]restaurant [合成测试]餐厅',exact:true}).click()
 await expect(page.getByLabel('待确认的素材篮变更')).toContainText('[合成测试]restaurant')
 expect((await(await page.request.get(path+'/basket')).json()).items).toEqual([])
 await page.getByRole('button',{name:'确认加入',exact:true}).click()
 await expect(page.getByLabel('待确认的素材篮变更')).toHaveCount(0)
 expect((await(await page.request.get(path+'/basket')).json()).items[0].id).toBe('restaurant')
})

test('changing date while a candidate is visible preserves the official trip and invalidates the old candidate',async({page})=>{
 await page.request.post('/api/v1/sessions')
 const draft={business_date:'2030-05-01',starts_at:'2030-05-01T10:00:00+08:00',ends_at:'2030-05-01T20:00:00+08:00',origin_place_id:'origin',destination_place_id:'hotel',modes:['walking'],stops:[]}
 const trip=await(await page.request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft}})).json(),path='/api/city/v1/trips/'+trip.id
 await page.request.patch(path+'/basket',{data:{request_id:crypto.randomUUID(),expected_version:0,item:{kind:'place',object_id:'museum',priority:'must'}}})
 await page.goto('/assistant?city_trip='+trip.id+'&plan=basket');await page.getByRole('button',{name:'生成安排',exact:true}).click()
 await expect(page.getByRole('button',{name:/确认方案/})).toBeEnabled()
 const original=(await(await page.request.get(path+'/exploration/constraints')).json()).result.planning
 const input=page.getByLabel('告诉小汴梁你的问题或想调整的安排')
 await input.fill('改成2030-05-02');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.locator('.agent-messages')).toContainText('日期已记为 2030-05-02。')
 const workspace=await(await page.request.get(path+'/exploration/constraints')).json()
 expect(workspace.constraints.business_date).toBe('2030-05-02')
 expect(await(await page.request.get(path)).json()).toEqual(trip)
 const stale=await page.request.post(path+'/engine/confirm',{data:{request_id:crypto.randomUUID(),expected_version:original.base_version,candidate_id:original.candidate_id,alternative_index:0}})
 expect(stale.status()).toBe(409)
 expect((await(await page.request.get(path)).json()).route_version).toBe(0)
})
