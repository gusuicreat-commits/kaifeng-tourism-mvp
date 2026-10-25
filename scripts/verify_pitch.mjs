import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(path.join(root,'frontend/package.json'));
const {chromium}=require('playwright');
const base=process.env.PITCH_BASE_URL||'http://127.0.0.1:8017';
const artifact=path.join(root,'frontend/public/assets/pitch/index.html');
const shots=path.join(root,'docs/screenshots/pitch-20261025');
const report={date:'2026-10-25',base,checks:[],screenshots:[],errors:[],apiRequests:[],externalRequests:[]};
const browser=await chromium.launch();
const context=await browser.newContext({viewport:{width:1600,height:900},acceptDownloads:true});
context.on('page',page=>page.on('pageerror',error=>report.errors.push(error.message)));
await context.route('**/api/**',route=>{report.apiRequests.push(route.request().url());return route.abort();});
context.on('request',request=>{const url=request.url();if(/^https?:/.test(url)&&!url.startsWith(base+'/'))report.externalRequests.push(url);});
const page=await context.newPage();
await mkdir(shots,{recursive:true});
const snapshot=()=>page.evaluate(()=>window.__pitchDemo.snapshot());
const goto=async index=>{await page.evaluate(i=>{const slides=[...document.querySelectorAll('#deck > section.slide')];go(typeof i==='string'?slides.findIndex(s=>s.dataset.slideId===i):i,{force:true});},index);await page.waitForTimeout(800);};
const record=name=>{report.checks.push({name,status:'passed'});console.log('PASS '+name);};
const capture=async name=>{await page.screenshot({path:path.join(shots,name),animations:'disabled'});report.screenshots.push('docs/screenshots/pitch-20261025/'+name);};

try {
  await page.goto(base+'/pitch',{waitUntil:'networkidle'});
  await page.waitForURL('**/assets/pitch/index.html');
  await page.waitForFunction(()=>window.__pitchDemo);
  assert.equal(await page.locator('#deck > section.slide').count(),15);
  assert.equal(await page.evaluate(()=>window.__SPEAKER_NOTES__.length),15);
  assert.deepEqual(report.apiRequests,[]);
  assert.deepEqual(report.externalRequests,[]);
  record('Production /pitch entry loads 15 slides and notes without API or external requests');

  await page.waitForTimeout(800);await page.keyboard.press('PageDown');await page.waitForTimeout(800);
  assert.equal(await page.evaluate(()=>window.__currentSlideIndex),1);
  await page.locator('#pitch-next').click();await page.waitForTimeout(800);
  await page.keyboard.press('ArrowRight');await page.waitForTimeout(800);
  assert.equal(await page.evaluate(()=>window.__currentSlideIndex),3);
  await page.keyboard.press('Escape');assert.equal(await page.locator('#overview').isVisible(),true);
  await page.keyboard.press('Escape');
  record('Keyboard, visible navigation and overview remain usable after button focus');

  assert.equal(await page.locator('#deck .screen-image img').count(),5);
  assert.equal(await page.locator('#deck .screen-image img').evaluateAll(images=>images.every(image=>image.complete&&image.naturalWidth>0)),true);
  await goto('product-interface');
  await page.locator('#deck [data-slide-id="product-interface"] .screen-open').first().click();
  assert.equal(await page.locator('.screenshot-viewer').isVisible(),true);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.screenshot-viewer').isVisible(),false);
  assert.equal(await page.evaluate(()=>window.__currentSlideIndex),4);
  record('Five original screenshots load; full-size view closes with Escape without changing slides');


  await goto('route-demo');
  await page.locator('#deck > section[data-slide-id="route-demo"] [data-choice="night"]').click();
  const original=await snapshot();
  await page.locator('#generate-demo').click();
  let state=await snapshot();
  assert.equal(state.candidate.length,2);
  assert.deepEqual(state.confirmed,original.confirmed);
  assert.equal(state.version,original.version);
  await page.locator('#adopt-demo').click();await page.waitForTimeout(800);
  state=await snapshot();
  assert.equal(state.confirmed.length,2);assert.equal(state.version,2);
  record('Candidate generation does not change confirmed demo state; adoption requires a click');

  await goto('adjust-demo');
  const before=await snapshot();
  await page.locator('#preview-adjustment').click();
  assert.equal((await snapshot()).version,before.version);
  assert.equal(await page.locator('#confirmed-demo-route .is-new').count(),0);
  assert.equal(await page.locator('#candidate-demo-route .is-new').count(),1);
  await page.locator('#cancel-adjustment').click();
  assert.deepEqual(await snapshot(),before);
  await page.locator('#preview-adjustment').click();
  await capture('07-adjustment-preview-desktop.png');
  await page.locator('#confirm-adjustment').click();
  state=await snapshot();assert.equal(state.version,3);assert.equal(state.rest,true);
  assert.deepEqual(state.confirmed,before.confirmed);
  assert.equal(await page.locator('#confirmed-demo-route .is-completed').count(),1);
  await page.evaluate(()=>document.getElementById('confirm-adjustment').click());
  assert.equal((await snapshot()).version,3);
  await page.locator('#reset-demo').click();
  record('Adjustment preview/cancel preserve progress and version; confirmation increments once');

  const layouts=[];
  for(const viewport of [{width:1600,height:900},{width:1280,height:720},{width:1024,height:768},{width:390,height:844},{width:320,height:740}]){
    await page.setViewportSize(viewport);
    for(let i=0;i<15;i++){
      await goto(i);
      const metrics=await page.evaluate(()=>{
        const s=document.querySelectorAll('#deck > section.slide')[window.__currentSlideIndex];
        const r=s.getBoundingClientRect();
        const bad=[];
        if(innerWidth>900){
          const walker=document.createTreeWalker(s,NodeFilter.SHOW_TEXT);
          while(walker.nextNode()){
            const node=walker.currentNode,el=node.parentElement;
            if(!node.textContent.trim()||el.closest('[hidden],svg,canvas')||getComputedStyle(el).visibility==='hidden'||getComputedStyle(el).display==='none')continue;
            if(!el.getClientRects().length)continue;
            const range=document.createRange();range.selectNodeContents(node);
            for(const box of range.getClientRects()){
              if(box.width&&box.height&&(box.bottom>innerHeight-50||box.top<0||box.left<r.left-1||box.right>r.right+1))bad.push({text:node.textContent.trim().slice(0,32),x:box.x,y:box.y,bottom:box.bottom});
            }
          }
        }
        return {id:s.dataset.slideId,width:s.clientWidth,scrollWidth:s.scrollWidth,bad};
      });
      assert.ok(metrics.scrollWidth<=metrics.width+1,`${viewport.width}px ${metrics.id} horizontal overflow`);
      assert.deepEqual(metrics.bad,[],`${viewport.width}x${viewport.height} ${metrics.id} text outside slide safe area`);
      if(viewport.width===1600){
        await page.waitForTimeout(800);
        await capture(`${String(i+1).padStart(2,'0')}-${metrics.id}-desktop.png`);
      }
      if(viewport.width===390&&i===6){
        await page.locator('#deck > section.slide').nth(i).evaluate(el=>{el.scrollTop=el.scrollHeight;});
        await capture('06-route-demo-mobile.png');
      }
    }
    layouts.push(viewport);
  }
  report.viewports=layouts;
  record('All 15 slides fit desktop/projector widths and have no horizontal overflow at 320/390px');

  await page.setViewportSize({width:1600,height:900});await goto('adjust-demo');
  const popup=context.waitForEvent('page');await page.locator('#ppt-presenter-btn').click();const audience=await popup;
  await audience.waitForFunction(()=>window.__pitchDemo);
  await page.waitForFunction(()=>window.__pptPresenter.debugState().syncState==='synced');
  assert.equal(await page.locator('#ppt-note-editor').inputValue().then(x=>x.includes('预览')),true);
  assert.equal(await page.evaluate(()=>window.__pptPresenter.debugState().timerRunning),false);
  await page.locator('#pitch-presenter-controls [data-demo-action="preview"]').click();
  await audience.waitForFunction(()=>window.__pitchDemo.snapshot().preview);
  assert.equal((await audience.evaluate(()=>window.__pitchDemo.snapshot())).version,1);
  await page.locator('#pitch-presenter-controls [data-demo-action="confirm"]').click();
  await audience.waitForFunction(()=>window.__pitchDemo.snapshot().version===2);
  const ratios=await page.locator('.ppt-frame').evaluateAll(frames=>frames.map(frame=>frame.clientWidth/frame.clientHeight));
  ratios.forEach(ratio=>assert.ok(Math.abs(ratio-16/9)<.02));
  await page.locator('#ppt-timer-toggle').click();assert.equal(await page.evaluate(()=>window.__pptPresenter.debugState().timerRunning),true);
  await page.locator('#ppt-timer-toggle').click();
  await page.locator('#ppt-grid').click();await page.locator('.ppt-overview-page').nth(6).click();
  assert.equal(await page.evaluate(()=>window.__currentSlideIndex),6);
  await capture('presenter-desktop.png');
  await page.locator('#ppt-exit').click();
  record('Presenter notes, manual timer, overview, 16:9 previews and audience demo-state sync work');

  await goto('closing');
  const downloaded=page.waitForEvent('download');await page.locator('#download-deck').click();const download=await downloaded;
  assert.equal(download.suggestedFilename(),'汴梁游记-路演.html');
  assert.deepEqual(await readFile(await download.path()),await readFile(artifact));
  const offline=await browser.newContext({offline:true,viewport:{width:1280,height:720}});
  const local=await offline.newPage();const localErrors=[];local.on('pageerror',e=>localErrors.push(e.message));
  await local.goto(pathToFileURL(artifact).href);await local.waitForFunction(()=>window.__pitchDemo);
  assert.equal(await local.locator('#deck > section.slide').count(),15);
  await local.evaluate(()=>go(7,{force:true}));await local.waitForTimeout(800);
  await local.locator('#preview-adjustment').click();await local.locator('#confirm-adjustment').click();
  assert.equal(await local.evaluate(()=>window.__pitchDemo.snapshot().version),2);
  assert.deepEqual(localErrors,[]);await offline.close();
  record('Downloaded HTML is self-contained and the interaction works offline over file://');

  assert.deepEqual(report.errors,[]);assert.deepEqual(report.apiRequests,[]);assert.deepEqual(report.externalRequests,[]);
  record('No browser script errors, business API requests or external resource requests');
  report.status='passed';
} catch(error){report.status='failed';report.failure=error.stack;process.exitCode=1;console.error(error);}
finally{
  await browser.close();
  await mkdir(path.join(root,'docs/reports'),{recursive:true});
  await writeFile(path.join(root,'docs/reports/pitch-deck-20261025.json'),JSON.stringify(report,null,2)+'\n');
}
