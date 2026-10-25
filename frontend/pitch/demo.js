(() => {
  const $ = id => document.getElementById(id);
  const deckSlides = [...document.querySelectorAll('#deck > section.slide')];
  const routeIndex = deckSlides.findIndex(slide=>slide.dataset.slideId==='route-demo');
  const adjustIndex = deckSlides.findIndex(slide=>slide.dataset.slideId==='adjust-demo');
  const initial = () => ({choices:['show','food','night'],candidate:null,confirmed:['show','food','night'],version:1,rest:false,preview:false});
  let state = initial();
  const names = {show:'景区演出',food:'特色用餐',night:'城市夜游'};
  const mode = new URLSearchParams(location.search).get('mode') || 'deck';
  const owner = !['preview','audience'].includes(mode);
  const session = window.__pptPresenter.debugState().session;
  const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('bianliang-pitch-demo:' + session) : null;

  function stopRows(items, rest) {
    const rows = items.flatMap((key,index) => {
      const stop = `<div class="stop-row${index===0?' is-completed':''}"><span>${String(index+1).padStart(2,'0')}</span><span>${names[key]}</span><small>${index===0?'已完成':key==='night'?'自选':'必选保留'}</small></div>`;
      return rest && key==='food' ? [`<div class="stop-row is-new"><span>＋</span><span>休息</span><small>新增</small></div>`,stop] : [stop];
    });
    return rows.join('');
  }

  function render() {
    document.querySelectorAll('[data-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(state.choices.includes(button.dataset.choice)));
      button.disabled = button.dataset.choice !== 'night';
    });
    $('selection-summary').textContent = `已选 ${state.choices.length} 项体验 · 演出与用餐为必选`;
    const displayed = state.candidate || state.confirmed;
    const night = displayed.includes('night');
    $('night-marker').hidden = !night;
    $('route-line').setAttribute('d', night ? 'M105 270 L310 270 L310 130 L600 130' : 'M105 270 L310 270 L310 130');
    $('route-map').classList.toggle('has-candidate', !!state.candidate);
    $('demo-route-status').textContent = state.candidate ? '示例候选 · 等待演示确认' : '选择体验后查看候选';
    $('adopt-demo').hidden = !state.candidate;
    $('confirmed-demo-route').innerHTML = stopRows(state.confirmed, state.rest);
    $('candidate-demo-route').innerHTML = stopRows(state.confirmed, true);
    $('demo-version').textContent = `演示版本 ${state.version}`;
    $('adjust-idle').hidden = state.preview || state.rest;
    $('adjust-preview').hidden = !state.preview;
    $('adjust-complete').hidden = !state.rest;
    $('adjustment-status').textContent = state.preview
      ? `候选预览中 · 当前演示版本仍为 ${state.version}，必选体验与已完成进度保留。`
      : state.rest ? `已确认演示修改 · 演示版本 ${state.version}，真实行程未被写入。`
      : '合成交互示例 · 仅修改当前页面的演示状态。';
    presenterControls();
  }

  function update(change) {
    state = {...state,...change};
    render();
    channel?.postMessage({type:'state',state});
  }
  const adopt = document.createElement('button');
  adopt.id = 'adopt-demo'; adopt.type = 'button'; adopt.className = 'pitch-button secondary';
  adopt.textContent = '采用此演示候选'; adopt.hidden = true;
  $('generate-demo').after(adopt);
  $('experience-options').addEventListener('click', event => {
    const button = event.target.closest('[data-choice]');
    if (!button || button.disabled) return;
    const key = button.dataset.choice;
    update({choices:state.choices.includes(key)?state.choices.filter(x=>x!==key):[...state.choices,key],candidate:null});
  });
  $('generate-demo').onclick = () => update({candidate:[...state.choices]});
  adopt.onclick = () => {
    if (!state.candidate) return;
    update({confirmed:[...state.candidate],candidate:null,version:state.version+1,rest:false,preview:false});
    go(adjustIndex,{force:true});
  };
  $('preview-adjustment').onclick = () => {if(!state.rest) update({preview:true});};
  $('cancel-adjustment').onclick = () => update({preview:false});
  $('confirm-adjustment').onclick = () => {
    if(state.preview && !state.rest) update({rest:true,preview:false,version:state.version+1});
  };
  $('reset-demo').onclick = () => update(initial());
  channel?.addEventListener('message', event => {
    if(event.data?.type==='request' && owner) channel.postMessage({type:'state',state});
    if(event.data?.type==='state' && !owner){state=event.data.state;render();}
  });
  if(!owner) channel?.postMessage({type:'request'});

  function presenterControls() {
    if(!document.body.classList.contains('ppt-presenter'))return;
    let box=$('pitch-presenter-controls');
    if(!box){
      box=document.createElement('div');box.id='pitch-presenter-controls';
      box.innerHTML='<strong>交互示例控制</strong><div data-presenter-route><button class="ppt-ui-btn" data-demo-action="night">切换夜游</button><button class="ppt-ui-btn" data-demo-action="generate">查看候选</button><button class="ppt-ui-btn" data-demo-action="adopt">采用候选</button></div><div data-presenter-adjust><button class="ppt-ui-btn" data-demo-action="preview">预览休息</button><button class="ppt-ui-btn" data-demo-action="cancel">取消</button><button class="ppt-ui-btn" data-demo-action="confirm">确认修改</button><button class="ppt-ui-btn" data-demo-action="reset">重置</button></div>';
      document.querySelector('.ppt-notes-head')?.append(box);
      const targets={night:'experience-options',generate:'generate-demo',adopt:'adopt-demo',preview:'preview-adjustment',cancel:'cancel-adjustment',confirm:'confirm-adjustment',reset:'reset-demo'};
      box.addEventListener('click',event=>{
        const action=event.target.closest('[data-demo-action]')?.dataset.demoAction;
        if(!action)return;
        if(action==='night')document.querySelector('[data-choice="night"]').click();
        else $(targets[action])?.click();
      });
    }
    const index=window.__currentSlideIndex;
    box.hidden=![routeIndex,adjustIndex].includes(index);
    box.querySelector('[data-presenter-route]').hidden=index!==routeIndex;
    box.querySelector('[data-presenter-adjust]').hidden=index!==adjustIndex;
    box.querySelector('[data-demo-action="adopt"]').disabled=!state.candidate;
    box.querySelector('[data-demo-action="preview"]').disabled=state.rest||state.preview;
    box.querySelector('[data-demo-action="cancel"]').disabled=!state.preview;
    box.querySelector('[data-demo-action="confirm"]').disabled=!state.preview;
  }
  window.__pitchRenderPresenter=presenterControls;
  new MutationObserver(presenterControls).observe(document.body,{attributes:true,attributeFilter:['class']});

  const panel = $('route-map'), drawing = panel.querySelector('.map-drawing');
  let zoom=1,offset={x:0,y:0},dragging=false,start=null;
  const transform = () => {drawing.style.transform=`translate(${offset.x}px,${offset.y}px) scale(${zoom})`;};
  panel.querySelectorAll('[data-map-ctrl]').forEach(button => button.addEventListener('click', () => {
    const action=button.dataset.mapCtrl;
    if(action==='zoom-in') zoom=Math.min(1.6,zoom+.15);
    if(action==='zoom-out'){zoom=Math.max(1,zoom-.15);if(zoom===1)offset={x:0,y:0};}
    if(action==='drag'){
      dragging=!dragging;panel.dataset.draggable=String(dragging);
      button.setAttribute('aria-pressed',String(dragging));button.classList.toggle('active',dragging);
      button.textContent=dragging?'DRAG ON':'DRAG';
    }
    transform();
  }));
  drawing.addEventListener('pointerdown',event=>{if(!dragging)return;start={x:event.clientX-offset.x,y:event.clientY-offset.y};drawing.setPointerCapture(event.pointerId);});
  drawing.addEventListener('pointermove',event=>{if(!start)return;offset={x:Math.max(-120,Math.min(120,event.clientX-start.x)),y:Math.max(-100,Math.min(100,event.clientY-start.y))};transform();});
  drawing.addEventListener('pointerup',()=>{start=null;});
  drawing.addEventListener('pointercancel',()=>{start=null;});
  drawing.addEventListener('lostpointercapture',()=>{start=null;});

  const controls=document.createElement('div');controls.id='pitch-step-controls';controls.setAttribute('aria-label','路演翻页');
  controls.innerHTML='<button type="button" aria-label="上一页" id="pitch-prev">←</button><span id="pitch-position" aria-live="polite"></span><button type="button" aria-label="下一页" id="pitch-next">→</button>';
  document.body.append(controls);
  $('pitch-prev').onclick=()=>go(window.__currentSlideIndex-1,{force:true});
  $('pitch-next').onclick=()=>go(window.__currentSlideIndex+1,{force:true});
  function position(){const i=window.__currentSlideIndex||0,total=deckSlides.length;$('pitch-position').textContent=`${String(i+1).padStart(2,'0')} / ${total}`;$('pitch-prev').disabled=i===0;$('pitch-next').disabled=i===total-1;document.querySelectorAll('#nav button').forEach((button,n)=>{if(i===n)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});}
  addEventListener('pitch-slide-change',position);
  document.querySelectorAll('[data-goto]').forEach(button=>button.onclick=()=>{
    const destination=button.dataset.goto;
    const index=/^\d+$/.test(destination)?Number(destination):deckSlides.findIndex(slide=>slide.dataset.slideId===destination);
    if(index>=0)go(index,{force:true});
  });
  document.querySelectorAll('#nav button').forEach((button,index)=>{button.setAttribute('aria-label',`第 ${index+1} 页：${SPEAKER_NOTES[index].title}`);button.title=SPEAKER_NOTES[index].title;});
  const fullscreen=document.createElement('button');fullscreen.type='button';fullscreen.id='pitch-fullscreen';fullscreen.textContent='全屏';
  fullscreen.onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{fullscreen.textContent='请用浏览器全屏';}};
  $('hint').prepend(fullscreen);
  const viewer=document.createElement('dialog');viewer.className='screenshot-viewer';viewer.setAttribute('aria-label','产品截图原图');
  viewer.innerHTML='<div class="screenshot-viewer-head"><span>产品截图 · 原图</span><button type="button" aria-label="关闭原图">关闭 ×</button></div><div class="screenshot-viewer-content"><img alt=""></div>';
  document.body.append(viewer);
  viewer.querySelector('button').onclick=()=>viewer.close();
  viewer.addEventListener('click',event=>{if(event.target===viewer)viewer.close();});
  document.querySelectorAll('.screen-figure').forEach(figure=>{
    const image=figure.querySelector('img'),button=document.createElement('button');
    button.className='screen-open';button.type='button';button.textContent='查看原图 ↗';button.setAttribute('aria-label','查看原图：'+image.alt);
    figure.querySelector('figcaption').append(button);
    button.onclick=()=>{const full=viewer.querySelector('img');full.src=image.src;full.alt=image.alt;viewer.showModal();};
  });
  $('download-deck').onclick=()=>{const a=document.createElement('a'),url=new URL(location.href);url.search='';url.hash='';a.href=url.href;a.download='汴梁游记-路演.html';a.click();};
  if(location.protocol==='file:')document.querySelectorAll('[data-live-link]').forEach(link=>{link.hidden=true;});
  if(matchMedia('(prefers-reduced-motion: reduce)').matches)window.__setLowPowerMode(true,{persist:false});
  window.__pitchDemo={snapshot:()=>JSON.parse(JSON.stringify(state))};
  render();position();
})();
