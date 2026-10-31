import {test,expect,type Page,type TestInfo} from '@playwright/test'
import {readFileSync} from 'node:fs'
import {fileURLToPath} from 'node:url'
import type {Basket} from '../src/basketApi'
import type {CityTripState} from '../src/cityApi'
import {draftDays} from '../src/days'
import type {EnginePreview,MultiDayPlanningConstraints,MultiDayItinerary,PlanningConstraints,PlanningResult,PlanningWorkspace,RouteSnapshot} from '../src/explorationApi'
import {scenic,food,endpoints} from '../playwright.full-flow.fixture.mjs'

const cityFacts=JSON.parse(readFileSync(fileURLToPath(new URL('../../backend/tests/fixtures/city_facts.json',import.meta.url)),'utf8')) as {records:Array<{kind:string;fact_id:string;name?:string}>}
const nightPlaces=['pl_wb_000003','pl_wb_vendor_000001','pl_wb_000013']
const hasNightRoute=nightPlaces.every(id=>cityFacts.records.some(r=>r.kind==='place'&&r.fact_id===id))
const prefix=(id:string)=>'/api/city/v1/trips/'+id
const form=(page:Page)=>page.locator('.planning-question')

async function read<T>(page:Page,path:string):Promise<T>{
 const response=await page.request.get(path)
 expect(response.ok(),await response.text()).toBeTruthy()
 return response.json()
}
async function current(page:Page){
 const {trip}=await read<{trip:CityTripState|null}>(page,'/api/city/v1/trips/current')
 expect(trip,'页面操作应已创建当前旅程').toBeTruthy()
 return trip!
}
async function shot(page:Page,info:TestInfo,step:string){
 const id=info.annotations.find(a=>a.type==='full-flow')!.description
 await page.screenshot({path:`../.test-results/full-flow/shots/full-flow-${id}-${step}.png`,fullPage:true})
}
async function home(page:Page,info:TestInfo,id:string){
 info.annotations.push({type:'full-flow',description:id})
 await page.goto('/')
 await expect(page.getByRole('heading',{name:'发现开封',exact:true})).toBeAttached()
 await expect(page.getByRole('link',{name:'规划我的开封之旅',exact:true})).toBeVisible()
 await shot(page,info,'home')
}
async function addFood(page:Page){
 await page.getByRole('button',{name:'美食',exact:true}).click()
 await page.locator(`.food-card a[href="/food/${food.id}"]`).click()
 await page.getByRole('button',{name:'加入素材篮：'+food.name,exact:true}).click()
 await expect(page.getByRole('button',{name:'移出素材篮：'+food.name,exact:true})).toBeVisible()
}
async function openBasketPlanning(page:Page,info:TestInfo,names:string[]){
 await page.getByRole('link',{name:'汴梁游记首页',exact:true}).click()
 await page.locator('.selection-basket').getByRole('link',{name:'旅行素材篮',exact:true}).click()
 await expect(page.locator('.basket-item')).toHaveCount(names.length)
 for(const name of names)await expect(page.locator('.basket-item').filter({hasText:name})).toBeVisible()
 const trip=await current(page),basket=await read<Basket>(page,prefix(trip.id)+'/basket')
 expect(basket.items.map(i=>i.name).sort()).toEqual([...names].sort())
 expect(basket.items.find(i=>i.id===food.id)).toMatchObject({fact_id:food.fact_id})
 await shot(page,info,'basket')
 await page.getByRole('link',{name:'交给小汴梁安排',exact:true}).click()
 await expect(form(page).getByRole('button',{name:'明天',exact:true})).toBeEnabled()
 return trip
}
async function fillDay(page:Page,info:TestInfo,days=1){
 const question=form(page)
 const tomorrow=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(Date.now()+86400000))
 await question.getByRole('button',{name:'明天',exact:true}).click()
 await expect(page.getByLabel('已提供的安排',{exact:true})).toContainText(tomorrow)
 await question.getByRole('button',{name:days+'天',exact:true}).click()
 await question.getByLabel('开始时间',{exact:true}).fill('09:00')
 await question.getByLabel('最晚结束',{exact:true}).fill('20:00')
 await question.getByRole('button',{name:'继续',exact:true}).click()
 await question.getByRole('combobox',{name:'出发地点',exact:true}).selectOption(days>1?endpoints.destination_place_id:endpoints.origin_place_id)
 await question.getByRole('button',{name:'继续',exact:true}).click()
 await question.getByRole('combobox',{name:'返程地点',exact:true}).selectOption(endpoints.destination_place_id)
 await question.getByRole('button',{name:'继续',exact:true}).click()
 await question.getByRole('button',{name:'2 人',exact:true}).click()
 await expect(question).toContainText('想怎么安排？')
 await shot(page,info,'basics')
}
async function preferences(page:Page,info:TestInfo,pace:'轻松'|'紧凑'){
 const question=form(page)
 await expect(question).toContainText('最后几个问题，帮我更懂你')
 for(const name of ['游玩节奏','景点之间怎么走','更在意什么'])await expect(question.getByRole('group',{name,exact:true})).toBeVisible()
 await question.getByRole('button',{name:pace,exact:true}).click()
 await question.getByRole('button',{name:'公交地铁',exact:true}).click()
 await question.getByRole('button',{name:'少走路',exact:true}).click()
 await shot(page,info,'preferences')
 await question.getByRole('button',{name:'继续',exact:true}).click()
 await expect(question.getByRole('button',{name:'生成安排',exact:true})).toBeEnabled()
}
async function preview(page:Page,info:TestInfo,action:()=>Promise<unknown>,step:string){
 const waiting=page.waitForResponse(r=>r.request().method()==='POST'&&/\/exploration\/preview$/.test(r.url()))
 await action()
 const response=await waiting
 expect(response.ok(),await response.text()).toBeTruthy()
 const request=response.request().postDataJSON() as {constraints:PlanningConstraints}
 const result=await response.json() as PlanningResult
 await info.attach(step,{body:JSON.stringify({request,result},null,2),contentType:'application/json'})
 expect(request.constraints.starts_at).toBe(request.constraints.business_date+'T09:00:00+08:00')
 expect(request.constraints.ends_at).toBe(request.constraints.business_date+'T20:00:00+08:00')
 const assessment=page.getByLabel('规划检测结果',{exact:true})
 await expect(assessment).toBeVisible()
 await shot(page,info,step)
 expect(result.status,await assessment.innerText()).toBe('feasible')
 expect(result.planning?.candidate_id).toBeTruthy()
 expect(result.planning?.result.itineraries.length).toBeGreaterThan(0)
 await expect(assessment.locator('.plan-option').first()).toBeVisible()
 await expect(assessment.getByRole('button',{name:/^确认方案 /})).toBeEnabled()
 await expect(page.getByRole('group',{name:'游玩节奏',exact:true})).toHaveCount(0)
 return {request,result}
}
async function quick(page:Page,info:TestInfo){
 await page.getByRole('link',{name:'规划我的开封之旅',exact:true}).click()
 await expect(form(page)).toContainText('素材篮还是空的')
 await shot(page,info,'empty-basket')
 await form(page).getByRole('button',{name:'用推荐的开封夜游路线',exact:true}).click()
 if(!hasNightRoute){
  await expect(page.getByRole('alert').filter({hasText:'对象资料待添加，不能加入未知对象。'})).toBeVisible()
  await shot(page,info,'recommendation-error')
  info.annotations.push({type:'fixture-fallback',description:'缺少推荐夜游三个地点；先断言错误，再通过发现页加入炒凉粉（test:food → test:restaurant）。'})
  await form(page).getByRole('link',{name:'先去发现开封',exact:true}).click()
  await addFood(page)
  await openBasketPlanning(page,info,[food.name])
 }else await expect(form(page).getByRole('button',{name:'明天',exact:true})).toBeEnabled()
 await fillDay(page,info)
 const result=await preview(page,info,()=>page.getByRole('group',{name:'选择互动模式',exact:true}).getByRole('button',{name:/^快速安排/}).click(),'quick-candidate')
 expect(result.request.constraints).toMatchObject({...endpoints,pace:'standard',objective:'balanced',party_size:2,modes:['walking']})
 return result
}
async function confirm(page:Page,info:TestInfo,trip:CityTripState,placeIds:string[]){
 const before=await read<CityTripState>(page,prefix(trip.id))
 expect(before.route_version).toBe(0)
 expect(before.version).toBe(trip.version)
 const waiting=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(prefix(trip.id)+'/engine/confirm'))
 await page.getByLabel('规划检测结果',{exact:true}).getByRole('button',{name:/^确认方案 /}).click()
 const response=await waiting
 expect(response.ok(),await response.text()).toBeTruthy()
 await expect(page.getByRole('status').filter({hasText:'已确认并更新正式路线'})).toBeVisible()
 await shot(page,info,'confirmed')
 await page.locator('.planning-success').getByRole('link',{name:'查看行程',exact:true}).click()
 await expect(page.getByRole('heading',{name:'我的开封游记',exact:true})).toBeVisible()
 const saved=await read<CityTripState>(page,prefix(trip.id)),route=await read<RouteSnapshot>(page,prefix(trip.id)+'/engine/current')
 expect(saved.route_version).toBe(1)
 expect(saved.version).toBeGreaterThan(before.version)
 expect(route).toMatchObject({trip_id:trip.id,version:saved.version,route_version:1,stale:false})
 expect(route.itinerary).toBeTruthy()
 const stops=draftDays(saved.draft).flatMap(d=>d.stops).filter(s=>placeIds.includes(s.place_id))
 expect([...new Set(stops.map(s=>s.place_id))].sort(),'已保存的首选方案应包含素材篮中的地点和菜品供应商').toEqual([...placeIds].sort())
 const flow=page.getByRole('list',{name:'行程流程安排',exact:true})
 await expect(flow).toBeVisible()
 const place=cityFacts.records.find(r=>r.kind==='place'&&r.fact_id===stops[0].place_id)
 if(place?.name)await expect(flow).toContainText(place.name)
 await shot(page,info,'itinerary')
 return read<PlanningWorkspace>(page,prefix(trip.id)+'/exploration/constraints')
}

test.describe('完整规划流程：真实后端与合成资料',()=>{
 test.setTimeout(60000)
 test.afterEach(async({page},info)=>{
  if(info.status!==info.expectedStatus){
   await shot(page,info,'failed')
   await info.attach('页面停留位置',{body:JSON.stringify({url:page.url(),text:await page.locator('body').innerText()},null,2),contentType:'application/json'})
  }
 })

 for(const viewport of [{name:'desktop',width:1440,height:1000},{name:'mobile',width:390,height:844}])test(`1 深度互动全流程 ${viewport.name}`,async({page},info)=>{
  await page.setViewportSize({width:viewport.width,height:viewport.height})
  await home(page,info,'1-'+viewport.name)
  await page.getByRole('button',{name:'景点',exact:true}).click()
  await page.locator(`.scenic-card a[href="/scenic/${scenic.id}"]`).click()
  await page.getByRole('button',{name:'加入素材篮：'+scenic.name,exact:true}).click()
  await expect(page.getByRole('button',{name:'移出素材篮：'+scenic.name,exact:true})).toBeVisible()
  await shot(page,info,'scenic-detail')
  await page.getByRole('link',{name:'汴梁游记首页',exact:true}).click()
  await addFood(page)
  const trip=await openBasketPlanning(page,info,[scenic.name,food.name])
  await fillDay(page,info)
  await form(page).getByRole('button',{name:/^深度互动/}).click()
  await preferences(page,info,'轻松')
  const candidate=await preview(page,info,()=>form(page).getByRole('button',{name:'生成安排',exact:true}).click(),'deep-candidate')
  expect(candidate.request.constraints).toMatchObject({...endpoints,pace:'relaxed',objective:'less_walking',party_size:2,modes:['transit']})
  const saved=await confirm(page,info,trip,[scenic.fact_id,food.place_id])
  expect(saved.constraints).toMatchObject({pace:'relaxed',objective:'less_walking',party_size:2})
 })

 test('2 跳过素材篮，快速安排直接出候选并确认',async({page},info)=>{
  await home(page,info,'2-desktop')
  await quick(page,info)
  const trip=await current(page)
  const saved=await confirm(page,info,trip,hasNightRoute?nightPlaces:[food.place_id])
  expect(saved.constraints).toMatchObject({pace:'standard',objective:'balanced',party_size:2})
 })

 test('3 快速候选中切换深度互动，重新生成并保持对话模式',async({page},info)=>{
  await home(page,info,'3-desktop')
  const original=await quick(page,info),trip=await current(page)
  await page.getByLabel('已提供的安排',{exact:true}).getByRole('button',{name:'快速安排',exact:true}).click()
  await preferences(page,info,'紧凑')
  const revised=await preview(page,info,()=>form(page).getByRole('button',{name:'生成安排',exact:true}).click(),'compact-candidate')
  expect(revised.request.constraints.pace).toBe('compact')
  expect(revised.request.constraints.modes).toEqual(['transit'])
  expect(revised.result.planning_version).toBeGreaterThan(original.result.planning_version)
  expect((await read<CityTripState>(page,prefix(trip.id))).route_version).toBe(0)
  await confirm(page,info,trip,hasNightRoute?nightPlaces:[food.place_id])
  await page.locator('.journey-assistant-card').click()
  await expect(page.locator('#city-agent-input')).toBeEnabled()
  await expect(page.getByRole('group',{name:'互动模式',exact:true}).getByRole('button',{name:'深度互动',exact:true})).toHaveAttribute('aria-pressed','true')
  await shot(page,info,'dialogue-mode')
 })

 test('4 两天快速安排复用素材篮并确认整套方案',async({page},info)=>{
  await home(page,info,'4-multi-day')
  await addFood(page)
  const trip=await openBasketPlanning(page,info,[food.name])
  await fillDay(page,info,2)
  await expect(page.getByLabel('已提供的安排',{exact:true})).toContainText('2 天')
  const waiting=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/exploration/preview'))
  await form(page).getByRole('button',{name:/^快速安排/}).click()
  const response=await waiting
  expect(response.ok(),await response.text()).toBeTruthy()
  const request=response.request().postDataJSON() as {constraints:MultiDayPlanningConstraints},result=await response.json() as EnginePreview
  await info.attach('multi-day-candidate',{body:JSON.stringify({request,result},null,2),contentType:'application/json'})
  expect(request.constraints).toMatchObject({schema_version:'city-constraints/2',trip_days:2,party_size:2,pace:'standard',objective:'balanced',modes:['walking']})
  expect(request.constraints.days).toHaveLength(2)
  for(const d of request.constraints.days as {business_date:string;starts_at:string;ends_at:string}[])expect(d).toMatchObject({starts_at:d.business_date+'T09:00:00+08:00',ends_at:d.business_date+'T20:00:00+08:00',origin_place_id:endpoints.destination_place_id,destination_place_id:endpoints.destination_place_id})
  expect(request.constraints).not.toHaveProperty('goals');expect(request.constraints).not.toHaveProperty('overnights')
  expect(result.result.status).toBe('feasible')
  const plan=result.result.itineraries[0] as MultiDayItinerary
  expect(plan.schema_version).toBe('city-itinerary/2');expect(plan.days).toHaveLength(2)
  expect(plan.overnights).toHaveLength(1)
  expect(plan.days.flatMap(d=>d.itinerary.activities).map(a=>a.place_id)).toContain(food.place_id)
  const assessment=page.getByLabel('规划检测结果',{exact:true})
  await expect(assessment.getByLabel('整套行程方案',{exact:true})).toBeVisible()
  await expect(assessment.locator('.preview-day')).toHaveCount(2)
  await expect(page.getByRole('group',{name:'游玩节奏',exact:true})).toHaveCount(0)
  expect((await read<CityTripState>(page,prefix(trip.id))).route_version).toBe(0)
  await shot(page,info,'multi-day-candidate')
  const confirming=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(prefix(trip.id)+'/engine/confirm'))
  await assessment.getByRole('button',{name:'确认整套方案',exact:true}).click()
  const confirmed=await confirming
  expect(confirmed.ok(),await confirmed.text()).toBeTruthy()
  await expect(page.getByRole('status').filter({hasText:'已确认并更新正式路线'})).toBeVisible()
  const saved=await read<CityTripState>(page,prefix(trip.id)),route=await read<RouteSnapshot>(page,prefix(trip.id)+'/engine/current')
  expect(saved.route_version).toBe(1);expect(saved.version).toBeGreaterThan(trip.version)
  expect(route).toMatchObject({route_version:1,stale:false,itinerary:{schema_version:'city-itinerary/2',start_date:request.constraints.start_date,end_date:request.constraints.end_date}})
  expect(draftDays(saved.draft)).toHaveLength(2)
  expect(draftDays(saved.draft).flatMap(d=>d.stops).map(s=>s.place_id)).toContain(food.place_id)
  await page.locator('.planning-success').getByRole('link',{name:'查看行程',exact:true}).click()
  await expect(page.getByRole('heading',{name:'我的开封游记',exact:true})).toBeVisible()
  await shot(page,info,'multi-day-itinerary')
 })

})

test.describe('偏好条件：真实后端',()=>{
 test.setTimeout(60000)
 test('深度互动排除美食并显示原因，确认景区方案',async({page},info)=>{
  await home(page,info,'E13-preferences')
  await page.getByRole('button',{name:'景点',exact:true}).click()
  await page.locator(`.scenic-card a[href="/scenic/${scenic.id}"]`).click()
  await page.getByRole('button',{name:'加入素材篮：'+scenic.name,exact:true}).click()
  await page.getByRole('link',{name:'汴梁游记首页',exact:true}).click()
  await addFood(page)
  const trip=await openBasketPlanning(page,info,[scenic.name,food.name])
  await fillDay(page,info)
  await form(page).getByRole('button',{name:/^深度互动/}).click()
  await form(page).getByRole('group',{name:'不想安排（可多选）',exact:true}).getByRole('button',{name:'品美食',exact:true}).click()
  await expect(page.getByLabel('已提供的安排')).toContainText('不想安排：美食')
  await form(page).getByRole('button',{name:'继续',exact:true}).click()
  const candidate=await preview(page,info,()=>form(page).getByRole('button',{name:'生成安排',exact:true}).click(),'preference-candidate')
  expect(candidate.request.constraints.preferences?.avoid).toEqual(['food'])
  for(const plan of candidate.result.planning!.result.itineraries){
   expect('days' in plan).toBeFalsy()
   if(!('days' in plan))expect(plan.activities.map(a=>a.place_id)).not.toContain(food.place_id)
  }
  expect(candidate.result.unscheduled?.['0']).toEqual([expect.objectContaining({name:food.name,reason_code:'preference_excluded',reason:'你说过不想安排美食'})])
  await expect(page.locator('.plan-option').first()).toContainText('暂不安排：'+food.name+'（你说过不想安排美食）')
  const saved=await confirm(page,info,trip,[scenic.fact_id])
  expect(saved.constraints?.preferences?.avoid).toEqual(['food'])
  const route=await read<RouteSnapshot>(page,prefix(trip.id)+'/engine/current')
  expect(route.itinerary).toBeTruthy()
  const itinerary=route.itinerary!
  expect('days' in itinerary).toBeFalsy()
  if(!('days' in itinerary))expect(itinerary.activities.map(a=>a.place_id)).not.toContain(food.place_id)
 })
})
