import {test,expect,type Page} from '@playwright/test'
import {mapPoints,locationUsable,navigationUrl,gcjPosition} from '../src/amap'

// All coordinates and facts below are SYNTHETIC UI fixtures, not a Kaifeng dataset.
function fixture(){
 const t=new Date(),expires=new Date(t.getTime()+3600000).toISOString(),stamp=t.toISOString()
 const items=['已完成测试点','下一站测试舞台','未核验测试入口','无坐标测试点','万岁山合成视角点','清明上河园合成视角点'].map((name,i)=>({ready:i!==2,issues:[],record:{kind:'place',fact_id:'p'+i,name,place_type:i===2?'entrance':i===1?'stage':'poi',longitude:i===3?null:114.30+i*.001,latitude:i===3?null:34.80,coordinate_system:'GCJ02',validation_status:i===2?'unverified':'verified',business_status:'active',source_ids:['synthetic'],is_demo:false,effective_to:expires}}))
 items.push({ready:true,issues:[],record:{...items[0]!.record,fact_id:'bad',name:'错误坐标系测试点',coordinate_system:'unknown'}})
 const stops=[{stop_id:'g0',place_id:'p0',progress:'completed',locked:false},{stop_id:'g1',place_id:'p1',progress:'pending',locked:true},{stop_id:'g3',place_id:'p3',progress:'pending',locked:false}]
 const trip={id:'test-map-trip',version:3,route_version:2,status:'ACTIVE',draft:{stops,business_date:new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(t)},anchor:null}
 const route={trip_id:trip.id,version:3,route_version:2,itinerary:{activities:stops.map(s=>({goal_id:s.stop_id,place_id:s.place_id,required:s.locked,starts_at:stamp,departs_at:expires})),ongoing:null,legs:[],returns_at:expires}}
 return {items,trip,route}
}
async function mock(page:Page,options:{config?:boolean;geo?:string;route?:string;sdk?:string}={}){
 const data=fixture();await page.route('**/api/maps/config',r=>r.fulfill({json:{configured:options.config!==false,key:options.config===false?null:'synthetic-browser-key',service_host:'/api/maps/_AMapService'}}))
 await page.route('**/api/city/v1/facts',r=>r.fulfill({json:{items:data.items}}))
 await page.route('**/api/city/v1/trips/current',r=>r.fulfill({json:{trip:data.trip}}))
 await page.route('**/api/city/v1/trips/test-map-trip/engine/current',r=>r.fulfill({json:data.route}))
 await page.route('**/api/city/v1/trips/test-map-trip/basket',r=>r.fulfill({json:{items:[],version:0}}))
 await page.route('https://webapi.amap.com/maps?**',r=>options.sdk==='fail'?r.abort():r.fulfill({contentType:'application/javascript',body:`
 window.__maps={created:0,destroyed:0,geo:0,search:0,mode:'',clears:0};
 window.AMap={
 Map:class{constructor(el,opts){this.el=el;this.events={};window.__maps.created++;el.innerHTML='<button aria-label="合成地图点选" style="margin:70px 20px" type="button">模拟地图点击</button>';el.firstChild.onclick=()=>this.events.click?.({lnglat:{lng:114.31,lat:34.81}})}on(e,f){this.events[e]=f;if(e==='complete')setTimeout(f,10)}add(){}setCenter(){}setZoomAndCenter(z,p){window.__maps.center=p}destroy(){window.__maps.destroyed++;this.el.innerHTML=''}},
 Marker:class{on(){}setMap(){}},
 Geolocation:class{getCurrentPosition(cb){window.__maps.geo++;setTimeout(()=>{${options.geo==='denied'?"cb('error',{})":`cb('complete',{position:{lng:114.31,lat:34.81},accuracy:${options.geo==='coarse'?'null':'20'},location_type:'${options.geo==='coarse'?'ip':'html5'}'})`}},10)}},
 plugin(n,cb){setTimeout(cb,0)},convertFrom(p,t,cb){setTimeout(()=>cb('complete',{locations:[{lng:p[0]+.001,lat:p[1]+.001}]}),0)}
 };
 for(const mode of ['Walking','Driving','Transfer'])window.AMap[mode]=class{constructor(o){this.map=o.map}setMap(m){this.map=m}clear(){window.__maps.clears++}search(a,b,cb){window.__maps.search++;window.__maps.mode=mode;setTimeout(()=>{${options.route==='fail'?"cb('error',{})":"cb('complete',{routes:[{distance:420,time:360}],plans:[{distance:850,time:600}]})"}},${options.route==='slow'?'700':'10'})}};
 `}))
 await page.goto('/guide?mode=city')
 return data
}

test('375px: formal markers, next/completed/locked, unknown points omitted, three real map views',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await mock(page)
 await expect(page.getByTestId('amap-canvas')).toHaveAttribute('data-loaded','true')
 await expect(page.locator('.map-place-picks')).toContainText('已完成')
 await expect(page.locator('.map-place-picks')).toContainText('下一站')
 await expect(page.locator('.map-place-picks')).toContainText('锁定目标')
 await expect(page.locator('.map-place-picks')).not.toContainText('未核验测试入口')
 await expect(page.locator('.map-place-picks')).not.toContainText('错误坐标系')
 await expect(page.getByText(/以下安排缺少有效坐标/)).toContainText('无坐标测试点')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 await page.screenshot({path:'../.test-results/amap-m42-synthetic-mobile.png',fullPage:true})
 await page.getByRole('tab',{name:'万岁山大宋武侠城',exact:true}).click()
 await expect(page).toHaveURL(/scenic=wansui/);await expect(page.getByTestId('amap-canvas')).toHaveAttribute('data-loaded','true')
 await expect.poll(()=>page.evaluate(()=>(window as any).__maps.center)).toEqual([114.304,34.8])
 await page.getByRole('tab',{name:'清明上河园',exact:true}).click();await expect(page).toHaveURL(/scenic=qingming/)
 expect(await page.evaluate(()=>(window as any).__maps.created)).toBe(1)
 expect(errors).toEqual([])
})

test('single active geolocation, coordinates/accuracy/source and route plugins, no automatic persistence',async({page})=>{
 await mock(page);let writes=0;page.on('request',r=>{if(r.method()!=='GET'&&r.url().includes('/api/city/'))writes++})
 await expect(page.getByTestId('amap-canvas')).toHaveAttribute('data-loaded','true')
 expect(await page.evaluate(()=>(window as any).__maps.geo)).toBe(0)
 await page.getByRole('button',{name:'我在这里',exact:true}).click()
 await expect(page.getByTestId('location-card')).toContainText('20 米')
 await expect(page.getByTestId('location-card')).toContainText('GCJ02')
 await expect(page.getByTestId('location-card')).toContainText('融合定位')
 for(const [value,name] of [['walking','Walking'],['driving','Driving'],['transit','Transfer']]){
  await page.getByLabel('交通方式',{exact:true}).selectOption(value)
  await page.getByRole('button',{name:'预览前往路线',exact:true}).click()
  await expect(page.getByText(/地图查询预览/)).toBeVisible()
  expect(await page.evaluate(()=>(window as any).__maps.mode)).toBe(name)
 }
 const href=await page.getByRole('link',{name:'在高德查看路线',exact:true}).getAttribute('href'),url=new URL(href!)
 expect(url.origin+url.pathname).toBe('https://uri.amap.com/navigation');expect(url.searchParams.get('mode')).toBe('bus');expect(url.searchParams.get('coordinate')).toBe('gaode');expect(url.searchParams.get('from')).toContain('114.31,34.81')
 expect(writes).toBe(0)
 expect(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).not.toContain('114.31')
})

test('permission denied then manual map click requires explicit confirmation',async({page})=>{
 await mock(page,{geo:'denied'});await page.getByRole('button',{name:'我在这里',exact:true}).click()
 await expect(page.getByText(/定位失败或权限被拒绝/)).toBeVisible()
 await page.getByLabel('合成地图点选').click();await expect(page.getByTestId('location-card')).toHaveCount(0)
 await page.getByRole('button',{name:'确认作为临时出发位置',exact:true}).click()
 await expect(page.getByTestId('location-card')).toContainText('手动地图选点')
 await expect(page.getByTestId('location-card')).toContainText('精度：未知')
 await expect(page.getByRole('button',{name:'预览前往路线',exact:true})).toBeEnabled()
 await page.getByRole('button',{name:'清除临时位置',exact:true}).click()
 await expect(page.getByTestId('location-card')).toHaveCount(0)
})

test('coarse IP never becomes precise GPS or a usable route origin',async({page})=>{
 await mock(page,{geo:'coarse'});await page.getByRole('button',{name:'我在这里',exact:true}).click()
 await expect(page.getByTestId('location-card')).toContainText('精度：未知')
 await expect(page.getByRole('button',{name:'预览前往路线',exact:true})).toBeDisabled()
 expect(new URL((await page.getByRole('link',{name:'在高德查看路线',exact:true}).getAttribute('href'))!).searchParams.has('from')).toBeFalsy()
})

test('no JS key: graceful fallback, known-place search still usable',async({page})=>{
 await mock(page,{config:false});await expect(page.getByText('地图尚未配置，仍可查看地点资料和行程')).toBeVisible()
 await expect(page.getByRole('button',{name:'我在这里',exact:true})).toBeDisabled()
 await page.getByLabel('搜索已知地点').fill('下一站');await expect(page.locator('.map-place-picks button')).toHaveCount(1)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})

test('route failure and stale delayed response cannot replace changed destination',async({page})=>{
 await mock(page,{route:'slow'});await expect(page.getByTestId('amap-canvas')).toHaveAttribute('data-loaded','true')
 await page.getByRole('button',{name:'选择此地点为出发位置',exact:true}).click();await page.getByRole('button',{name:'确认作为临时出发位置',exact:true}).click()
 await page.getByRole('button',{name:'预览前往路线',exact:true}).click()
 await page.locator('.map-place-picks button').filter({hasText:'已完成测试点'}).click()
 await page.waitForTimeout(900);await expect(page.getByText(/地图查询预览/)).toHaveCount(0)
 expect(await page.evaluate(()=>(window as any).__maps.clears)).toBeGreaterThan(0)
 await page.getByRole('link',{name:'返回景区探索',exact:true}).click();expect(await page.evaluate(()=>(window as any).__maps.destroyed)).toBe(1)
})

test('unavailable route clears query state',async({page})=>{
 await mock(page,{route:'fail'});await expect(page.getByTestId('amap-canvas')).toHaveAttribute('data-loaded','true');await page.getByRole('button',{name:'选择此地点为出发位置',exact:true}).click();await page.getByRole('button',{name:'确认作为临时出发位置',exact:true}).click();await page.getByRole('button',{name:'预览前往路线',exact:true}).click();await expect(page.getByText('没有可用路线或服务暂不可用')).toBeVisible()
})

test('REAL SDK: online city tiles and at most three plugin queries (explicit opt-in)',async({page})=>{
 test.skip(process.env.AMAP_LIVE_TEST!=='1','真实在线验收只显式运行')
 test.setTimeout(90000)
 const fs=await import('node:fs/promises')
 const observations:any[]=[]
 page.on('response',async r=>{const u=new URL(r.url());if(u.hostname.endsWith('amap.com')||u.hostname.endsWith('autonavi.com')||u.pathname.startsWith('/api/maps/')){const entry:any={host:u.hostname,path:u.pathname,status:r.status()};if(u.pathname.includes('/_AMapService/')){entry.parameter_names=[...u.searchParams.keys()];entry.callback_shape=u.searchParams.get('callback')?.replace(/[A-Za-z0-9]/g,'x');if(r.status()===400)entry.rejection=await r.json().catch(()=>({detail:'unreadable'}))}observations.push(entry)}})
 const report:any={kind:'live_sdk',synthetic_business_data:true,real_device:false,started_at:new Date().toISOString(),sdk:'2.0',status:'BLOCKED',key_logged:false,queries:[],network:observations}
 try{
  const data=fixture()
  await page.route('**/api/city/v1/facts',r=>r.fulfill({json:{items:data.items}}))
  await page.route('**/api/city/v1/trips/current',r=>r.fulfill({json:{trip:data.trip}}))
  await page.route('**/api/city/v1/trips/test-map-trip/engine/current',r=>r.fulfill({json:data.route}))
  await page.route('**/api/city/v1/trips/test-map-trip/basket',r=>r.fulfill({json:{items:[],version:0}}))
  await page.goto('/guide?mode=city')
  const config=await(await page.request.get('/api/maps/config')).json();report.configured=config.configured
  expect(config.configured).toBeTruthy()
  await expect(page.getByTestId('amap-canvas')).toHaveAttribute('data-loaded','true',{timeout:30000})
  report.map_complete=true
  report.security=await page.evaluate(()=>({serviceHost:new URL((window as any)._AMapSecurityConfig.serviceHost).pathname,securityCodeExposed:!!(window as any)._AMapSecurityConfig.securityJsCode}))
  report.canvas_count=await page.locator('.amap-live-canvas canvas').count()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
  // Coordinates are city-level test points, not certified scenic entrances. No data saved.
  report.queries=await page.evaluate(async()=>{
   const A=(window as any).AMap,results=[]
   for(const name of ['Walking','Driving','Transfer']){
    const result=await new Promise(resolve=>{const timer=setTimeout(()=>resolve({plugin:name,status:'timeout'}),12000);A.plugin('AMap.'+name,()=>{new A[name]({city:'开封'}).search([114.307,34.799],[114.315,34.802],(status:string,data:any)=>{clearTimeout(timer);const r=name==='Transfer'?data?.plans?.[0]:data?.routes?.[0];const info=typeof data==='string'?data:data?.info;const error_code=typeof info==='string'&&/^[A-Z0-9_ :.-]{1,120}$/.test(info)?info:'not_reported';resolve({plugin:name,status,error_code,has_route:!!r,has_distance:Number.isFinite(Number(r?.distance)),has_time:Number.isFinite(Number(r?.time))})})})})
    results.push(result)
   }
   return results
  })
  await page.locator('.map-place-picks button').filter({hasText:'已完成测试点'}).click()
  await page.getByRole('button',{name:'选择此地点为出发位置',exact:true}).click()
  await page.getByRole('button',{name:'确认作为临时出发位置',exact:true}).click()
  await page.locator('.map-place-picks button').filter({hasText:'下一站测试舞台'}).click()
  await page.getByRole('button',{name:'预览前往路线',exact:true}).click()
  await expect(page.getByText(/地图查询预览/)).toBeVisible({timeout:20000})
  report.ui_walking_preview=true
  await page.getByTestId('amap-canvas').scrollIntoViewIfNeeded()
  await page.waitForTimeout(3000)
  await page.screenshot({path:'../.test-results/amap-m42-real-mobile.png',fullPage:true})
  report.status=report.queries.every((r:any)=>r.status==='complete'&&r.has_route)?'PASS':'PARTIAL'
  expect(report.status).toBe('PASS')
 }catch(e){report.failure=(e as Error).message.split('\n')[0];throw e}
 finally{await fs.mkdir('../docs/reports',{recursive:true});await fs.writeFile('../docs/reports/amap-map-location-live.json',JSON.stringify(report,null,2))}
})


test('formal state utilities reject stale, wrong-version, demo, cancelled and unsupported coordinates',()=>{
 const d=fixture();const safe=mapPoints(d.items as any,d.trip as any,d.route as any)
 expect(safe.find(p=>p.id==='p1')?.state).toBe('下一站')
 for(const route of [null,{...d.route,stale:true},{...d.route,route_version:9}])expect(mapPoints(d.items as any,d.trip as any,route as any).every(p=>p.state==='已有地点')).toBeTruthy()
 const bad=d.items.map(p=>({...p,record:{...p.record,is_demo:true}}))
 expect(mapPoints(bad as any,d.trip as any,d.route as any)).toEqual([])
})

test('ongoing activity stored outside remaining activities is visible and next stays distinct',()=>{
 const d=fixture(),original=d.route.itinerary.activities[1]!
 const r={...d.route,itinerary:{...d.route.itinerary,activities:d.route.itinerary.activities.filter(a=>a.goal_id!=='g1'),ongoing:{stop_id:'g1',original_activity:original}}}
 const points=mapPoints(d.items as any,d.trip as any,r as any)
 expect(points.find(p=>p.id==='p1')?.state).toBe('正在游览')
})

test('position freshness, coarse accuracy, coordinate conversion and navigation encoding boundaries',async()=>{
 const p:any={position:[114.3,34.8],coordinate_system:'GCJ02',source:'amap_geolocation',method:'html5',accuracy_m:20,obtained_at:new Date().toISOString()}
 expect(locationUsable(p)).toBeTruthy()
 expect(locationUsable({...p,obtained_at:new Date(Date.now()-900001).toISOString()})).toBeFalsy()
 expect(locationUsable({...p,obtained_at:new Date(Date.now()+900000).toISOString()})).toBeFalsy()
 expect(locationUsable({...p,accuracy_m:null})).toBeFalsy()
 expect(locationUsable({...p,method:'ip'})).toBeFalsy()
 const u=new URL(navigationUrl([114.3,34.8],'名称 & = / , 测试','walking',p))
 expect(u.searchParams.get('to')).toBe('114.3,34.8,名称 & = / ， 测试')
 const point:any={position:[114.3,34.8],coordinateSystem:'WGS84'}
 await expect(gcjPosition({convertFrom:(p:any,t:any,cb:any)=>{expect(t).toBe('gps');cb('error',{})}},point)).rejects.toThrow('坐标转换失败')
 await expect(gcjPosition({convertFrom:(p:any,t:any,cb:any)=>cb('complete',{locations:[{lng:114.4,lat:34.9}]})},point)).resolves.toEqual([114.4,34.9])
})

test('SDK network failure remains recoverable without duplicate scripts or automatic location',async({page})=>{
 await mock(page,{sdk:'fail'});await expect(page.getByText('高德地图加载失败，请检查网络')).toBeVisible()
 await page.getByRole('button',{name:'重试地图',exact:true}).click();await expect(page.getByText('高德地图加载失败，请检查网络')).toBeVisible()
 expect(await page.locator('script[data-bianliang-amap]').count()).toBe(0)
 await expect(page.getByRole('button',{name:'我在这里',exact:true})).toBeDisabled()
})

test('only explicit known-place save uses existing CAS API; temporary coordinates are absent',async({page})=>{
 const d=await mock(page);let body:any=null
 await page.route('**/api/city/v1/trips/test-map-trip',async r=>{body=r.request().postDataJSON();await r.fulfill({json:{...d.trip,version:4,anchor:{place_id:'p1',confirmed_at:new Date().toISOString(),source:'manual_known_place'}}})})
 await expect(page.getByTestId('amap-canvas')).toHaveAttribute('data-loaded','true')
 expect(body).toBeNull()
 await page.getByRole('button',{name:'确认我在此已知地点，并设为旅行当前位置',exact:true}).click()
 await expect(page.getByText('已保存手动已知地点，小汴梁会读取同一旅行位置')).toBeVisible()
 expect(body.expected_version).toBe(3);expect(body.action.operation).toBe('set_anchor')
 expect(Object.keys(body.action.anchor).sort()).toEqual(['confirmed_at','place_id'])
 expect(body.action.anchor.place_id).toBe('p1')
 await page.getByRole('button',{name:'使用已保存位置',exact:true}).click()
 await expect(page.getByTestId('location-card')).toContainText('手动已知地点')
})
