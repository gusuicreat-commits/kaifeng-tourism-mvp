import {test,expect} from '@playwright/test'
import type {Page} from '@playwright/test'

async function openVoice(page:Page){
 await page.goto('/')
 await page.getByRole('button',{name:'打开语音转文字'}).click()
 await expect(page.getByRole('dialog',{name:'语音转文字'})).toBeVisible()
}

async function available(page:Page){
 await page.route('**/api/speech/status',route=>route.fulfill({json:{available:true,max_seconds:60}}))
 // Track real getUserMedia streams to prove cleanup, without capturing a real microphone.
 await page.addInitScript(()=>{
  const streams:MediaStream[]=[]
  const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
  navigator.mediaDevices.getUserMedia=async constraints=>{const stream=await original(constraints);streams.push(stream);return stream}
  Object.assign(window,{voiceTestTracks:()=>streams.flatMap(s=>s.getTracks()).map(t=>t.readyState)})
 })
}

test('未配置密钥时显示未开通，保留手动编辑与复制',async({page,context})=>{
 await context.grantPermissions(['clipboard-read','clipboard-write'])
 await page.route('**/api/speech/status',route=>route.fulfill({json:{available:false,max_seconds:60}}))
 await openVoice(page)
 await expect(page.getByRole('alert')).toContainText('暂未开通')
 await expect(page.getByRole('button',{name:'开始录音',exact:true})).toBeDisabled()
 await page.getByLabel('转写文字').fill('我想去万岁山武侠城。')
 await page.getByRole('button',{name:'复制文字',exact:true}).click()
 expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe('我想去万岁山武侠城。')
 await page.setViewportSize({width:320,height:740})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 const bounds=await page.getByRole('dialog').boundingBox()
 expect(bounds!.x).toBeGreaterThanOrEqual(0)
 expect(bounds!.width).toBeLessThanOrEqual(320)
 await page.screenshot({path:'../docs/screenshots/voice-unconfigured-mobile.png'})
 await page.keyboard.press('Escape')
 await expect(page.getByRole('dialog')).not.toBeVisible()
 await expect(page.getByRole('button',{name:'打开小汴梁智能助手',exact:true})).toBeFocused()
})

test('浏览器采集、WAV上传、转写编辑复制与关闭释放麦克风',async({page,context})=>{
 await context.grantPermissions(['microphone','clipboard-read','clipboard-write'])
 await available(page)
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 let uploaded=0
 await page.route('**/api/speech/transcriptions',async route=>{
  const body=route.request().postDataBuffer()!
  const start=body.indexOf(Buffer.from('RIFF'))
  expect(start).toBeGreaterThan(0)
  expect(body.toString('ascii',start+8,start+12)).toBe('WAVE')
  expect(body.readUInt16LE(start+22)).toBe(1)
  expect(body.readUInt32LE(start+24)).toBe(16000)
  expect(body.readUInt16LE(start+34)).toBe(16)
  expect(body.readUInt32LE(start+40)).toBeGreaterThan(6400)
  expect(body.readUInt32LE(start+40)).toBeLessThanOrEqual(60*32000)
  uploaded++
  await route.fulfill({json:{text:'我想去清明上河园，玩两个小时。'}})
 })
 await openVoice(page)
 await page.getByRole('button',{name:'开始录音',exact:true}).click()
 await expect(page.getByText('正在录音 · 1 / 60 秒',{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'结束并转文字'}).click()
 await expect(page.getByLabel('转写文字')).toHaveValue('我想去清明上河园，玩两个小时。')
 expect(uploaded).toBe(1)
 expect(await page.evaluate(()=>(window as unknown as {voiceTestTracks:()=>string[]}).voiceTestTracks())).toEqual(['ended'])
 await page.getByLabel('转写文字').fill('我想去清明上河园，玩三个小时。')
 await page.getByRole('button',{name:'复制文字',exact:true}).click()
 expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe('我想去清明上河园，玩三个小时。')
 await page.screenshot({path:'../docs/screenshots/voice-desktop.png'})
 await page.setViewportSize({width:390,height:844})
 await page.screenshot({path:'../docs/screenshots/voice-mobile.png'})
 await page.getByRole('button',{name:'开始录音',exact:true}).click()
 await expect(page.getByRole('button',{name:'结束并转文字'})).toBeVisible()
 await page.getByRole('button',{name:'关闭语音转文字'}).click()
 expect(await page.evaluate(()=>(window as unknown as {voiceTestTracks:()=>string[]}).voiceTestTracks())).toEqual(['ended','ended'])
 expect(uploaded).toBe(1)
 expect(errors).toEqual([])
})

test('失败保留旧文字，手动重试同一录音且不重复追加',async({page,context})=>{
 await context.grantPermissions(['microphone'])
 await available(page)
 let calls=0
 await page.route('**/api/speech/transcriptions',route=>{
  calls++
  return calls===1?route.fulfill({status:504,json:{detail:'语音识别超时，请稍后重试'}}):route.fulfill({json:{text:'我想看演出。'}})
 })
 await openVoice(page)
 await page.getByLabel('转写文字').fill('原有想法。')
 await page.getByRole('button',{name:'开始录音',exact:true}).click()
 await expect(page.getByText('正在录音 · 1 / 60 秒',{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'结束并转文字'}).click()
 await expect(page.getByRole('alert')).toContainText('超时')
 await expect(page.getByLabel('转写文字')).toHaveValue('原有想法。')
 await page.getByRole('button',{name:'重试识别',exact:true}).click()
 await expect(page.getByLabel('转写文字')).toHaveValue('原有想法。\n我想看演出。')
 expect(calls).toBe(2)
})

test('麦克风拒绝后可以重试',async({page})=>{
 await available(page)
 await page.addInitScript(()=>{navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('denied','NotAllowedError')}})
 await openVoice(page)
 await page.getByRole('button',{name:'开始录音',exact:true}).click()
 await expect(page.getByRole('alert')).toContainText('麦克风权限未开启')
 await expect(page.getByRole('button',{name:'开始录音',exact:true})).toBeEnabled()
})

test('取消待授权后迟到的麦克风会释放',async({page,context})=>{
 await context.grantPermissions(['microphone'])
 await available(page)
 await page.addInitScript(()=>{
  const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
  navigator.mediaDevices.getUserMedia=async constraints=>{
   const stream=await original(constraints)
   await new Promise<void>(resolve=>Object.assign(window,{releaseVoicePermission:resolve}))
   return stream
  }
 })
 let calls=0
 await page.route('**/api/speech/transcriptions',route=>{calls++;return route.fulfill({json:{text:'不应追加。'}})})
 await openVoice(page)
 await page.getByRole('button',{name:'开始录音',exact:true}).click()
 await expect.poll(()=>page.evaluate(()=>typeof (window as unknown as {releaseVoicePermission?:()=>void}).releaseVoicePermission)).toBe('function')
 await page.getByRole('button',{name:'取消录音',exact:true}).click()
 await page.evaluate(()=>(window as unknown as {releaseVoicePermission:()=>void}).releaseVoicePermission())
 await expect.poll(()=>page.evaluate(()=>(window as unknown as {voiceTestTracks:()=>string[]}).voiceTestTracks())).toEqual(['ended'])
 expect(calls).toBe(0)
 await page.getByRole('button',{name:'关闭语音转文字'}).click()
})

test('关闭识别后迟到的结果不会覆盖已有文字',async({page,context})=>{
 await context.grantPermissions(['microphone'])
 await available(page)
 let release:()=>void=()=>{}
 await page.route('**/api/speech/transcriptions',async route=>{
  await new Promise<void>(resolve=>{release=resolve})
  await route.fulfill({json:{text:'不应追加。'}})
 })
 await openVoice(page)
 await page.getByLabel('转写文字').fill('保留我的想法。')
 await page.getByRole('button',{name:'开始录音',exact:true}).click()
 await expect(page.getByText('正在录音 · 1 / 60 秒',{exact:true})).toBeVisible()
 const sent=page.waitForRequest('**/api/speech/transcriptions')
 await page.getByRole('button',{name:'结束并转文字'}).click()
 await sent
 await page.getByRole('button',{name:'关闭语音转文字'}).click()
 release()
 await page.getByRole('button',{name:'打开语音转文字'}).click()
 await expect(page.getByRole('button',{name:'开始录音',exact:true})).toBeEnabled()
 await expect(page.getByLabel('转写文字')).toHaveValue('保留我的想法。')
})


test.describe('手机小汴梁统一入口',()=>{
 test.use({viewport:{width:390,height:844},hasTouch:true})
 async function hold(page:Page){
  const box=(await page.locator('.global-assistant-launcher').boundingBox())!
  const cdp=await page.context().newCDPSession(page)
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:box.x+box.width/2,y:box.y+40}]})
  await expect(page.getByRole('region',{name:'小汴梁语音输入',exact:true})).toBeVisible()
  return async(cancel=false)=>{await cdp.send('Input.dispatchTouchEvent',{type:cancel?'touchCancel':'touchEnd',touchPoints:[]});await cdp.detach()}
 }
 test('按住显示无按钮侧边气泡，松手立刻收起，转写进入原上下文草稿且不自动发送',async({page,context})=>{
  await context.grantPermissions(['microphone']);await available(page)
  let tasks=0,complete!:()=>void
  const gate=new Promise<void>(resolve=>complete=resolve)
  page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/agent/tasks'))tasks++})
  await page.route('**/api/speech/transcriptions',async route=>{await gate;await route.fulfill({json:{text:'我想去清明上河园。'}})})
  await page.goto('/scenic/qingming')
  const release=await hold(page),bubble=page.getByRole('region',{name:'小汴梁语音输入',exact:true})
  await expect(bubble).toContainText('正在录音 · 1 / 60 秒')
  await expect(bubble.locator('button,svg')).toHaveCount(0)
  await expect(page.locator('.global-assistant-launcher')).toHaveClass(/assistant-is-writing/)
  const b=(await bubble.boundingBox())!,character=(await page.locator('.global-assistant-launcher').boundingBox())!
  expect(b.x+b.width).toBeLessThanOrEqual(character.x);expect(Math.abs(b.y-character.y)).toBeLessThan(48)
  await page.screenshot({path:'../docs/screenshots/assistant-voice-hold-mobile.png'})
  await release();await expect(bubble).toHaveCount(0)
  await expect(page).toHaveURL(/scenic\/qingming$/)
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {voiceTestTracks:()=>string[]}).voiceTestTracks())).toEqual(['ended'])
  complete()
  await expect(page).toHaveURL(/focus_id=qingming/)
  const input=page.getByLabel('告诉小汴梁你的问题或想调整的安排')
  await expect(input).toHaveValue('我想去清明上河园。')
  await input.fill('我想去清明上河园，轻松一点。');expect(tasks).toBe(0)
  await page.screenshot({path:'../docs/screenshots/assistant-voice-released-mobile.png'})
  const cancel=await hold(page);await expect(bubble).toContainText('正在录音 · 1 / 60 秒')
  await cancel(true);await expect(bubble).toHaveCount(0)
  expect(await page.evaluate(()=>(window as unknown as {voiceTestTracks:()=>string[]}).voiceTestTracks())).toEqual(['ended','ended'])
  await expect(input).toHaveValue('我想去清明上河园，轻松一点。')
 })
 test('拖动与取消不触发语音或导航，地图入口一致且保留景区',async({page})=>{
  let statusCalls=0
  await page.route('**/api/speech/status',route=>{statusCalls++;return route.fulfill({json:{available:false,max_seconds:60}})})
  await page.goto('/guide?scenic=qingming')
  const launcher=page.locator('.global-assistant-launcher')
  await expect(launcher).toHaveCount(1);await expect(page.locator('.xiaobianliang-launcher')).toHaveCount(0)
  const cdp=await page.context().newCDPSession(page),original=(await launcher.boundingBox())!
  const point={x:original.x+30,y:original.y+30}
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[point]})
  await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:point.x-200,y:point.y-200}]})
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]})
  await page.waitForTimeout(650);expect(statusCalls).toBe(0);await expect(page).toHaveURL(/guide/)
  const moved=(await launcher.boundingBox())!
  expect(moved.x).toBeLessThan(original.x-100);expect(moved.y).toBeLessThan(original.y-100)
  await page.reload();expect((await launcher.boundingBox())!.x).toBeCloseTo(moved.x,0)
  await launcher.tap();await expect(page).toHaveURL(/assistant\?context=map&scenic=qingming/);await cdp.detach()
 })
 test('准备阶段松手不会迟到启动，320与482宽度不溢出，键盘松开也收起',async({page})=>{
  let complete!:()=>void,mediaCalls=0
  const gate=new Promise<void>(resolve=>complete=resolve)
  await page.addInitScript(()=>{Object.assign(window,{voiceMediaCalls:0});navigator.mediaDevices.getUserMedia=async()=>{(window as unknown as {voiceMediaCalls:number}).voiceMediaCalls++;throw new Error('must not record')}})
  page.on('request',r=>{if(r.url().includes('/speech/transcriptions'))mediaCalls++})
  await page.route('**/api/speech/status',async route=>{await gate;await route.fulfill({json:{available:true,max_seconds:60}})})
  await page.goto('/');const release=await hold(page)
  await expect(page.getByText('正在准备语音输入',{exact:true})).toBeVisible()
  await release();await expect(page.getByRole('region',{name:'小汴梁语音输入',exact:true})).toHaveCount(0)
  complete();await page.unroute('**/api/speech/status',{behavior:'wait'})
  expect(await page.evaluate(()=>(window as unknown as {voiceMediaCalls:number}).voiceMediaCalls)).toBe(0)
  await page.route('**/api/speech/status',route=>route.fulfill({json:{available:false,max_seconds:60}}))
  for(const width of [320,482]){
   await page.setViewportSize({width,height:769})
   await page.locator('.global-assistant-launcher').focus();await page.keyboard.down('Shift');await page.keyboard.down('Enter')
   const bubble=page.getByRole('region',{name:'小汴梁语音输入',exact:true})
   await expect(bubble.getByRole('status',{name:'语音暂不可用'})).toBeVisible()
   await expect(bubble.locator('button,svg')).toHaveCount(0)
   const box=(await bubble.boundingBox())!,character=(await page.locator('.global-assistant-launcher').boundingBox())!
   expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(character.x)
   expect(Math.abs(box.y-character.y)).toBeLessThan(48)
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
   await page.keyboard.up('Enter');await page.keyboard.up('Shift');await expect(bubble).toHaveCount(0)
  }
  expect(mediaCalls).toBe(0)
 })
 test('权限请求期间松开，迟到的麦克风会释放且没有转写请求',async({page,context})=>{
  await context.grantPermissions(['microphone']);await available(page)
  await page.addInitScript(()=>{
   const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
   navigator.mediaDevices.getUserMedia=async constraints=>{const stream=await original(constraints);await new Promise<void>(resolve=>Object.assign(window,{releaseVoicePermission:resolve}));return stream}
  })
  let calls=0;await page.route('**/api/speech/transcriptions',route=>{calls++;return route.fulfill({json:{text:'不应出现'}})})
  await page.goto('/');const release=await hold(page)
  await expect.poll(()=>page.evaluate(()=>typeof (window as unknown as {releaseVoicePermission?:()=>void}).releaseVoicePermission)).toBe('function')
  await release();await expect(page.getByRole('region',{name:'小汴梁语音输入',exact:true})).toHaveCount(0)
  await page.evaluate(()=>(window as unknown as {releaseVoicePermission:()=>void}).releaseVoicePermission())
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {voiceTestTracks:()=>string[]}).voiceTestTracks())).toEqual(['ended'])
  expect(calls).toBe(0)
 })
})
