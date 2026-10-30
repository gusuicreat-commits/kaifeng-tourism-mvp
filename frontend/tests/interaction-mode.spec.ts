import {test,expect,type Page} from '@playwright/test'
import type {Basket} from '../src/basketApi'
import type {PlanningConstraints,PlanningResult,PlanningWorkspace} from '../src/explorationApi'

const routePlaces=[['pl_wb_000003','开封府'],['pl_wb_vendor_000001','第一楼寺后街店'],['pl_wb_000013','鼓楼夜市']]
async function prepare(page:Page,empty=false,complete=false){
 await page.request.post('/api/v1/sessions')
 const draft=complete?{business_date:'2030-05-01',starts_at:'2030-05-01T09:00:00+08:00',ends_at:'2030-05-01T18:00:00+08:00',origin_place_id:'test:gate',destination_place_id:'test:hotel',modes:['walking']}:{}
 const response=await page.request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft}})
 expect(response.ok()).toBeTruthy();const trip=await response.json(),path='/api/city/v1/trips/'+trip.id
 const basket:Basket={trip_id:trip.id,version:empty?0:1,items:empty?[]:[{key:'place:test:gate',kind:'place',id:'test:gate',name:'[合成测试]入口',status:'pending'}]}
 const additions:string[]=[],previews:PlanningConstraints[]=[],tasks:{text:string;interaction_mode:string}[]=[]
 let workspace:PlanningWorkspace={version:0,constraints:null,result:null,rounds:0}
 await page.route('**'+path+'/basket',async route=>{
  if(route.request().method()==='PATCH'){
   const body=route.request().postDataJSON();expect(body.expected_version).toBe(basket.version)
   additions.push(body.item.object_id);basket.version++
   basket.items.push({key:body.item.kind+':'+body.item.object_id,kind:body.item.kind,id:body.item.object_id,name:routePlaces.find(p=>p[0]===body.item.object_id)?.[1]||'合成地点',status:'pending'})
  }
  await route.fulfill({json:basket})
 })
 await page.route('**'+path+'/exploration/constraints',route=>route.fulfill({json:workspace}))
 await page.route('**'+path+'/exploration/preview',async route=>{
  const body=route.request().postDataJSON(),c=body.constraints as PlanningConstraints
  expect(body.planning_version).toBe(workspace.version);previews.push(c)
  const missing=!c.business_date||!c.starts_at||!c.ends_at||!c.origin_place_id||!c.destination_place_id
  const result:PlanningResult={planning_version:workspace.version+1,rounds:0,status:'unknown',reply:missing?'请补齐条件':'合成规划请求已收到，仍待确认',constraints:c,basket_version:basket.version,trip_version:trip.version,issues:missing?[{code:'PERSONAL_CHOICE_MISSING',message:'待填写',affected_ids:[]}]:[],options:[],planning:null}
  workspace={version:result.planning_version,constraints:c,result,rounds:0}
  await route.fulfill({json:result})
 })
 await page.route('**'+path+'/agent/tasks',async route=>{
  const body=route.request().postDataJSON();tasks.push(body)
  await route.fulfill({json:{task_id:'mode-test',trip_id:trip.id,step:0,status:'answered',mode:'conversation',busy:false,progress:null,events:[],understanding:null,result:{reply:'合成对话已收到'}}})
 })
 await page.goto('/assistant?city_trip='+trip.id+'&plan=basket')
 await expect(page.locator('#city-agent-input')).toBeEnabled()
 return {trip,path,previews,tasks,additions}
}
async function fillDay(page:Page){
 const form=page.locator('.planning-question')
 await form.getByLabel('旅行日期',{exact:true}).fill('2030-05-01');await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByLabel('开始时间',{exact:true}).fill('09:00');await form.getByLabel('最晚结束',{exact:true}).fill('18:00');await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByRole('combobox',{name:'出发地点',exact:true}).selectOption('test:gate');await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByRole('combobox',{name:'返程地点',exact:true}).selectOption('test:hotel');await form.getByRole('button',{name:'继续',exact:true}).click()
 await expect(form).toContainText('几个人一起去？');await form.getByRole('button',{name:'2 人',exact:true}).click()
 await expect(form).toContainText('想怎么安排？')
}

test('单日深度互动收集人数和三组偏好，继续后由游客生成',async({page})=>{
 const {previews}=await prepare(page);await fillDay(page)
 const form=page.locator('.planning-question'),count=previews.length
 await form.getByRole('button',{name:'深度互动',exact:false}).click()
 await expect(form).toContainText('最后几个问题，帮我更懂你')
 for(const label of ['游玩节奏','景点之间怎么走','更在意什么'])await expect(form.getByRole('group',{name:label,exact:true})).toBeVisible()
 await form.getByRole('button',{name:'轻松',exact:true}).click();await form.getByRole('button',{name:'公交地铁',exact:true}).click();await form.getByRole('button',{name:'少走路',exact:true}).click()
 await form.getByRole('button',{name:'继续',exact:true}).click();expect(previews).toHaveLength(count)
 await form.getByRole('button',{name:'生成安排',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('合成规划请求已收到')
 expect(previews.at(-1)).toMatchObject({pace:'relaxed',modes:['transit'],objective:'less_walking',party_size:2})
 expect(previews.at(-1)).not.toHaveProperty('pricing')
})

test('快速安排跳过偏好并直接生成，模式刷新保持，已选模式不再追问',async({page})=>{
 const {trip,previews}=await prepare(page);await fillDay(page)
 const count=previews.length
 await page.getByRole('group',{name:'选择互动模式',exact:true}).getByRole('button',{name:'快速安排',exact:false}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('合成规划请求已收到')
 expect(previews).toHaveLength(count+1);expect(previews.at(-1)).toMatchObject({pace:'standard',modes:['walking'],objective:'balanced',party_size:2})
 await expect(page.getByRole('group',{name:'游玩节奏',exact:true})).toHaveCount(0)
 expect(await page.evaluate(id=>localStorage.getItem('bianliang:interaction-mode:'+id),trip.id)).toBe('quick')
 await page.reload();await expect(page.getByRole('group',{name:'互动模式',exact:true}).getByRole('button',{name:'快速安排',exact:true})).toHaveAttribute('aria-pressed','true')
 await page.getByLabel('已提供的安排').getByRole('button',{name:'2 人',exact:true}).click();await page.locator('.planning-question').getByRole('button',{name:'3 人',exact:true}).click()
 await expect(page.getByRole('group',{name:'选择互动模式',exact:true})).toHaveCount(0)
 await expect.poll(()=>previews.at(-1)?.party_size).toBe(3)
})

test('ready 和偏好步骤随时切换，聊天和表单立即共用模式',async({page})=>{
 const {previews}=await prepare(page);await fillDay(page)
 const form=page.locator('.planning-question'),known=page.getByLabel('已提供的安排'),mode=page.getByRole('group',{name:'互动模式',exact:true})
 await form.getByRole('button',{name:'深度互动',exact:false}).click();await form.getByRole('button',{name:'继续',exact:true}).click()
 await expect(form.getByRole('button',{name:'生成安排',exact:true})).toBeVisible()
 await known.getByRole('button',{name:'深度互动',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('合成规划请求已收到')
 await known.getByRole('button',{name:'快速安排',exact:true}).click()
 await expect(form).toContainText('最后几个问题，帮我更懂你');await expect(mode.getByRole('button',{name:'深度互动',exact:true})).toHaveAttribute('aria-pressed','true')
 const count=previews.length
 await mode.getByRole('button',{name:'快速安排',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('合成规划请求已收到');expect(previews).toHaveLength(count+1)
 await mode.getByRole('button',{name:'深度互动',exact:true}).click();await expect(form).toContainText('最后几个问题，帮我更懂你')
})

test('对话快速模式隐藏自动偏好卡，仍可手动打开，请求携带当前模式',async({page})=>{
 const {tasks}=await prepare(page,false,true),mode=page.getByRole('group',{name:'互动模式',exact:true}),card=page.getByRole('region',{name:'出发前问你几个问题',exact:true})
 await expect(card).toBeVisible()
 const send=async(text:string)=>{await page.locator('#city-agent-input').fill(text);await page.getByRole('button',{name:'发送',exact:true}).click();await expect(page.locator('.agent-messages')).toContainText(text);await expect(page.locator('#city-agent-input')).toBeEnabled()}
 await send('默认模式');expect(tasks.at(-1)?.interaction_mode).toBe('deep')
 await mode.getByRole('button',{name:'快速安排',exact:true}).click();await expect(card).toHaveCount(0)
 await page.getByRole('button',{name:'偏好',exact:true}).click();await expect(card).toBeVisible()
 await send('快速聊一下');expect(tasks.at(-1)?.interaction_mode).toBe('quick')
 await mode.getByRole('button',{name:'深度互动',exact:true}).click();await expect(card).toBeVisible()
 await send('深度聊一下');expect(tasks.at(-1)?.interaction_mode).toBe('deep')
 await page.reload();await expect(mode.getByRole('button',{name:'深度互动',exact:true})).toHaveAttribute('aria-pressed','true')
})

test('空素材篮先选择发现或推荐，推荐依次加入三处地点后进入日期步',async({page})=>{
 const {additions}=await prepare(page,true)
 await expect(page.locator('.planning-question')).toContainText('素材篮还是空的，先挑几个地方，还是用推荐路线？')
 await expect(page.getByRole('link',{name:'先去发现开封',exact:true})).toHaveAttribute('href','/')
 await page.getByRole('button',{name:'用推荐的开封夜游路线',exact:true}).click()
 await expect(page.getByLabel('旅行日期',{exact:true})).toBeVisible();expect(additions).toEqual(routePlaces.map(p=>p[0]))
 await expect(page.locator('.planning-materials')).toContainText('开封府、第一楼寺后街店、鼓楼夜市')
})

test('人数输入严格限制1到50，存储被禁用也能共用本页模式',async({page})=>{
 await page.addInitScript(()=>{Storage.prototype.getItem=()=>{throw new Error('blocked')};Storage.prototype.setItem=()=>{throw new Error('blocked')}})
 const {previews}=await prepare(page,false,true),form=page.locator('.planning-question')
 const count=previews.length,input=form.getByLabel('旅行人数',{exact:true})
 for(const value of ['0','51','1.5']){await input.fill(value);await expect(form.getByRole('button',{name:'继续',exact:true})).toBeDisabled()}
 await input.fill('50');await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByRole('button',{name:'快速安排',exact:false}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('合成规划请求已收到');expect(previews).toHaveLength(count+1);expect(previews.at(-1)?.party_size).toBe(50)
 await expect(page.getByRole('group',{name:'互动模式',exact:true}).getByRole('button',{name:'快速安排',exact:true})).toHaveAttribute('aria-pressed','true')
})

test('首页规划入口直接打开素材篮规划表单',async({page})=>{
 await page.goto('/');await expect(page.getByRole('link',{name:'规划我的开封之旅',exact:true})).toHaveAttribute('href','/assistant?context=city&plan=basket')
})
