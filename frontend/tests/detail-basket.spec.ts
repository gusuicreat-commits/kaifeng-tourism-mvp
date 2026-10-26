import {test,expect} from '@playwright/test'

test('缺资料保留媒体占位，景区已有视频只提供来源外链',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 for(const width of [320,390,482,1440]){
  await page.setViewportSize({width,height:844})
  for(const url of ['/scenic/qingming','/food/taosibao','/vendor/pending']){
   await page.goto(url);await expect(page.locator('.detail-media')).toBeVisible()
   if(url!=='/scenic/qingming')await expect(page.locator('.photo-strip .media-slot')).toHaveCount(3)
   else {await expect(page.locator('.video-link')).toHaveCount(3);await expect(page.locator('.photo-strip img[src*="wansui"]')).toHaveCount(0)}
   if(url==='/food/taosibao')await expect(page.locator('.video-slot')).toContainText('待添加');await expect(page.locator('video')).toHaveCount(0)
   if(url.startsWith('/food/'))await expect(page.getByText('适合何时品尝',{exact:true})).toHaveCount(0)
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
   if(width===390){await page.locator('.detail-media').scrollIntoViewIfNeeded();await page.screenshot({path:'../docs/screenshots/detail-skeleton-'+url.split('/').pop()+'-mobile.png'})}
  }
 }
 expect(errors).toEqual([])
})

test('景区只展示精选有时刻的主要演出',async({page})=>{
 const expected=[{id:'wansui',count:4,photos:14,needle:'王婆说媒'},{id:'qingming',count:6,photos:0,needle:'大宋·东京保卫战'}]
 for(const item of expected){
  await page.goto('/scenic/'+item.id)
  await expect(page.locator('#scenic-shows .detail-show')).toHaveCount(item.count)
  await expect(page.locator('#scenic-shows')).toContainText(item.needle)
  await expect(page.locator('#scenic-shows .show-photos img')).toHaveCount(item.photos)
  await expect(page.locator('#scenic-highlights a[href^="/place/"]')).toHaveCount(0)
  await expect(page.locator('.qmsy-table')).toHaveCount(0)
 }
})

test('万岁山图集按对象隔离，节目大图可滑动放大且手机不溢出',async({page})=>{
 for(const width of [320,465,1440]){
  await page.setViewportSize({width,height:900});await page.goto('/scenic/wansui')
  await expect(page.locator('.photo-strip .detail-photo')).toHaveCount(22)
  const photos=page.locator('#scenic-shows .show-photos').first()
  const first=photos.locator('button').first()
  await first.scrollIntoViewIfNeeded()
  await expect.poll(()=>first.locator('img').evaluate(image=>(image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)
  const bounds=await first.boundingBox();expect(bounds!.width).toBeGreaterThan(width<760?200:300)
  expect(await photos.evaluate(el=>el.scrollWidth>el.clientWidth)).toBe(true)
  await first.click()
  const dialog=page.getByRole('dialog',{name:'王婆说媒现场大图'})
  await expect(dialog).toBeVisible();await expect(dialog.locator('img')).toHaveAttribute('src',/wansui-program-r02-1/)
  await page.keyboard.press('Escape');await expect(dialog).not.toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.locator('.detail-media .detail-photo').nth(8).click()
  await expect(page.locator('.detail-media dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.goto('/scenic/qingming')
  await expect(page.locator('.photo-strip img[src*="wansui"],.show-photos img[src*="wansui"]')).toHaveCount(0)
 }
})

test('工作簿视频链接按景区匹配到影像记',async({page})=>{
 const expected:{id:string;count:number}[]=[{id:'qingming',count:3},{id:'wansui',count:3},{id:'kaifengfu',count:1},{id:'tieta',count:1},{id:'longting',count:1}]
 for(const item of expected){
  await page.goto('/scenic/'+item.id);await expect(page.locator('.video-link')).toHaveCount(item.count)
  await expect(page.locator('.video-link').first()).toHaveAttribute('href',/https?:\/\//)
  await expect(page.locator('.video-slot')).toHaveCount(0)
 }
})

test('团队表格中的美食与商家图片资料按行展示',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 for(const width of [390,1440]){
  await page.setViewportSize({width,height:844});await page.goto('/')
  await page.getByRole('button',{name:'美食',exact:true}).click()
  const foodLink=page.locator('.food-card .catalog-main').filter({hasText:'宫廷杏仁茶'})
  await expect(foodLink.locator('img')).toHaveAttribute('src','/images/foods/xingrentea.jpg')
  await foodLink.click()
  await expect(page.locator('.food-detail-hero img')).toHaveAttribute('src','/images/food-workbook/xingrentea-r02-dish-1.jpg')
  await expect(page.locator('.workbook-vendor')).toHaveCount(3)
  await expect(page.locator('.detail-media .detail-photo img')).toHaveCount(4)
  await expect(page.locator('.workbook-vendor').first()).toContainText('周记杏仁茶')
  await expect(page.locator('.workbook-vendor').first()).toContainText('17:00-24:00')
  for(const image of await page.locator('.detail-media .detail-photo img').all()){
   await image.scrollIntoViewIfNeeded()
   await expect.poll(()=>image.evaluate(image=>(image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)
  }
  await page.locator('.workbook-vendor-main').first().click()
  await expect(page).toHaveURL('/food-vendor/xingrentea-r02')
  await expect(page.locator('.detail-fact-grid')).toContainText('14元')
  await expect(page.locator('.workbook-vendor-gallery img')).toHaveCount(2)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 }
 expect(errors).toEqual([])
})

test('快捷加入跨页同步、优先级备注候补刷新、移除撤销且不改变正式路线',async({page})=>{
 await page.goto('/');await page.getByRole('button',{name:'加入素材篮：清明上河园',exact:true}).click()
 await expect(page.getByRole('button',{name:'移出素材篮：清明上河园',exact:true})).toContainText('已加入素材篮')
 const trip=(await(await page.request.get('/api/city/v1/trips/current')).json()).trip
 expect(trip.route_version).toBe(0);expect(trip.draft.stops).toEqual([])
 await page.goto('/scenic/qingming');await expect(page.getByRole('button',{name:'移出素材篮：清明上河园',exact:true})).toBeVisible()
 await page.goto('/basket');const card=page.locator('.basket-item').filter({hasText:'清明上河园'})
 await card.getByRole('button',{name:'必去',exact:true}).click();await expect(card.getByRole('button',{name:'必去',exact:true})).toHaveAttribute('aria-pressed','true')
 await card.getByLabel('补充备注').fill('留时间拍照');await card.getByLabel('补充备注').press('Tab')
 await expect.poll(async()=>((await(await page.request.get(`/api/city/v1/trips/${trip.id}/basket`)).json()).items[0].note)).toBe('留时间拍照')
 await card.getByRole('button',{name:'先放候补',exact:true}).click();await expect(card).toContainText('候补');await page.reload()
 await expect(card.getByLabel('补充备注')).toHaveValue('留时间拍照');await expect(card.getByRole('button',{name:'必去',exact:true})).toHaveAttribute('aria-pressed','true')
 expect((await(await page.request.get('/api/city/v1/trips/'+trip.id)).json()).version).toBe(1)
 await card.getByRole('button',{name:'移出素材篮：清明上河园',exact:true}).click();await expect(page.locator('.basket-item')).toHaveCount(0)
 await page.getByRole('button',{name:'撤销移除 清明上河园',exact:true}).click();await expect(page.locator('.basket-item')).toContainText('清明上河园')
})

test('详情提问携带稳定对象引用，切换对象后不串话，素材篮规划缺数据诚实降级',async({page})=>{
 await page.goto('/scenic/kaifengfu');await page.getByRole('button',{name:'这里适合游玩多久？',exact:true}).click()
 await expect(page.locator('.agent-messages')).toContainText('开封府：');await expect(page.locator('.agent-messages')).toContainText('待添加')
 await page.goto('/scenic/qingming');await page.getByRole('button',{name:'这里适合游玩多久？',exact:true}).click()
 await expect(page.locator('.agent-messages p').last()).toContainText('清明上河园：')
 const t0=(await(await page.request.get('/api/city/v1/trips/current')).json()).trip
 await page.request.patch('/api/city/v1/trips/'+t0.id,{data:{request_id:crypto.randomUUID(),expected_version:t0.version,action:{operation:'save_draft',draft:{...t0.draft,business_date:'2026-11-01',starts_at:'2026-11-01T09:00:00+08:00',ends_at:'2026-11-01T19:00:00+08:00',origin_place_id:'test:gate',destination_place_id:'test:hotel'}}}})
 await page.goto('/assistant?context=city&plan=basket');await page.getByRole('button',{name:'生成安排',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('信息不足');await expect(page.getByRole('button',{name:/确认方案/})).toHaveCount(0)
 const t=(await(await page.request.get('/api/city/v1/trips/current')).json()).trip;expect(t.route_version).toBe(0)
})

test('旧旅程和新旅程素材不混用，切换后刷新仍绑定所选旅程',async({page})=>{
 await page.goto('/food/yangroukangmo');await page.getByRole('button',{name:'加入素材篮：羊肉炕馍',exact:true}).click()
 await expect(page.getByRole('button',{name:'移出素材篮：羊肉炕馍',exact:true})).toBeVisible()
 const original=(await(await page.request.get('/api/city/v1/trips/current')).json()).trip
 const second=await(await page.request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft:{}}})).json()
 await page.goto('/city-trip/'+second.id);await expect(page.getByRole('heading',{name:'我的开封游记',exact:true})).toBeVisible();await page.goto('/basket');await expect(page.locator('.basket-item')).toHaveCount(0)
 await page.goto('/city-trip/'+original.id);await expect.poll(async()=>((await(await page.request.get('/api/city/v1/trips/current')).json()).trip.id)).toBe(original.id);await page.goto('/basket');await expect(page.locator('.basket-item')).toContainText('羊肉炕馍');await page.reload();await expect(page.locator('.basket-item')).toContainText('羊肉炕馍')
})


test('当前旅程读取失败不另建旅程，条件初始化期间不能抢先规划',async({page})=>{
 let creates=0
 await page.route('**/api/city/v1/trips/current',route=>route.fulfill({status:503,json:{detail:'暂时不可用'}}))
 page.on('request',req=>{if(req.method()==='POST'&&req.url().endsWith('/api/city/v1/trips'))creates++})
 await page.goto('/food/chaoliangfen');await page.getByRole('button',{name:'加入素材篮：炒凉粉',exact:true}).click()
 await expect(page.getByRole('button',{name:'加入素材篮：炒凉粉',exact:true})).toBeEnabled();expect(creates).toBe(0)
 await page.unroute('**/api/city/v1/trips/current')
 await page.getByRole('button',{name:'加入素材篮：炒凉粉',exact:true}).click();await expect(page.getByRole('button',{name:'移出素材篮：炒凉粉',exact:true})).toBeVisible()
 let release!:()=>void;const gate=new Promise<void>(resolve=>release=resolve)
 await page.route('**/exploration/constraints',async route=>{await gate;await route.continue()})
 await page.goto('/assistant?context=city&plan=basket')
 await expect(page.getByRole('status').filter({hasText:'正在接上你的安排'})).toBeVisible();await expect(page.getByRole('button',{name:'生成安排',exact:true})).toHaveCount(0)
 release();await expect(page.getByText('你打算哪天去？',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'明天',exact:true})).toBeEnabled()
})

test('手机从素材接着问缺项，同一输入框回答、逐步保存，刷新不重问',async({page})=>{
 await page.setViewportSize({width:465,height:769})
 let agentCalls=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/agent/tasks'))agentCalls++})
 await page.goto('/scenic/qingming');await page.getByRole('button',{name:'加入素材篮：清明上河园',exact:true}).click();await expect(page.getByRole('button',{name:'移出素材篮：清明上河园',exact:true})).toBeVisible()
 const trip=(await(await page.request.get('/api/city/v1/trips/current')).json()).trip,path='/api/city/v1/trips/'+trip.id
 await page.goto('/basket');await page.getByRole('link',{name:'交给小汴梁安排',exact:true}).click()
 await expect(page.locator('.planning-materials')).toContainText('清明上河园')
 await expect(page.getByRole('button',{name:'用素材篮规划',exact:true})).toHaveCount(0)
 await expect(page.getByLabel('旅行日期',{exact:true})).toBeVisible();await expect(page.getByLabel('开始时间',{exact:true})).toHaveCount(0)
 const composer=page.getByLabel('告诉小汴梁你的问题或想调整的安排',{exact:true})
 await composer.fill('2026-11-01');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByText('当天几点开始，最晚几点结束？',{exact:true})).toBeVisible()
 await page.reload();await expect(page.getByLabel('旅行日期',{exact:true})).toHaveCount(0)
 await expect(page.getByLabel('已提供的安排')).toContainText('2026-11-01')
 await composer.fill('09:00到19:00');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByText('从哪里出发？',{exact:true})).toBeVisible();await page.reload()
 await expect(page.getByLabel('开始时间',{exact:true})).toHaveCount(0)
 await page.getByRole('combobox',{name:'出发地点',exact:true}).selectOption('test:gate');await page.getByRole('button',{name:'继续',exact:true}).click()
 await expect(page.getByText('最后回到哪里？',{exact:true})).toBeVisible()
 await page.getByRole('combobox',{name:'返程地点',exact:true}).selectOption('test:hotel');await page.getByRole('button',{name:'继续',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('信息不足')
 expect(agentCalls).toBe(2)
 const saved=await(await page.request.get(path+'/exploration/constraints')).json()
 expect(saved.constraints.business_date).toBe('2026-11-01');expect(saved.constraints.origin_place_id).toBe('test:gate')
 const formal=await(await page.request.get(path)).json();expect(formal.version).toBe(trip.version);expect(formal.route_version).toBe(0);expect(formal.draft.stops).toEqual([])
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
})

test('明确日期时段和节奏可以一次回答，无效时段不写入条件',async({page})=>{
 await page.goto('/food/chaoliangfen');await page.getByRole('button',{name:'加入素材篮：炒凉粉',exact:true}).click()
 await expect(page.getByRole('button',{name:'移出素材篮：炒凉粉',exact:true})).toBeVisible()
 await page.goto('/assistant?context=city&plan=basket')
 const input=page.getByLabel('告诉小汴梁你的问题或想调整的安排',{exact:true})
 await input.fill('2030-05-01，下午6点到上午9点');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.locator('.agent-messages')).toContainText('结束时间要晚于出发时间')
 const trip=(await(await page.request.get('/api/city/v1/trips/current')).json()).trip,path='/api/city/v1/trips/'+trip.id
 expect((await(await page.request.get(path+'/exploration/constraints')).json()).version).toBe(0)
 await input.fill('2030-05-01，上午9点到下午6点，轻松一点');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByText('从哪里出发？',{exact:true})).toBeVisible()
 await page.reload();await expect(page.getByLabel('旅行日期',{exact:true})).toHaveCount(0);await expect(page.getByLabel('开始时间',{exact:true})).toHaveCount(0)
 const saved=(await(await page.request.get(path+'/exploration/constraints')).json()).constraints
 expect(saved.pace).toBe('relaxed');expect(saved.starts_at).toContain('T09:00:00');expect(saved.ends_at).toContain('T18:00:00')
 expect((await(await page.request.get(path)).json()).route_version).toBe(0)
})

test('没有素材时完整日期表达经过后端理解接上规划，等待初始化后保存',async({page})=>{
 let tasks=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/agent/tasks'))tasks++})
 await page.goto('/assistant?context=city')
 const input=page.getByLabel('告诉小汴梁你的问题或想调整的安排',{exact:true})
 await expect(input).toBeEnabled()
 await expect(page.getByLabel('素材篮规划协商')).toHaveCount(0)
 await input.fill('我明天想去开封玩');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.locator('.agent-messages')).toContainText('日期已记为')
 await expect(page.getByText('当天几点开始，最晚几点结束？',{exact:true})).toBeVisible()
 const trip=(await(await page.request.get('/api/city/v1/trips/current')).json()).trip
 const saved=(await(await page.request.get('/api/city/v1/trips/'+trip.id+'/exploration/constraints')).json()).constraints
 expect(saved.business_date).toBeTruthy();expect(saved.starts_at).toBeNull();expect(trip.route_version).toBe(0);expect(tasks).toBe(1)
})

test('缺地点资料时可填写起终点，刷新保留且不生成正式路线',async({page})=>{
 await page.setViewportSize({width:465,height:769})
 await page.route('**/api/city/v1/facts',r=>r.fulfill({json:{items:[]}}))
 let tasks=0,confirms=0;const understood:string[]=[]
 page.on('response',async r=>{if(r.url().endsWith('/step')){const data=await r.json();if(data.mode==='planning'&&data.interpretation?.source==='mock_model')understood.push(data.status)}})
 page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/agent/tasks'))tasks++;if(r.method()==='POST'&&r.url().endsWith('/engine/confirm'))confirms++})
 await page.goto('/food/chaoliangfen');await page.getByRole('button',{name:'加入素材篮：炒凉粉',exact:true}).click();await expect(page.getByRole('button',{name:'移出素材篮：炒凉粉',exact:true})).toBeVisible()
 const trip=(await(await page.request.get('/api/city/v1/trips/current')).json()).trip,path='/api/city/v1/trips/'+trip.id
 await page.goto('/assistant?context=city&plan=basket')
 const input=page.getByLabel('告诉小汴梁你的问题或想调整的安排',{exact:true})
 const send=async(text:string)=>{await input.fill(text);await page.getByRole('button',{name:'发送',exact:true}).click();await expect(input).toBeEnabled()}
 await send('就这样');await expect(page.locator('.agent-messages')).toContainText('你打算哪天去？')
 await send('2030-05-01，上午9点到下午6点')
 await expect(page.getByText('从哪里出发，最后返回哪里？',{exact:true})).toBeVisible()
 await send('就这样');await expect(page.locator('.agent-messages')).toContainText('从哪里出发，最后返回哪里？')
 for(const text of ['我想明天去开封玩','我明天想去开封玩','明天我想去开封玩'])await send(text)
 const tomorrow=await page.evaluate(()=>{const d=new Date(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+1);return d.toISOString().slice(0,10)})
 await expect(page.getByLabel('已提供的安排')).toContainText(tomorrow)
 await expect(page.locator('.agent-messages')).toContainText('日期已记为 '+tomorrow)
 await expect(page.locator('.agent-messages')).not.toContainText('请明确要调整的活动或条件')
 const workspace=(await(await page.request.get(path+'/exploration/constraints')).json()).constraints
 expect(workspace.business_date).toBe(tomorrow);expect(workspace.starts_at).toBe(tomorrow+'T09:00:00+08:00');expect(workspace.ends_at).toBe(tomorrow+'T18:00:00+08:00')
 await page.getByLabel('出发地点',{exact:true}).fill('我住的酒店')
 await page.getByLabel('返回地点',{exact:true}).fill('开封站')
 await page.screenshot({path:'../docs/screenshots/endpoint-input-mobile.png',fullPage:true})
 await page.locator('.planning-question').getByRole('button',{name:'继续',exact:true}).click()
 await expect(page.getByLabel('已提供的安排')).toContainText('从我住的酒店')
 await expect(page.getByLabel('规划检测结果')).toContainText('位置资料待核验')
 const reported=(await(await page.request.get(path+'/exploration/constraints')).json()).constraints
 expect(reported.origin_text).toBe('我住的酒店');expect(reported.destination_text).toBe('开封站')
 expect(reported.origin_place_id).toBeNull();expect(reported.destination_place_id).toBeNull()
 await page.reload();await expect(page.getByLabel('已提供的安排')).toContainText(tomorrow)
 await expect(page.getByLabel('已提供的安排')).toContainText('回到开封站')
  await expect(page.locator('.planning-question')).toHaveCount(0)
  await expect(page.getByLabel('规划检测结果')).toContainText('位置资料待核验')
 const formal=await(await page.request.get(path)).json();expect(formal.version).toBe(trip.version);expect(formal.route_version).toBe(0);expect(formal.draft.stops).toEqual([])
 expect(tasks).toBe(6);expect(confirms).toBe(0);expect(understood.filter(status=>status!=='running')).toEqual(Array(6).fill('needs_clarification'))
})

test('结构化规划输入必须属于当前旅程，相同版本也不能串写',async({page})=>{
 await page.request.post('/api/v1/sessions')
 const create=async()=>await(await page.request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft:{}}})).json()
 const first=await create(),other=await create()
 await page.route('**/agent/tasks/*/step',async route=>{
  const response=await route.fetch(),data=await response.json()
  if(data.result?.planning_workflow)data.result.planning_workflow.trip_id=other.id
  await route.fulfill({response,json:data})
 })
 await page.goto('/assistant?context=city&city_trip='+first.id)
 const input=page.getByLabel('告诉小汴梁你的问题或想调整的安排',{exact:true})
 await expect(input).toBeEnabled();await input.fill('我明天想去开封玩')
 await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByRole('alert')).toContainText('安排已变化，请重新读取当前旅程。')
 expect((await(await page.request.get('/api/city/v1/trips/'+first.id+'/exploration/constraints')).json()).version).toBe(1)
 expect((await(await page.request.get('/api/city/v1/trips/'+other.id+'/exploration/constraints')).json()).version).toBe(0)
})
