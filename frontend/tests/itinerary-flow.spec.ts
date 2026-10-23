import {test,expect,type Page} from '@playwright/test'

// Display-only synthetic snapshots. Never import these into the runtime database.
function fixture(){
 const at=(value:string)=>`2030-05-01T${value}:00+08:00`
 const activities=[
  {goal_id:'visit',place_id:'museum',kind:'visit',starts_at:at('09:10'),departs_at:at('10:10'),duration_seconds:3600,required:true,session_id:null},
  {goal_id:'show',place_id:'stage',kind:'show',starts_at:at('10:30'),departs_at:at('11:00'),duration_seconds:1800,required:true,session_id:'show-session'},
  {goal_id:'meal',place_id:'restaurant',kind:'dining',starts_at:at('11:15'),departs_at:at('12:00'),duration_seconds:2700,required:false,session_id:null},
  {goal_id:'rest',place_id:'garden',kind:'rest',starts_at:at('12:20'),departs_at:at('12:40'),duration_seconds:1200,required:false,session_id:null},
 ]
 const leg=(origin:string,destination:string,from:string,to:string,mode='walking')=>({departs_at:at(from),arrives_at:at(to),journey:{travel:{origin_place_id:origin,destination_place_id:destination,mode}}})
 const trip={id:'00000000-0000-4000-8000-000000000001',schema_version:'city-state/1',status:'DRAFT',version:2,route_version:1,updated_at:at('08:00'),anchor:null,
  draft:{business_date:'2030-05-01',starts_at:at('09:00'),ends_at:at('18:00'),origin_place_id:'station',destination_place_id:'station',interests:[],dish_interest_ids:[],modes:['walking'],
   stops:activities.map(a=>({stop_id:a.goal_id,place_id:a.place_id,kind:a.kind,session_id:a.session_id,locked:a.required,duration_seconds:a.duration_seconds,progress:'pending',actual_start:null as string|null,actual_end:null as string|null}))},
  readiness:{missing_fields:[],issues:[],planning_available:false,notice:''}}
 const route={trip_id:trip.id,version:2,route_version:1,stale:false,itinerary:{activities,ongoing:null as any,
  legs:[leg('station','museum','09:00','09:10'),leg('museum','stage','10:10','10:25'),leg('stage','restaurant','11:00','11:15'),leg('restaurant','garden','12:00','12:20'),leg('garden','station','12:40','13:00','driving')],
  returns_at:at('13:00'),total_seconds:14400,travel_seconds:4800,walking_distance_m:2200,known_cost_upper_fen:0,cost_unknown:true,evidence_mode:'synthetic'}}
 const items=[['station','演示起终点'],['museum','示例景点'],['stage','示例舞台'],['restaurant','示例餐饮地点'],['garden','示例休息地点']].map(([fact_id,name])=>({record:{kind:'place',fact_id,name},ready:true,issues:[]}))
 items.push({record:{kind:'show_session',fact_id:'show-session',program_id:'program',name:'演示场次'} as any,ready:true,issues:[]},{record:{kind:'program',fact_id:'program',name:'示例演出'} as any,ready:true,issues:[]})
 return {trip,route,items}
}
async function display(page:Page,data:ReturnType<typeof fixture>){
 await page.route('**/api/city/v1/facts',r=>r.fulfill({json:{items:data.items}}))
 await page.route('**/api/city/v1/trips/current',r=>r.fulfill({json:{trip:data.trip}}))
 await page.route(`**/api/city/v1/trips/${data.trip.id}/basket`,r=>r.fulfill({json:{version:0,items:[]}}))
 await page.route(`**/api/city/v1/trips/${data.trip.id}/engine/current`,r=>r.fulfill({json:data.route}))
 await page.goto('/city-trip')
}

test('确认路线按起点、活动、分段交通和返程连接，手机及桌面可读',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 await page.setViewportSize({width:465,height:769});await display(page,fixture())
 const flow=page.getByRole('list',{name:'行程流程安排'})
 await expect(flow.locator('.flow-point')).toHaveCount(6)
 await expect(flow.locator('h3')).toHaveText(['演示起终点','示例景点','示例演出','示例餐饮地点','示例休息地点','演示起终点'])
 await expect(flow.locator('.flow-transfer')).toHaveText(['步行约 10 分钟','步行约 15 分钟','步行约 15 分钟','步行约 20 分钟','驾车约 20 分钟'])
 await expect(flow.locator('.flow-activity .timeline-clock')).toHaveText(['09:10','10:30','11:15','12:20'])
 await expect(flow.locator('.flow-point').last()).toContainText('13:00')
 await expect(flow.locator('[aria-current=step]')).toContainText('示例景点')
 await expect(page.locator('.trip-evidence-note')).toHaveText('演示路线')
 for(const width of [320,465,1200]){
  await page.setViewportSize({width,height:769})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  const boxes=await flow.locator('.flow-card').evaluateAll(els=>els.map(e=>{const b=e.getBoundingClientRect();return {x:b.x,y:b.y,right:b.right,width:b.width}}))
  expect(boxes.every((b,i)=>b.x>=0&&b.right<=width&&b.width>120&&(i===0||b.y>boxes[i-1].y))).toBe(true)
 }
 await page.setViewportSize({width:465,height:769})
 await flow.scrollIntoViewIfNeeded()
 await page.screenshot({path:'../docs/screenshots/itinerary-flow-synthetic-mobile.png',fullPage:true})
 await flow.screenshot({path:'../docs/screenshots/itinerary-flow-chain-synthetic.png',style:'.site-header nav,.global-assistant-launcher{visibility:hidden!important}'})
 await flow.getByRole('link',{name:'查看位置'}).first().click()
 await expect(page).toHaveURL(/guide\?mode=city&place=museum/)
 expect(errors).toEqual([])
})

test('重新规划保留完成记录和正在进行的活动，不把历史转场当新路段',async({page})=>{
 const d=fixture();d.trip.status='ACTIVE'
 d.trip.draft.stops[0].progress='completed';d.trip.draft.stops[0].actual_start='2030-05-01T09:12:00+08:00';d.trip.draft.stops[0].actual_end='2030-05-01T10:09:00+08:00'
 d.route.itinerary.ongoing={stop_id:'show',place_id:'stage',ends_at:'2030-05-01T11:00:00+08:00',original_activity:d.route.itinerary.activities[1]}
 d.route.itinerary.activities=d.route.itinerary.activities.slice(2);d.route.itinerary.legs=d.route.itinerary.legs.slice(2)
 await display(page,d);const flow=page.getByRole('list',{name:'行程流程安排'})
 await expect(flow.locator('h3')).toHaveText(['演示起终点','示例景点','示例演出','示例餐饮地点','示例休息地点','演示起终点'])
 await expect(flow.locator('.completed')).toContainText('09:12')
 await expect(flow.locator('.ongoing')).toContainText('正在进行')
 await expect(flow.locator('[aria-current=step]')).toHaveCount(1)
 await expect(flow).not.toContainText('步行约 10 分钟')
 await expect(flow.locator('.flow-transfer').nth(2)).toContainText('步行约 15 分钟')
})

for(const state of ['unconfirmed','stale','expired','wrong-version','missing-leg'])test(`链式展示遵守${state}资料状态`,async({page})=>{
 const d=fixture()
 if(state==='unconfirmed'){d.trip.route_version=0;d.route.itinerary=null as any}
 if(state==='stale')d.route.stale=true
 if(state==='expired')d.route.itinerary.returns_at='2000-01-01T13:00:00+08:00'
 if(state==='wrong-version')d.route.version=1
 if(state==='missing-leg')d.route.itinerary.legs=[]
 await display(page,d)
 if(state==='missing-leg'){
  await expect(page.locator('.journey-flow')).toBeVisible()
  await expect(page.locator('.flow-transfer').first()).toHaveText('转场资料待核验')
  await expect(page.locator('.journey-flow')).not.toContainText('约 0 分钟')
 }else{
  await expect(page.locator('.journey-timeline')).toBeVisible()
  await expect(page.locator('.journey-flow')).toHaveCount(0)
  await expect(page.locator('.journey-next-card')).not.toContainText('预计转场')
 }
})
