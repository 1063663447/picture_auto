/* Small view helpers; generation, settings and original features stay in app.js. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const terminal = ['done','partial','failed','canceled'];
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  let api;
  let filter='all';
  let observedJob='';
  let waitTimer;
  let runStarted=0;
  let lastFinished=0;
  let lastArrival=0;
  let modalIndex=0;
  let drawerInvoker;
  let openPanel;
  const cardObserver = new IntersectionObserver(rows => {
    for(const row of rows)row.target.classList.toggle('in-viewport', row.isIntersecting);
    window.PrismFX?.invalidate();
  },{root:$('galleryScroll'),threshold:0,rootMargin:'0px'});
  function notify(text,bad=false){
    const el=document.createElement('div');el.className='toast'+(bad?' bad':'');el.textContent=text;$('toasts').appendChild(el);setTimeout(()=>el.remove(),3500);
  }
  function setText(el,text){if(el&&el.textContent!==text)el.textContent=text;}
  function syncConfig(state){
    const colors=state.styles.reduce((n,s)=>n+s.colors.size,0),count=colors*state.files.length;
    setText($('styleCount'),`${state.styles.length} 款 / ${colors} 色`);
    setText($('mobileStyleCount'),`${state.styles.length} 款`);
    setText($('settingsSummary'),`${$('sizeTier').value} · ${{high:'高质量',medium:'标准',low:'快速',auto:'自动'}[$('quality').value]||'高质量'}`);
    const active=state.job&&!terminal.includes(state.job.status);
    const button=$('startBtn');
    button.disabled=!!state.busy||active||state.files.some(entry=>entry.loading)||!state.files.length||!colors||!$('provider').value||!$('model').value;
    if(!state.busy){
      const label=active?`${state.job.stats.done} / ${state.job.stats.total} · 生成中`:`生成 ${count} 张`;
      if(button.dataset.flowLabel!==label||button.textContent.trim()!==label){button.innerHTML='';if(!active){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#i-spark');svg.appendChild(use);button.appendChild(svg);}const text=document.createElement('span');text.textContent=label;button.appendChild(text);button.dataset.flowLabel=label;}
    }
    $('directStartBtn').disabled=button.disabled;
    $('demoScenario').disabled=active||state.busy;
    $('demoReset').disabled=active||state.busy;
    button.title=active?'本轮正在生成，可查看成品、切换筛选或取消':`按当前配置生成 ${count} 张图片`;
  }
  function observeCard(card,task){
    cardObserver.observe(card);
    const shot=card.querySelector('.shot');shot.tabIndex=0;shot.setAttribute('role','button');shot.setAttribute('aria-label',`${task.garment_name} ${task.color_name} ${task.item}，查看大图`);
    shot.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();shot.click();}});
    card.title=`${task.garment_name} · ${task.color_name} · ${task.item}\n演示样图；文件名可在导出清单中查看。`;
    shot.dataset.item=task.item.replace('item-','素材 ');
  }
  function matches(task){return filter==='all'||(filter==='done'?['done','skipped'].includes(task.status):filter==='queued'?['pending','queued','canceled'].includes(task.status):task.status===filter);}
  function applyFilter(state){
    const counts={all:state.job?.tasks.length||0,done:0,running:0,queued:0,failed:0};
    let shown=0;
    for(const task of state.job?.tasks||[]){
      const group=['done','skipped'].includes(task.status)?'done':['queued','pending','canceled'].includes(task.status)?'queued':task.status;
      if(group in counts)counts[group]++;
      const entry=state.cards.get(task.id),card=entry?.el;
      if(card){
        const visible=matches(task),disabled=!task.out,unavailable=task.thumb?'false':'true';
        if(card.hidden===visible)card.hidden=!visible;
        if(visible)shown++;
        if(entry.input.disabled!==disabled)entry.input.disabled=disabled;
        if(entry.shot.getAttribute('aria-disabled')!==unavailable)entry.shot.setAttribute('aria-disabled',unavailable);
      }
    }
    document.querySelectorAll('[data-filter]').forEach(btn=>{const on=btn.dataset.filter===filter;btn.classList.toggle('active',on);btn.setAttribute('aria-pressed',String(on));setText(btn.querySelector('span'),String(counts[btn.dataset.filter]));});
    $('filterEmpty').hidden=shown>0||counts.all===0;
  }
  function chooseFilter(value){filter=value;if(api)applyFilter(api.state);$('galleryScroll').scrollTop=0;}
  function updateWaitFeedback(){
    if(!api?.state.job||terminal.includes(api.state.job.status)){clearTimeout(waitTimer);waitTimer=null;return;}
    if(!document.hidden){
      const stats=api.state.job.stats;
      const elapsed=Math.max(1,Math.floor((performance.now()-runStarted)/1000));
      const silent=Math.floor((performance.now()-lastArrival)/1000);
      const parts=[];if(stats.running)parts.push(`${stats.running} 张生成中`);if(stats.queued)parts.push(`${stats.queued} 张排队`);if(stats.failed)parts.push(`${stats.failed} 张失败可重试`);parts.push(`已等待 ${elapsed}s`);
      if(silent>=7)parts.push('仍在处理中，可先查看已完成图片');
      setText($('progressStats'),parts.join(' · '));
    }
    waitTimer=setTimeout(updateWaitFeedback,1000);
  }
  function syncJob(state){
    const job=state.job;if(!job)return;
    if(observedJob!==job.id){
      observedJob=job.id;cardObserver.disconnect();state.cards.forEach(entry=>cardObserver.observe(entry.el));
      runStarted=performance.now();lastArrival=runStarted;lastFinished=0;
    }
    if(job.stats.done!==lastFinished){lastArrival=performance.now();lastFinished=job.stats.done;}
    $('progressBar').style.setProperty('--progress',String(Math.max(0,Math.min(1,job.progress/100))));
    setText($('mobileDoneCount'),`${job.stats.done} / ${job.stats.total}`);
    applyFilter(state);
    clearTimeout(waitTimer);waitTimer=null;
    if(!terminal.includes(job.status))updateWaitFeedback();
    else {
      setText($('progressStats'),job.status==='canceled'?`已取消 · 保留 ${job.stats.done} 张成品`:job.stats.failed?`${job.stats.done} 张完成 · ${job.stats.failed} 张可重试`:`${job.stats.done} 张已完成 · 可查看或导出`);
    }
    // The current model, image count and button feedback remain visible during generation.
    syncConfig(state);
    window.PrismUI?.syncJob(state);
  }
  function mobilePane(name){
    document.body.dataset.mobilePane=name;
    document.querySelectorAll('button[data-mobile-pane]').forEach(btn=>{const on=btn.dataset.mobilePane===name;btn.classList.toggle('active',on);btn.setAttribute('aria-pressed',String(on));});
  }
  function onSubmit(){
    filter='all';$('galleryScroll').scrollTop=0;mobilePane('results');
    cardObserver.disconnect();observedJob='';runStarted=performance.now();lastArrival=runStarted;
    document.querySelectorAll('.more-actions[open]').forEach(el=>el.open=false);
  }
  function modalTasks(){return (api?.state.job?.tasks||[]).filter(t=>t.thumb);}
  function showModalTask(index){
    const tasks=modalTasks();if(!tasks.length)return;
    modalIndex=(index+tasks.length)%tasks.length;const task=tasks[modalIndex];
    $('lightboxStage').classList.remove('ready');
    $('lightboxStage').setAttribute('aria-busy','true');
    setText($('lightboxStage').querySelector('.image-loading'),'正在读取原图…');
    $('largeImage').src=task.full||task.thumb;
    $('largeImage').alt=`${task.garment_name} · ${task.color_name} · ${task.item}，演示样图`;
    setText($('lightboxTitle'),`${task.garment_name} · ${task.color_name}`);
    setText($('lightboxSub'),`${modalIndex+1} / ${tasks.length} · ${task.item} · 演示样图`);
    $('sampleDownload').href=task.full||task.thumb;$('sampleDownload').download=task.out||'demo.jpg';
  }
  function ready(bridge){
    api=bridge;syncConfig(api.state);syncJob(api.state);
    mobilePane('results');
    $('demoScenario').addEventListener('change',event=>{chooseFilter('all');window.FlowDemo.setScenario(event.target.value);notify(event.target.value==='stress'?'已准备 8 款、5 张素材，共 120 张任务':'已恢复 6 款、2 张素材，共 36 张任务');});
    $('demoReset').addEventListener('click',()=>{chooseFilter('all');window.FlowDemo.reset();notify('演示配置已恢复');});
    document.querySelectorAll('[data-filter]').forEach(btn=>btn.addEventListener('click',()=>chooseFilter(btn.dataset.filter)));
    $('clearFilter').addEventListener('click',()=>chooseFilter('all'));
    $('toggleDensity').addEventListener('click',()=>{const compact=$('gridImgs').classList.toggle('compact');$('toggleDensity').setAttribute('aria-pressed',String(compact));setText($('toggleDensity').querySelector('span'),compact?'舒展':'紧凑');});
    document.querySelectorAll('button[data-mobile-pane]').forEach(btn=>btn.addEventListener('click',()=>mobilePane(btn.dataset.mobilePane)));
    $('largeImage').addEventListener('load',async()=>{const image=$('largeImage'),expected=image.src;try{await image.decode();}catch(_){}if(image.src===expected){$('lightboxStage').classList.add('ready');$('lightboxStage').setAttribute('aria-busy','false');}});
    $('largeImage').addEventListener('error',()=>{setText($('lightboxStage').querySelector('.image-loading'),'原图读取失败，可关闭后重试');});
    $('lightbox').addEventListener('close',()=>{$('lightboxStage').classList.remove('ready');});
    // The existing click handler opens the dialog; this handler fills the richer context.
    $('gridImgs').addEventListener('click',event=>{const shot=event.target.closest('.shot');const card=shot?.closest('.cardimg');if(!card?.dataset.full)return;const tasks=modalTasks();const index=tasks.findIndex(t=>t.id===card.dataset.task);if(index>=0)showModalTask(index);});
    $('lightboxPrev').addEventListener('click',()=>showModalTask(modalIndex-1));
    $('lightboxNext').addEventListener('click',()=>showModalTask(modalIndex+1));
    document.addEventListener('keydown',event=>{
      if($('lightbox').open&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();showModalTask(modalIndex+(event.key==='ArrowLeft'?-1:1));}
      if(event.key==='Escape'&&$('adminPreview').open)$('adminPreview').close();
      if(event.key==='Tab'&&openPanel&&!openPanel.hidden){const list=[...openPanel.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),textarea,select:not(:disabled),[tabindex="0"]')].filter(el=>el.getClientRects().length);if(!list.length)return;const first=list[0],last=list.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
    });
    ['harness','prompt','feishu','accounts'].forEach(name=>{
      const panel=$(name+'Drawer');
      new MutationObserver(()=>{
        if(!panel.hidden&&panel.classList.contains('on')){
          if(openPanel===panel)return;drawerInvoker=document.activeElement;openPanel=panel;document.querySelector('.app').inert=true;
          requestAnimationFrame(()=>{const target=panel.querySelector('textarea:not([hidden])')||panel.querySelector('button');target?.focus({preventScroll:true});});
        }else if(openPanel===panel){document.querySelector('.app').inert=false;openPanel=null;drawerInvoker?.focus?.({preventScroll:true});}
      }).observe(panel,{attributes:true,attributeFilter:['hidden','class']});
    });
    document.addEventListener('pointerdown',event=>{if(!$('settingsDetails').contains(event.target))$('settingsDetails').open=false;});
    document.addEventListener('keydown',event=>{if(event.key==='Escape')$('settingsDetails').open=false;});
    ['adminLink','railAdmin'].forEach(id=>$(id).addEventListener('click',event=>{event.preventDefault();$('adminPreview').showModal();}));
    $('adminPreviewClose').addEventListener('click',()=>$('adminPreview').close());
    $('adminPreviewAccounts').addEventListener('click',()=>{$('adminPreview').close();$('accountsBtn').click();});
    $('railUpload').addEventListener('click',()=>mobilePane('config'));
    document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(waitTimer);waitTimer=null;}else if(api.state.job&&!terminal.includes(api.state.job.status)){clearTimeout(waitTimer);updateWaitFeedback();}});
    $('briefInput').addEventListener('input',()=>{document.querySelectorAll('[data-brief]').forEach(btn=>btn.classList.remove('chosen'));});
    $('briefChips').addEventListener('click',event=>{const chip=event.target.closest('[data-brief]');if(chip){document.querySelectorAll('[data-brief]').forEach(btn=>btn.classList.toggle('chosen',btn===chip));if(!reduced())chip.animate([{transform:'scale(.96)'},{transform:'scale(1)'}],{duration:140});}});
    window.PrismUI?.ready(bridge);
    document.body.classList.remove('booting');
  }
  window.FlowUI={notify,syncConfig,syncJob,observeCard,ready,onSubmit,mobilePane};
})();
