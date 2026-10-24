import {test,expect,type Page} from '@playwright/test'

async function json(request:Page['request'],method:string,path:string,data?:Record<string,unknown>){
 const response=await request.fetch(path,{method,data:data?JSON.stringify(data):undefined,
  headers:{'Content-Type':'application/json'}})
 expect(response.ok(),await response.text()).toBeTruthy()
 return await response.json()
}

test('weather reminder renders and replan requires an explicit confirmation step',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 await json(page.request,'POST','/api/v1/sessions')
 let trip=await json(page.request,'POST','/api/city/v1/trips',{request_id:crypto.randomUUID(),draft:{}})
 const path=`/api/city/v1/trips/${trip.id}`

 // 1) Confirm the fixture route while the server clock is still before 09:00.
 const requirements=await json(page.request,'GET','/api/test/requirements')
 const plan=await json(page.request,'POST',`${path}/engine/preview`,
  {request_id:crypto.randomUUID(),expected_version:trip.version,requirements})
 expect(plan.status).toBe('preview')
 await json(page.request,'POST',`${path}/engine/confirm`,
  {request_id:crypto.randomUUID(),expected_version:trip.version,candidate_id:plan.candidate_id})

 // 2) Advance the clock, finish the first stop and set the anchor.
 await json(page.request,'POST','/api/test/clock',{now:'2030-05-01T09:40:00+08:00'})
 trip=await json(page.request,'GET',path)
 trip=await json(page.request,'PATCH',path,{request_id:crypto.randomUUID(),expected_version:trip.version,action:{operation:'transition',status:'ACTIVE'}})
 trip=await json(page.request,'PATCH',path,{request_id:crypto.randomUUID(),expected_version:trip.version,action:{operation:'mark_completed',stop_id:'visit',actual_start:'2030-05-01T09:00:00+08:00',actual_end:'2030-05-01T09:30:00+08:00'}})
 trip=await json(page.request,'PATCH',path,{request_id:crypto.randomUUID(),expected_version:trip.version,action:{operation:'set_anchor',anchor:{place_id:'museum',confirmed_at:'2030-05-01T09:40:00+08:00'}}})

 // 3) API-level smoke: a rain impact is reported for a remaining pending activity.
 const weatherBody=await json(page.request,'GET',`${path}/weather`)
 expect(weatherBody.status).toBe('answered')
 expect(weatherBody.weather.impacts.length).toBeGreaterThan(0)
 expect(weatherBody.weather.adjustment_offered).toBe(true)
 expect(weatherBody.weather.formal_state_changed).toBe(false)

 // 4) Direct agent-task smoke: the consent phrase reaches an OR-Tools preview.
 trip=await json(page.request,'GET',path)
 const task=await json(page.request,'POST',`${path}/agent/tasks`,
  {request_id:crypto.randomUUID(),text:'可以，按天气调整行程',expected_version:trip.version,expected_route_version:trip.route_version})
 let taskState=task
 for(let i=0;i<12&&taskState.status==='running';i++){
  taskState=await json(page.request,'POST',`${path}/agent/tasks/${task.task_id}/step`,
   {request_id:crypto.randomUUID(),expected_step:taskState.step})
 }
 expect(taskState.status).toBe('needs_confirmation')

 // 5) Browser-level: proactive card renders and the action reaches a confirmation gate.
 await page.goto(`/assistant?city_trip=${trip.id}`)
 await expect(page.locator('.weather-card')).toBeVisible()
 await expect(page.locator('.weather-card')).toContainText('重新核算行程')

 const before=await json(page.request,'GET',path)
 await page.getByRole('button',{name:'重新核算行程',exact:true}).click()
 await expect(page.getByRole('heading',{name:'请核对新安排'})).toBeVisible()

 const after=await json(page.request,'GET',path)
 expect(after.route_version).toBe(before.route_version)
 await expect(page.getByRole('button',{name:'确认修改',exact:true})).toBeVisible()
 expect(errors).toEqual([])
})
