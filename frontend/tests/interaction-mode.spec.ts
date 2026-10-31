import {test,expect,type Page} from '@playwright/test'
import type {Basket} from '../src/basketApi'
import type {EnginePreview,PlanningInput,PlanningConstraints,PlanningResult,PlanningWorkspace} from '../src/explorationApi'

const routePlaces=[['pl_wb_000003','开封府'],['pl_wb_vendor_000001','第一楼寺后街店'],['pl_wb_000013','鼓楼夜市']]
async function prepare(page:Page,empty=false,complete=false,planning=true,previewDelayMs=0){
 await page.request.post('/api/v1/sessions')
 const draft=complete?{business_date:'2030-05-01',starts_at:'2030-05-01T09:00:00+08:00',ends_at:'2030-05-01T18:00:00+08:00',origin_place_id:'test:gate',destination_place_id:'test:hotel',modes:['walking']}:{}
 const response=await page.request.post('/api/city/v1/trips',{data:{request_id:crypto.randomUUID(),draft}})
 expect(response.ok()).toBeTruthy();const trip=await response.json(),path='/api/city/v1/trips/'+trip.id
 const basket:Basket={trip_id:trip.id,version:empty?0:1,items:empty?[]:[{key:'place:test:gate',kind:'place',id:'test:gate',name:'[合成测试]入口',status:'pending'}]}
 const additions:string[]=[],previews:PlanningInput[]=[],tasks:{text:string;interaction_mode:string}[]=[]
 let workspace:PlanningWorkspace={version:0,constraints:null,result:null,rounds:0}
 let delayNextPreview=false
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
  const body=route.request().postDataJSON(),c=body.constraints as PlanningInput
  if(previewDelayMs&&delayNextPreview){delayNextPreview=false;await new Promise(resolve=>setTimeout(resolve,previewDelayMs))}
  expect(body.planning_version).toBe(workspace.version);previews.push(c)
  const missing='schema_version' in c?!(c.days as PlanningConstraints[]).every(d=>d.starts_at&&d.ends_at&&d.origin_place_id&&d.destination_place_id):!c.business_date||!c.starts_at||!c.ends_at||!c.origin_place_id||!c.destination_place_id
  const result:PlanningResult={planning_version:workspace.version+1,rounds:0,status:'unknown',reply:missing?'请补齐条件':'合成规划请求已收到，仍待确认',constraints:c,basket_version:basket.version,trip_version:trip.version,issues:missing?[{code:'PERSONAL_CHOICE_MISSING',message:'待填写',affected_ids:[]}]:[],options:[],planning:null}
  workspace={version:result.planning_version,constraints:c,result,rounds:0}
  await route.fulfill({json:result})
 })
 await page.route('**'+path+'/agent/tasks',async route=>{
  const body=route.request().postDataJSON();tasks.push(body)
  await route.fulfill({json:{task_id:'mode-test',trip_id:trip.id,step:0,status:'answered',mode:'conversation',busy:false,progress:null,events:[],understanding:null,result:{reply:'合成对话已收到'}}})
 })
 await page.goto('/assistant?city_trip='+trip.id+(planning?'&plan=basket':''))
 await expect(page.locator('#city-agent-input')).toBeEnabled()
 return {trip,path,basket,previews,tasks,additions,armPreviewDelay:()=>{delayNextPreview=true}}
}

test('规划预览超过旧时限仍等待结果，并在八秒后显示查询提示',async({page})=>{
 test.setTimeout(60000)
 const {previews,armPreviewDelay}=await prepare(page,false,false,true,18000);await fillDay(page)
 const form=page.locator('.planning-question')
 armPreviewDelay()
 await form.getByRole('button',{name:'快速安排',exact:false}).click()
 await expect(page.getByRole('status')).toContainText('正在向高德查询路线和计算方案，地点较多时可能需要半分钟…',{timeout:12000})
 await expect(page.getByLabel('规划检测结果')).toContainText('合成规划请求已收到',{timeout:30000})
 expect(previews.length).toBeGreaterThan(0);await expect(page.getByText('请求超时，请重试。')).toHaveCount(0)
})
async function fillDay(page:Page,days=1){
 const form=page.locator('.planning-question')
 await form.getByLabel('旅行日期',{exact:true}).fill('2030-05-01');await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByRole('button',{name:days+'天',exact:true}).click()
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
 const {tasks}=await prepare(page,true,true,false),mode=page.getByRole('group',{name:'互动模式',exact:true}),card=page.getByRole('region',{name:'出发前问你几个问题',exact:true})
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

for(const explained of [true,false])test('单日规划表保留候补和无原因项'+(explained?'，优先显示后端未安排原因并去重':'，无后端原因时仍标注候补'),async({page})=>{
 const {trip,path,basket}=await prepare(page)
 basket.items.push(
  {key:'place:test:unknown',kind:'place',id:'test:unknown',name:'合成无原因地点',status:'pending'},
  {key:'place:test:omitted',kind:'place',id:'test:omitted',name:'合成未排地点',status:'pending'},
  {key:'place:test:spare',kind:'place',id:'test:spare',name:'合成候补地点',status:'standby'},
  {key:'place:test:arranged',kind:'place',id:'test:arranged',name:'合成已安排地点',status:'arranged'}
 )
 await fillDay(page)
 await page.route('**'+path+'/exploration/preview',async route=>{
  const body=route.request().postDataJSON(),item={goal_id:'goal:omitted',key:'place:test:omitted',name:'合成未排地点',reason_code:'time_conflict',reason:'和其他地点放在一起时间不够'}
  const result:PlanningResult={planning_version:body.planning_version+1,rounds:0,status:'feasible',reply:'合成规划完成，仍待确认',constraints:body.constraints,basket_version:basket.version,trip_version:trip.version,issues:[],options:[],goal_keys:{'goal:gate':'place:test:gate'},unscheduled:explained?{'0':[item,item]}:{},planning:{candidate_id:'mock-candidate',base_version:trip.version,result:{status:'feasible',warnings:[],metrics:{},itineraries:[{objectives:['balanced'],activities:[{goal_id:'goal:gate',place_id:'test:gate',session_id:null,kind:'visit',starts_at:'2030-05-01T09:00:00+08:00',departs_at:'2030-05-01T10:00:00+08:00',duration_seconds:3600,required:false}],legs:[],total_seconds:3600,travel_seconds:0,walking_distance_m:0,known_cost_upper_fen:0,cost_unknown:true,returns_at:'2030-05-01T10:00:00+08:00',evidence_mode:'synthetic'}]}}}
  await route.fulfill({json:result})
 })
 await page.getByRole('group',{name:'选择互动模式',exact:true}).getByRole('button',{name:'快速安排',exact:false}).click()
 const omitted=page.locator('.plan-option').first().locator('p').filter({hasText:'暂不安排：'})
 await expect(omitted).toHaveText('暂不安排：'+(explained?'合成未排地点（和其他地点放在一起时间不够）、合成候补地点（候补）、合成无原因地点':'合成候补地点（候补）、合成无原因地点、合成未排地点')+'候补、未选择或在当前约束下未纳入；素材不会删除。')
})

test('素材篮入口独占偏好收集，普通对话保留原偏好卡',async({page})=>{
 const {trip}=await prepare(page,true)
 await page.goto('/assistant?context=city&plan=basket')
 await expect(page.getByLabel('素材篮规划协商')).toBeVisible()
 const card=page.getByRole('region',{name:'出发前问你几个问题',exact:true}),button=page.getByRole('button',{name:'偏好',exact:true})
 await expect(card).toHaveCount(0);await expect(button).toHaveCount(0)
 await page.goto('/assistant?city_trip='+trip.id)
 await expect(card).toBeVisible();await expect(button).toBeVisible()
})


test('三天快速安排共用每天时间与端点，天数标签可修改回单日',async({page})=>{
 const {previews}=await prepare(page);await fillDay(page,3)
 const form=page.locator('.planning-question'),known=page.getByLabel('已提供的安排')
 await known.getByRole('button',{name:'2030-05-01',exact:true}).click()
 await expect(form).toContainText('第一天是哪天？');await form.getByRole('button',{name:'继续',exact:true}).click()
 await known.getByRole('button',{name:'09:00—18:00',exact:true}).click()
 await expect(form).toContainText('每天几点开始、几点结束？');await form.getByRole('button',{name:'继续',exact:true}).click()
 await known.getByRole('button',{name:'从[测试]入口',exact:true}).click()
 await expect(form).toContainText('每天从哪里出发？');await form.getByRole('button',{name:'继续',exact:true}).click()
 await known.getByRole('button',{name:'回到[测试]返程点',exact:true}).click()
 await expect(form).toContainText('每天最后回到哪里？');await form.getByRole('button',{name:'继续',exact:true}).click()
 const count=previews.length
 await form.getByRole('button',{name:'快速安排',exact:false}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('合成规划请求已收到')
 expect(previews).toHaveLength(count+1)
 expect(previews.at(-1)).toEqual({schema_version:'city-constraints/2',start_date:'2030-05-01',end_date:'2030-05-03',trip_days:3,days:[1,2,3].map(n=>({day_id:'day:2030-05-0'+n,business_date:'2030-05-0'+n,starts_at:`2030-05-0${n}T09:00:00+08:00`,ends_at:`2030-05-0${n}T18:00:00+08:00`,origin_place_id:'test:gate',destination_place_id:'test:hotel'})),pace:'standard',objective:'balanced',modes:['walking'],party_size:2})
 await known.getByRole('button',{name:'3 天',exact:true}).click()
 await expect(form.getByLabel('游玩天数',{exact:true})).toHaveValue('3')
 await form.getByRole('button',{name:'1天',exact:true}).click()
 await expect(known.getByRole('button',{name:'1 天',exact:true})).toBeVisible()
 await expect.poll(()=>previews.length).toBe(count+2)
 expect(previews.at(-1)).toEqual({business_date:'2030-05-01',starts_at:'2030-05-01T09:00:00+08:00',ends_at:'2030-05-01T18:00:00+08:00',origin_place_id:'test:gate',destination_place_id:'test:hotel',pace:'standard',objective:'balanced',modes:['walking'],budget_fen:null,party_size:2})
})

test('天数只接受1到3的整数，跨年多天沿用深度偏好与预算',async({page})=>{
 const {previews}=await prepare(page),form=page.locator('.planning-question')
 await form.getByLabel('旅行日期',{exact:true}).fill('2030-12-31');await form.getByRole('button',{name:'继续',exact:true}).click()
 const input=form.getByLabel('游玩天数',{exact:true})
 await expect(input).toHaveValue('1');await expect(input).toHaveAttribute('max','3')
 for(const value of ['','0','4','1.5']){await input.fill(value);await expect(form.getByRole('button',{name:'继续',exact:true})).toBeDisabled()}
 await input.fill('3');await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByLabel('开始时间',{exact:true}).fill('10:00');await form.getByLabel('最晚结束',{exact:true}).fill('19:00');await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByRole('combobox',{name:'出发地点',exact:true}).selectOption('test:hotel');await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByRole('combobox',{name:'返程地点',exact:true}).selectOption('test:hotel');await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByRole('button',{name:'3 人',exact:true}).click();await form.getByRole('button',{name:'深度互动',exact:false}).click()
 await form.getByRole('button',{name:'轻松',exact:true}).click();await form.getByRole('button',{name:'公交地铁',exact:true}).click();await form.getByRole('button',{name:'少走路',exact:true}).click();await form.getByLabel('预算上限（选填）',{exact:true}).fill('1000')
 const count=previews.length
 await form.getByRole('button',{name:'继续',exact:true}).click();expect(previews).toHaveLength(count)
 await form.getByRole('button',{name:'生成安排',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('合成规划请求已收到')
 expect(previews.at(-1)).toMatchObject({schema_version:'city-constraints/2',start_date:'2030-12-31',end_date:'2031-01-02',trip_days:3,days:[{business_date:'2030-12-31'},{business_date:'2031-01-01'},{business_date:'2031-01-02'}],pace:'relaxed',objective:'less_walking',modes:['transit'],party_size:3,pricing:{total_budget_fen:100000,party_size:3,hard_limit:true}})
 expect(previews.at(-1)).not.toHaveProperty('goals');expect(previews.at(-1)).not.toHaveProperty('overnights')
})

for(const days of [1,3])test(days+'天填写预算后显示引擎unknown回复，零预算也保留',async({page})=>{
 const {trip,path,previews}=await prepare(page);await fillDay(page,days)
 await page.route('**'+path+'/exploration/preview',async route=>{
  const body=route.request().postDataJSON();previews.push(body.constraints)
  const result:EnginePreview={status:'unknown',candidate_id:null,base_version:trip.version,planning_version:body.planning_version+1,basket_version:1,trip_version:trip.version,constraints:body.constraints,reply:'合成引擎回复：交通资料不足，请核对条件',result:{status:'unknown',itineraries:[],warnings:[],metrics:{}}}
  await route.fulfill({json:result})
 })
 const form=page.locator('.planning-question'),assessment=page.getByLabel('规划检测结果')
 await form.getByRole('button',{name:'深度互动',exact:false}).click()
 await form.getByLabel('预算上限（选填）',{exact:true}).fill('125')
 await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByRole('button',{name:'生成安排',exact:true}).click()
 await expect(assessment).toContainText('合成引擎回复：交通资料不足，请核对条件')
 expect(previews.at(-1)).toMatchObject(days===1?{budget_fen:12500}:{pricing:{total_budget_fen:12500}})
 await page.getByLabel('已提供的安排').getByRole('button',{name:'适中 · 步行',exact:true}).click()
 await form.getByLabel('预算上限（选填）',{exact:true}).fill('0')
 await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByRole('button',{name:'生成安排',exact:true}).click()
 await expect(assessment).toContainText('合成引擎回复：交通资料不足，请核对条件')
 expect(previews.at(-1)).toMatchObject(days===1?{budget_fen:0}:{pricing:{total_budget_fen:0}})
})

for(const days of [1,3])test(days+'天深度偏好请求、互斥、已知条件与未安排原因',async({page})=>{
 const {previews,path,trip,basket}=await prepare(page);await fillDay(page,days)
 await page.route('**'+path+'/exploration/preview',async route=>{
  const body=route.request().postDataJSON();previews.push(body.constraints)
  const single={objectives:['balanced'],activities:[],legs:[],total_seconds:0,travel_seconds:0,walking_distance_m:0,known_cost_upper_fen:0,cost_unknown:true,returns_at:'2030-05-01T09:00:00+08:00',evidence_mode:'synthetic'}
  const multi={schema_version:'city-itinerary/2',start_date:'2030-05-01',end_date:'2030-05-03',days:body.constraints.days?.map((d:Record<string,unknown>)=>({...d,original_requirements:d,requirements:{...d,goals:[],pace:'standard',objective:'balanced',modes:['walking']},itinerary:single})),overnights:[],active_seconds:0,elapsed_seconds:0}
  const result={planning_version:body.planning_version+1,rounds:0,status:'feasible',reply:'合成偏好规划完成',constraints:body.constraints,basket_version:basket.version,trip_version:trip.version,issues:[],options:[],unscheduled:{'0':[{goal_id:'excluded:food',key:'food:test:food',name:'合成美食',reason_code:'preference_excluded',reason:'你说过不想安排美食'}]},planning:{candidate_id:'mock-candidate',base_version:trip.version,result:{status:'feasible',warnings:[],metrics:{},itineraries:[days===1?single:multi]}}}
  if(days===1)await route.fulfill({json:result})
  else {const {planning,...rest}=result;await route.fulfill({json:{...rest,status:'preview',...planning}})}
 })
 const form=page.locator('.planning-question'),known=page.getByLabel('已提供的安排')
 await form.getByRole('button',{name:'深度互动',exact:false}).click()
 const spice=form.getByRole('group',{name:'能吃辣吗',exact:true}),interests=form.getByRole('group',{name:'更感兴趣（可多选）',exact:true}),avoid=form.getByRole('group',{name:'不想安排（可多选）',exact:true})
 await spice.getByRole('button',{name:'微辣可以',exact:true}).click()
 await spice.getByRole('button',{name:'不吃辣',exact:true}).click()
 await expect(spice.getByRole('button',{name:'微辣可以',exact:true})).toHaveAttribute('aria-pressed','false')
 await interests.getByRole('button',{name:'品美食',exact:true}).click()
 await avoid.getByRole('button',{name:'品美食',exact:true}).click()
 await expect(interests.getByRole('button',{name:'品美食',exact:true})).toHaveAttribute('aria-pressed','false')
 await avoid.getByRole('button',{name:'看演出',exact:true}).click()
 await interests.getByRole('button',{name:'看演出',exact:true}).click()
 await expect(avoid.getByRole('button',{name:'看演出',exact:true})).toHaveAttribute('aria-pressed','false')
 await interests.getByRole('button',{name:'逛夜景',exact:true}).click()
 await avoid.getByRole('button',{name:'景区古迹',exact:true}).click()
 for(const label of ['不吃辣','感兴趣：演出','感兴趣：夜游','不想安排：美食','不想安排：景区'])await expect(known.getByRole('button',{name:label,exact:true})).toBeVisible()
 await form.getByRole('button',{name:'继续',exact:true}).click()
 await form.getByRole('button',{name:'生成安排',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('合成美食（你说过不想安排美食）')
 expect(previews.at(-1)?.preferences).toEqual({spice:'none',interests:['shows','night'],avoid:['food','scenic']})
})

test('深度偏好可取消单选，切到快速模式不发送偏好',async({page})=>{
 const {previews}=await prepare(page);await fillDay(page)
 const form=page.locator('.planning-question')
 await form.getByRole('button',{name:'深度互动',exact:false}).click()
 const spice=form.getByRole('group',{name:'能吃辣吗',exact:true})
 await spice.getByRole('button',{name:'都可以',exact:true}).click()
 await spice.getByRole('button',{name:'都可以',exact:true}).click()
 await expect(spice.getByRole('button',{name:'都可以',exact:true})).toHaveAttribute('aria-pressed','false')
 await spice.getByRole('button',{name:'不吃辣',exact:true}).click()
 await form.getByRole('group',{name:'不想安排（可多选）',exact:true}).getByRole('button',{name:'品美食',exact:true}).click()
 await page.getByRole('group',{name:'互动模式',exact:true}).getByRole('button',{name:'快速安排',exact:true}).click()
 await expect(page.getByLabel('规划检测结果')).toContainText('合成规划请求已收到')
 expect(previews.at(-1)).not.toHaveProperty('preferences')
 for(const name of ['能吃辣吗','更感兴趣（可多选）','不想安排（可多选）'])await expect(page.getByRole('group',{name,exact:true})).toHaveCount(0)
 await expect(page.getByLabel('已提供的安排').getByRole('button',{name:'不吃辣',exact:true})).toHaveCount(0)
})


test('对话结果更新模式、保存并用于下一次请求',async({page})=>{
 const {trip,path,tasks}=await prepare(page,false,true,false)
 await page.route('**'+path+'/agent/tasks',async route=>{
  tasks.push(route.request().postDataJSON())
  await route.fulfill({json:{task_id:'mode-switch',trip_id:trip.id,step:0,status:'answered',mode:'conversation',busy:false,progress:null,events:[],understanding:null,result:{reply:'已切换为快速安排',interaction_mode:'quick',interaction_mode_changed:true}}})
 })
 const mode=page.getByRole('group',{name:'互动模式',exact:true}),input=page.locator('#city-agent-input')
 await mode.getByRole('button',{name:'深度互动',exact:true}).click()
 await input.fill('请你直接决定');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(mode.getByRole('button',{name:'快速安排',exact:true})).toHaveAttribute('aria-pressed','true')
 expect(tasks[0].interaction_mode).toBe('deep')
 expect(await page.evaluate(id=>localStorage.getItem('bianliang:interaction-mode:'+id),trip.id)).toBe('quick')
 await expect(input).toBeEnabled();await input.fill('接着安排');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect.poll(()=>tasks.length).toBe(2);expect(tasks[1].interaction_mode).toBe('quick')
 await page.reload()
 await expect(mode.getByRole('button',{name:'快速安排',exact:true})).toHaveAttribute('aria-pressed','true')
})


test('对话结果未切换模式时，规划表单仍询问互动模式',async({page})=>{
 const {trip,path,tasks}=await prepare(page);await fillDay(page)
 await page.route('**'+path+'/agent/tasks',async route=>{
  tasks.push(route.request().postDataJSON())
  await route.fulfill({json:{task_id:'mode-stay',trip_id:trip.id,step:0,status:'answered',mode:'conversation',busy:false,progress:null,events:[],understanding:null,result:{reply:'合成对话已收到',interaction_mode:'deep',interaction_mode_changed:false}}})
 })
 const form=page.locator('.planning-question')
 await expect(form.getByRole('group',{name:'选择互动模式',exact:true})).toBeVisible()
 await page.locator('#city-agent-input').fill('先聊聊');await page.getByRole('button',{name:'发送',exact:true}).click()
 await expect(page.getByRole('log',{name:'聊天记录',exact:true})).toContainText('合成对话已收到')
 await expect(form.getByRole('group',{name:'选择互动模式',exact:true})).toBeVisible()
})
