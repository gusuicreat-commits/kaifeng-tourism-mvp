import {test,expect,type Page} from '@playwright/test'

// E26：景区页节目可单独加入素材篮；匹配不到资料库节目时不显示按钮。
async function mockMatches(page:Page,known:Record<string,string>){
 const asked:string[][]=[]
 await page.route('**/api/city/v1/scenic/*/programs/match?*',route=>{
  const names=new URL(route.request().url()).searchParams.getAll('name');asked.push(names)
  return route.fulfill({json:{matches:Object.fromEntries(names.map(n=>[n,known[n]??null]))}})
 })
 return asked
}

async function mockBasket(page:Page){
 const sent:Record<string,unknown>[]=[]
 await page.route('**/api/city/v1/trips/*/basket',async route=>{
  if(route.request().method()!=='PATCH')return route.continue()
  const body=route.request().postDataJSON();sent.push(body)
  const url=new URL(route.request().url()),trip=url.pathname.split('/')[5]
  const items=body.operation==='remove'?[]:[{key:body.item.kind+':'+body.item.object_id,kind:body.item.kind,id:body.item.object_id,name:'大宋·东京保卫战',
   fact_kind:'program',fact_id:body.item.object_id,priority:body.item.priority||'want',status:'pending',source:'detail'}]
  return route.fulfill({json:{trip_id:trip,version:(body.expected_version||0)+1,items}})
 })
 return sent
}

test('主要演出卡片：匹配到的节目可加入、移出素材篮，未匹配的不显示按钮',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 const asked=await mockMatches(page,{'大宋·东京保卫战':'prg_test_dongjing'})
 const sent=await mockBasket(page)
 await page.goto('/scenic/qingming')
 const card=page.locator('#scenic-shows .detail-show').filter({hasText:'大宋·东京保卫战'})
 const add=card.getByRole('button',{name:'加入素材篮：大宋·东京保卫战',exact:true})
 await expect(add).toBeVisible()
 expect(asked.flat()).toContain('大宋·东京保卫战')
 // 只有匹配到的那一个节目有按钮
 await expect(page.locator('#scenic-shows .detail-show .add-place')).toHaveCount(1)
 await add.click()
 await expect(card.getByRole('button',{name:'移出素材篮：大宋·东京保卫战',exact:true})).toBeVisible()
 expect(sent.at(-1)).toMatchObject({operation:'put',item:{kind:'program',object_id:'prg_test_dongjing'}})
 await card.getByRole('button',{name:'移出素材篮：大宋·东京保卫战',exact:true}).click()
 await expect(add).toBeVisible()
 expect(sent.at(-1)).toMatchObject({operation:'remove'})
 for(const width of [320,390]){
  await page.setViewportSize({width,height:844});await card.scrollIntoViewIfNeeded()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 }
 expect(errors).toEqual([])
})

test('匹配接口失败时节目卡片照常显示，只是不出现加入按钮',async({page})=>{
 await page.route('**/api/city/v1/scenic/*/programs/match?*',route=>route.fulfill({status:500,json:{detail:'服务暂不可用'}}))
 await page.goto('/scenic/wansui')
 await expect(page.locator('#scenic-shows .detail-show')).toHaveCount(4)
 await expect(page.locator('#scenic-shows .add-place')).toHaveCount(0)
 await expect(page.locator('[role=alert]')).toHaveCount(0)
})

test('当日节目表每个节目下也能加入素材篮',async({page})=>{
 await mockMatches(page,{'大宋·东京保卫战':'prg_test_dongjing'})
 const sent=await mockBasket(page)
 await page.route('**/api/scenic/parks/qmsy/performances?*',route=>{
  const date=new URL(route.request().url()).searchParams.get('date')||''
  return route.fulfill({json:{park_id:'qmsy',service_date:date,approved_schedule:true,status:'approved_schedule',display_status:'official_schedule_reviewed',
   latest_review_status:'approved_schedule',notice:'合成测试节目表。',source_updated_on:date,last_fetched_at:null,
   source:{source_ref:'https://example.invalid/synthetic-schedule',source_updated_on:date,fetched_at:''},
   performances:[['大宋·东京保卫战',1],['岳飞枪挑小梁王',2]].map(([name,id])=>({performance_id:id,program_name:name,venue_name:'合成场地',area:null,
    start_time:'15:00:00',duration_minutes:null,end_time:null,entry_deadline:null,requires_booking:null,weather_sensitive:null,schedule_note:'',
    verified_poi_id:null,eligible_for_solver:false,source_freshness:'approved_schedule',operating_status:'scheduled'}))}})
 })
 await page.goto('/scenic/qingming')
 const section=page.locator('.scenic-schedule')
 const program=section.locator('.schedule-program').filter({hasText:'大宋·东京保卫战'})
 await expect(program.getByRole('button',{name:'加入素材篮：大宋·东京保卫战',exact:true})).toBeVisible()
 await expect(section.locator('.schedule-program').filter({hasText:'岳飞枪挑小梁王'}).locator('.add-place')).toHaveCount(0)
 await program.getByRole('button',{name:'加入素材篮：大宋·东京保卫战',exact:true}).click()
 // 两处按钮共用同一素材状态
 await expect(page.getByRole('button',{name:'移出素材篮：大宋·东京保卫战',exact:true}).first()).toBeVisible()
 expect(sent.at(-1)).toMatchObject({operation:'put',item:{kind:'program',object_id:'prg_test_dongjing'}})
})

test('页头素材篮入口显示收藏数量并进入素材篮',async({page})=>{
 await mockMatches(page,{'大宋·东京保卫战':'prg_test_dongjing'})
 await mockBasket(page)
 for(const width of [390,1440]){
  await page.setViewportSize({width,height:844})
  await page.goto('/scenic/qingming')
  const entry=page.locator('header .basket-entry')
  await expect(entry).toBeVisible()
  await expect(entry).toHaveAttribute('aria-label','旅行素材篮，还没有收藏')
  await expect(entry.locator('.basket-count')).toHaveCount(0)
  await page.getByRole('button',{name:'加入素材篮：大宋·东京保卫战',exact:true}).click()
  await expect(entry).toHaveAttribute('aria-label','旅行素材篮，已收藏 1 项')
  await expect(entry.locator('.basket-count')).toHaveText('1')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await entry.click()
  await expect(page).toHaveURL(/\/basket$/)
  await expect(entry).toHaveAttribute('aria-current','page')
  await page.context().clearCookies();await page.evaluate(()=>sessionStorage.clear())
 }
})
