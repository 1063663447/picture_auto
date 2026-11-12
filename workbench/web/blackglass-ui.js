/* Blackglass production view. Real data, pricing, downloads and actions stay in app.js. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const terminal=['done','partial','failed','canceled'];
  const reduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
  const text=(el,value)=>{if(el&&el.textContent!==String(value))el.textContent=String(value);};
  let bridge,filter='all',observedJob='',pending=false,submission=null,waitTimer=0;
  let profileAnchor,profileTimer=0,modalIndex=0,drawerInvoker,openPanel,usageLoader;
  let lastFrameAt=0,lastFinished=0,runStarted=0,lastArrival=0,flipCount=0;
  const observer=typeof IntersectionObserver==='function'?new IntersectionObserver(rows=>{
    rows.forEach(row=>row.target.classList.toggle('in-viewport',row.isIntersecting));window.PrismFX?.invalidate();
  },{root:$('galleryScroll'),rootMargin:'0px'}):null;
  const state=()=>bridge?.state;
  const safeMoney=(value,currency)=>`${currency==='CNY'?'¥':currency==='USD'?'$':currency+' '}${Number(value||0).toFixed(3)}`;
  function imageURL(task,kind){
    const version=task.version||Math.round((task.finished_at||0)*1000);
    const source=kind==='list'?(task.thumb_small||task.thumb):(task.preview||task.thumb);
    if(!source)return '';
    return source.includes('v=')?source:`${source}${source.includes('?')?'&':'?'}v=${version||0}`;
  }
  function syncConfig(){
    const s=state();if(!s)return;
    const colors=s.styles.reduce((n,item)=>n+item.colors.size,0),count=s.files.length*colors;
    text($('styleCount'),`${s.styles.length} 款 / ${colors} 色`);text($('mobileStyleCount'),`${s.styles.length} 款`);
    text($('settingsSummary'),`${$('sizeTier').value} · ${{high:'高质量',medium:'标准',low:'快速',auto:'自动'}[$('quality').value]||'自动'}`);
    text($('workspaceSummary'),`${s.files.length} 张原图 · ${s.styles.length} 款 · ${count} 张预计成品`);
    const phase=s.job?bridge.jobPhase(s.job):null,active=phase&&!phase.terminal,button=$('startBtn');
    const model=$('model').value==='__manual__'?$('modelManual').value.trim():$('model').value;
    const blocked=s.busy||s.harnessBusy||s.harnessOpening||s.jobLoading||active||!s.skill||!s.files.length||!colors||s.files.some(f=>f.loading)||!$('provider').value||!model||$('model').disabled;
    if(button.disabled!==!!blocked)button.disabled=!!blocked;
    if(!s.busy){
      const label=s.harnessBusy?'AI 整理需求中…':s.jobLoading?'正在切换任务…':s.harnessOpening?'准备 AI 创作…':active?`${s.job.stats.done+s.job.stats.skipped} / ${s.job.stats.total} · ${phase.packaging?'整理打包…':'生成中'}`:`生成 ${count} 张`;
      if(button.dataset.label!==label||button.textContent.trim()!==label){button.replaceChildren();const span=document.createElement('span');span.textContent=label;button.appendChild(span);button.dataset.label=label;}
    }
    $('directStartBtn').disabled=!!blocked;
    button.title=active?(phase.packaging?'图片已处理完成，正在整理打包；任务确认完成后可开始下一轮':'本轮仍在运行，可提前查看成品或取消'):'真实接口调用会计费，请核对款色与模型后生成';
    text($('profileSession'),s.session?.authenticated?'已登录':'本地访问');
  }
  function observeCard(card,task){
    observer?.observe(card);if(!observer)card.classList.add('in-viewport');
    const shot=card.querySelector('.shot');shot.tabIndex=0;shot.setAttribute('role','button');
    shot.setAttribute('aria-label',`${task.garment_name||''} ${task.color_name} ${task.item}，查看大图`);
    shot.dataset.item=task.view?`${task.item} / ${task.view}`:task.item;
    shot.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();shot.click();}});
  }
  function resetCard(entry){entry.epoch=(entry.epoch||0)+1;entry.flipAnimation?.cancel();entry.flipAnimation=null;entry.el.classList.remove('image-ready','flash-revealed','flash-flipping');}
  async function revealCard(entry,task){
    const epoch=(entry.epoch||0)+1;entry.epoch=epoch;entry.el.classList.add('image-ready');
    const turner=entry.shot.querySelector('.card-turner');
    if(reduced()||!entry.el.classList.contains('in-viewport')||!turner?.animate||document.hidden){entry.el.classList.add('flash-revealed');return;}
    entry.el.classList.add('flash-flipping');flipCount++;
    const fold=turner.animate([{transform:'scaleX(1)'},{transform:'scaleX(.025)'}],{duration:190,easing:'ease-in',fill:'forwards'});entry.flipAnimation=fold;
    try{await fold.finished;}catch(_){return;}
    if(!entry.el.isConnected||entry.epoch!==epoch)return;
    entry.el.classList.add('flash-revealed');
    const unfold=turner.animate([{transform:'scaleX(.025)'},{transform:'scaleX(1)'}],{duration:320,easing:'cubic-bezier(.12,.65,.18,1)'});entry.flipAnimation=unfold;fold.cancel();window.PrismFX?.reveal(entry.shot);
    try{await unfold.finished;}catch(_){return;}
    if(entry.el.isConnected&&entry.epoch===epoch){entry.el.classList.remove('flash-flipping');entry.flipAnimation=null;}
  }
  function updateCard(entry,task){
    const s=state(),tasks=s?.job?.tasks||[];
    const master=task.master_task&&tasks.find(t=>t.id===task.master_task);
    const dependency=task.status==='pending'&&task.master_task;
    const labels={pending:dependency?'等待基准批次':'等待调度',queued:'排队中',running:'正在生成',failed:'这张暂未完成',canceled:'已取消',done:entry.el.classList.contains('image-ready')?'显影完成':'正在读取成品图',skipped:'成品已就绪'};
    const ahead=task.queue_ahead==null?null:Number(task.queue_ahead);
    const messages={pending:dependency?(master&&['done','skipped'].includes(master.status)?'本色基准已完成，等待其余基准任务':'基准版型完成后自动派生本色'):'等待服务端调度',queued:ahead!=null&&ahead>0?`前方还有 ${ahead} 张，等待调用`:'等待接口并发位或调度',running:task.master?'正在建立本款基准版型':'版型与色彩正在逐步显影',failed:'可单独重试，其他成品保留',canceled:'已完成图片仍可查看',done:'成品已返回，解码后自动翻面',skipped:'已有成品，可直接查看'};
    text(entry.backText,labels[task.status]||task.status);text(entry.backMessage,messages[task.status]||'');
    const tag=entry.el.querySelector('.sync-tag');if(tag){tag.hidden=!task.master&&!task.master_task;text(tag,task.master?'基准版型':'跟随基准');}
    if(task.person_alert){
      let alert=entry.el.querySelector('.person-warning');
      if(!alert){alert=document.createElement('span');alert.className='person-warning';alert.textContent='主体需复查';alert.title='纯衣服成品疑似出现人物，请查看原图或改图返工';entry.el.appendChild(alert);}
    }else entry.el.querySelector('.person-warning')?.remove();
    entry.el.title=`${task.garment_name||''} · ${task.color_name} · ${task.item}${task.view?' / '+task.view:''}\n${task.out||''}${task.person_alert?'\n警告：主体需复查':''}`;
  }
  function matches(task){return filter==='all'||(filter==='done'?['done','skipped'].includes(task.status):filter==='queued'?['pending','queued','canceled'].includes(task.status):task.status===filter);}
  function applyFilter(){
    const s=state();if(!s||pending)return;
    const counts={all:s.job?.tasks.length||0,done:0,running:0,queued:0,failed:0};let shown=0;
    for(const task of s.job?.tasks||[]){
      const group=['done','skipped'].includes(task.status)?'done':['pending','queued','canceled'].includes(task.status)?'queued':task.status;if(group in counts)counts[group]++;
      const entry=s.cards.get(task.id);if(!entry)continue;const visible=matches(task),disabled=!task.out,unavailable=task.thumb?'false':'true';
      if(entry.el.hidden===visible)entry.el.hidden=!visible;if(visible)shown++;
      if(entry.input.disabled!==disabled)entry.input.disabled=disabled;
      if(entry.shot.getAttribute('aria-disabled')!==unavailable)entry.shot.setAttribute('aria-disabled',unavailable);
    }
    document.querySelectorAll('[data-filter]').forEach(button=>{const on=button.dataset.filter===filter;button.classList.toggle('active',on);button.setAttribute('aria-pressed',String(on));text(button.querySelector('span'),counts[button.dataset.filter]);});
    $('filterEmpty').hidden=shown>0||counts.all===0;
  }
  function selectFilter(value){filter=value;applyFilter();$('galleryScroll').scrollTop=0;}
  function waitFeedback(){
    clearTimeout(waitTimer);const s=state();if(!s?.job||s.jobLoading||terminal.includes(s.job.status)||pending)return;
    if(!document.hidden){
      const job=s.job,stats=job.stats,phase=bridge.jobPhase(job),parts=[];
      // 9.6.24 基准图审核期其实什么都没在跑：以前这里照样打印「N 张待处理 · 仍在处理中」，
      // 一挂就是十几分钟，看着像卡死。现在老实说「等你确认」。
      const review=job.review&&job.review.enabled&&job.review.awaiting?job.review:null;
      if(review){
        const pendingCount=(review.pending_ids||[]).length;
        parts.push(pendingCount?`等你点「确定」确认基准图（${pendingCount} 张待确认）`:'等你确认基准图');
        if(review.derived_total)parts.push(`其余 ${review.derived_total} 个颜色确认后才开始`);
      }else if(phase.packaging)parts.push(job.params?.packaging_progress?`生成完成，校验打包 ${job.params.packaging_progress}`:/打包|校验|packag/i.test(job.stage||'')?job.stage:'生成完成，正在打包');
      else{
        if(stats.running)parts.push(`${stats.running} 张生成中`);
        const waiting=(stats.pending||0)+(stats.queued||0);if(waiting)parts.push(`${waiting} 张待处理`);
        if(job.params?.queue?.gate_ahead>0)parts.push(`接口队列前方 ${job.params.queue.gate_ahead}`);
      }
      if(stats.failed)parts.push(`${stats.failed} 张失败`);
      parts.push(`已等待 ${Math.max(1,Math.floor((Date.now()-job.created_at*1000)/1000))}s`);
      if(!review&&Date.now()-lastArrival>20000)parts.push(phase.packaging?'可先查看已完成图片，打包结束后提供整包下载':'仍在处理中，可先查看已完成图片');
      text($('progressStats'),parts.join(' · '));
    }
    waitTimer=setTimeout(waitFeedback,1000);
  }
  function syncJob(){
    const s=state(),job=s?.job;if(!job||pending)return;
    if(observedJob!==job.id){observedJob=job.id;filter='all';observer?.disconnect();s.cards.forEach(entry=>observer?.observe(entry.el));runStarted=Date.now();lastArrival=runStarted;lastFinished=-1;}
    if(job.stats.done!==lastFinished){lastFinished=job.stats.done;lastArrival=Date.now();}
    text($('mobileDoneCount'),`${job.stats.done+job.stats.skipped} / ${job.stats.total}`);
    applyFilter();waitFeedback();syncConfig();window.PrismFX?.invalidate();
    if(terminal.includes(job.status))text($('progressStats'),job.status==='canceled'?`已取消 · 保留 ${job.stats.done+job.stats.skipped} 张成品`:`${job.stats.done+job.stats.skipped} 张完成${job.stats.failed?` · ${job.stats.failed} 张可重试`:''}${job.error&&!bridge.jobPhase(job).successful?' · '+job.error:''}`);
  }
  function mobilePane(name){document.body.dataset.mobilePane=name;document.querySelectorAll('button[data-mobile-pane]').forEach(button=>{const on=button.dataset.mobilePane===name;button.classList.toggle('active',on);button.setAttribute('aria-pressed',String(on));});window.PrismFX?.invalidate();}
  function beginSubmission(picks){
    if(pending)return;pending=true;clearTimeout(waitTimer);
    const s=state(),total=s.files.length*picks.reduce((n,p)=>n+p.colors.length,0);
    submission={title:$('jobTitle').textContent,stage:$('jobStage').textContent,emptyHidden:$('emptyState').hidden,filterHidden:$('filterEmpty').hidden,durationHidden:$('jobDuration').hidden};$('jobDuration').hidden=true;
    bridge.switchView('gallery');mobilePane('results');$('gridImgs').hidden=true;$('emptyState').hidden=true;$('filterEmpty').hidden=true;
    text($('jobTitle'),'正在创建任务');text($('jobStage'),'上传与规划中，等待服务器确认');
    const surface=document.createElement('div');surface.className='submission-surface';surface.id='submissionSurface';surface.setAttribute('role','status');surface.setAttribute('aria-busy','true');
    const label=document.createElement('p');label.className='submission-label';label.textContent=`预计 ${total} 张 · 正在上传与规划，尚未获得任务 ID`;surface.appendChild(label);
    const grid=document.createElement('div');grid.className='gallery submission-grid';
    for(let i=0;i<Math.min(total,24);i++){const card=document.createElement('div');card.className='submission-card';card.innerHTML='<span aria-hidden="true">✧</span><b>等待服务器确认</b><small>真实任务返回后逐张回填</small>';grid.appendChild(card);}
    surface.appendChild(grid);$('galleryScroll').prepend(surface);$('galleryScroll').scrollTop=0;
    document.querySelectorAll('.more-actions[open]').forEach(el=>el.open=false);
  }
  function endSubmission(success){
    if(!pending)return;pending=false;$('submissionSurface')?.remove();$('gridImgs').hidden=false;
    if(!success&&submission){text($('jobTitle'),submission.title);text($('jobStage'),submission.stage);$('emptyState').hidden=submission.emptyHidden;$('filterEmpty').hidden=submission.filterHidden;$('jobDuration').hidden=submission.durationHidden;}
    submission=null;syncConfig();if(!success&&state()?.job)syncJob();
  }
  const modalTasks=()=>state()?.job?.tasks.filter(task=>task.thumb)||[];
  function openLightbox(url,title){
    const tasks=modalTasks();modalIndex=tasks.findIndex(task=>imageURL(task,'preview')===url||task.thumb===url);
    $('lightboxStage').classList.remove('ready');$('lightboxStage').setAttribute('aria-busy','true');text($('lightboxStage').querySelector('.image-loading'),'正在读取原图…');
    $('largeImage').src=url;text($('lightboxTitle'),title||'成品大图');
    const task=tasks[modalIndex];$('sampleDownload').href=task?bridge.downloadUrl(state().job.id,task.id):url;
    if(task){$('sampleDownload').download=task.out?.split('/').pop()||'';text($('lightboxSub'),`${modalIndex+1} / ${tasks.length} · ${task.item}${task.view?' / '+task.view:''} · 原图下载`);}
    $('lightboxPrev').disabled=tasks.length<2;$('lightboxNext').disabled=tasks.length<2;
  }
  function stepImage(direction){const tasks=modalTasks();if(!tasks.length)return;modalIndex=(Math.max(0,modalIndex)+direction+tasks.length)%tasks.length;const task=tasks[modalIndex];openLightbox(imageURL(task,'preview'),`${task.garment_name||''} · ${task.color_name}`);}
  function visibleAvatar(){return innerWidth>760?$('railAvatar'):$('mobileProfile');}
  function closeProfile(restore=true){clearTimeout(profileTimer);$('profileMenu').classList.remove('on');document.querySelectorAll('.profile-trigger').forEach(el=>el.setAttribute('aria-expanded','false'));profileTimer=setTimeout(()=>$('profileMenu').hidden=true,reduced()?0:180);if(restore)visibleAvatar()?.focus({preventScroll:true});}
  function toggleProfile(anchor){
    const menu=$('profileMenu');clearTimeout(profileTimer);if(!menu.hidden&&menu.classList.contains('on')){closeProfile();return;}
    profileAnchor=anchor||visibleAvatar();menu.hidden=false;
    const r=profileAnchor.getBoundingClientRect(),width=menu.offsetWidth,height=menu.offsetHeight;
    let x=innerWidth>760?r.right+15:r.right-width,y=innerWidth>760?r.bottom-height:r.bottom+10;
    if(y+height>innerHeight-12)y=r.top-height-10;
    menu.style.left=Math.max(12,Math.min(x,innerWidth-width-12))+'px';menu.style.top=Math.max(12,Math.min(y,innerHeight-height-12))+'px';
    menu.classList.add('on');document.querySelectorAll('.profile-trigger').forEach(el=>el.setAttribute('aria-expanded','true'));menu.querySelector('button.profile-option:not([hidden])')?.focus({preventScroll:true});
  }
  function closeUsage(){const drawer=$('usageDrawer'),scrim=$('usageScrim');drawer.classList.remove('on');scrim.classList.remove('on');setTimeout(()=>{drawer.hidden=true;scrim.hidden=true;},230);}
  async function refreshUsage(){
    const body=$('usageBody');body.replaceChildren();for(let i=0;i<3;i++){const row=document.createElement('div');row.className='sk';row.style.height='70px';body.appendChild(row);}
    try{
      const payload=await usageLoader(),currency=payload.currency||state().pricing.currency,usage=payload.usage||payload;
      body.replaceChildren();
      for(const [title,row] of [['今日用量',usage.today||{}],['累计用量',usage.total||{}]]){
        const card=document.createElement('article');card.className='usage-card';const h=document.createElement('h3');h.textContent=title;const p=document.createElement('p');p.textContent=`${row.images||0} 张 · ${safeMoney(row.cost,currency)}`;card.append(h,p);body.appendChild(card);
      }
      const note=document.createElement('p');note.className='hint';note.textContent=`${usage.jobs||0} 个任务 · 当前账号的后台用量记录${usage.unpriced_images?' · 部分图片尚未定价':''}`;body.appendChild(note);
    }catch(error){const note=document.createElement('p');note.className='usage-error';note.textContent=`读取失败：${error.message}`;body.replaceChildren(note);}
  }
  function showUsage(loader){usageLoader=loader;$('usageDrawer').hidden=false;$('usageScrim').hidden=false;requestAnimationFrame(()=>{$('usageDrawer').classList.add('on');$('usageScrim').classList.add('on');});refreshUsage();}
  function ready(api){
    bridge=api;mobilePane('config');syncConfig();
    window.PrismFX?.configure({palette:['#3c3549','#c9c4d5','#8a7e9f'],stars:false,particles:true,rain:false,intensity:70,paused:false});
    let ambiencePaused=false;try{ambiencePaused=localStorage.getItem('recolor.blackglass.paused')==='true';}catch(_){}
    function setAmbience(paused){ambiencePaused=paused;document.documentElement.dataset.atmospherePaused=String(paused);window.PrismFX?.configure({stars:false,particles:true,rain:false,intensity:70,paused});$('ambienceToggle').setAttribute('aria-pressed',String(paused));text($('ambienceToggle'),paused?'✧ 微光已暂停':'✧ 微光开启');}
    setAmbience(ambiencePaused);$('ambienceToggle').addEventListener('click',()=>{setAmbience(!ambiencePaused);try{localStorage.setItem('recolor.blackglass.paused',String(ambiencePaused));}catch(_){}});
    document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>selectFilter(button.dataset.filter)));$('clearFilter').addEventListener('click',()=>selectFilter('all'));
    $('toggleDensity').addEventListener('click',()=>{const compact=$('gridImgs').classList.toggle('compact');$('toggleDensity').setAttribute('aria-pressed',String(compact));text($('toggleDensity').querySelector('span'),compact?'舒展':'紧凑');window.PrismFX?.invalidate();});
    document.querySelectorAll('button[data-mobile-pane]').forEach(button=>button.addEventListener('click',()=>mobilePane(button.dataset.mobilePane)));
    $('mobileProfile').addEventListener('click',()=>toggleProfile($('mobileProfile')));$('profileClose').addEventListener('click',()=>closeProfile());
    $('profileMenu').addEventListener('click',event=>{if(event.target.closest('.profile-option,#logoutBtn'))closeProfile(false);},true);
    document.addEventListener('pointerdown',event=>{if(!$('profileMenu').hidden&&!$('profileMenu').contains(event.target)&&!event.target.closest('.profile-trigger'))closeProfile(false);if(!$('settingsDetails').contains(event.target))$('settingsDetails').open=false;});
    $('settingsDetails').addEventListener('toggle',()=>{const open=$('settingsDetails').open;$('settingsDetails').querySelector('summary').setAttribute('aria-label',`${open?'收起':'展开'}生成设定`);$('settingsDetails').querySelector('.settings-cta').firstChild.textContent=open?'收起参数 ':'调整参数 ';window.PrismFX?.invalidate();});
    $('largeImage').addEventListener('load',async()=>{const image=$('largeImage'),expected=image.src;try{await image.decode();}catch(_){}if(image.src===expected){$('lightboxStage').classList.add('ready');$('lightboxStage').setAttribute('aria-busy','false');}});
    $('largeImage').addEventListener('error',()=>{text($('lightboxStage').querySelector('.image-loading'),'图片读取失败，可关闭后重试');$('lightboxStage').setAttribute('aria-busy','false');});
    $('lightbox').addEventListener('close',()=>$('lightboxStage').classList.remove('ready'));$('lightboxPrev').addEventListener('click',()=>stepImage(-1));$('lightboxNext').addEventListener('click',()=>stepImage(1));
    $('usageClose').addEventListener('click',closeUsage);$('usageScrim').addEventListener('click',closeUsage);$('usageReload').addEventListener('click',refreshUsage);
    for(const name of ['harness','prompt','feishu','accounts','usage']){
      const panel=$(name+'Drawer');new MutationObserver(()=>{
        if(!panel.hidden&&panel.classList.contains('on')){if(openPanel===panel)return;drawerInvoker=document.activeElement;openPanel=panel;document.querySelector('.app').inert=true;const target=panel.querySelector('textarea:not([hidden])')||panel.querySelector('button');target?.focus({preventScroll:true});}
        else if(openPanel===panel){
          document.querySelector('.app').inert=false;openPanel=null;
          if(name==='harness')window.AIWorkflow?.refresh();
          const usable=el=>el&&!el.disabled&&!panel.contains(el)&&el.getClientRects().length&&el.matches('button,a,input,textarea,select,[tabindex]');
          const target=usable(drawerInvoker)?drawerInvoker:usable($('aiResumeBtn'))?$('aiResumeBtn'):$('briefInput');
          target?.focus?.({preventScroll:true});
        }
      }).observe(panel,{attributes:true,attributeFilter:['hidden','class']});
    }
    document.addEventListener('keydown',event=>{
      if(event.key==='Escape'){if(!$('profileMenu').hidden)closeProfile();$('settingsDetails').open=false;if(!$('usageDrawer').hidden)closeUsage();}
      if($('lightbox').open&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();stepImage(event.key==='ArrowLeft'?-1:1);}
      if(event.key==='Tab'&&openPanel&&!openPanel.hidden){const items=[...openPanel.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),textarea,select:not(:disabled),[tabindex="0"]')].filter(el=>el.getClientRects().length);if(!items.length)return;const first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
    });
    document.addEventListener('visibilitychange',()=>{clearTimeout(waitTimer);if(!document.hidden)waitFeedback();});
    window.addEventListener('pagehide',()=>{clearTimeout(waitTimer);observer?.disconnect();state()?.files.forEach(f=>{if(f.url)URL.revokeObjectURL(f.url);});});
  }
  window.WorkbenchUI={ready,syncConfig,syncJob,observeCard,resetCard,revealCard,updateCard,beginSubmission,endSubmission,openLightbox,toggleProfile,showUsage,mobilePane,imageURL,
    snapshot:()=>({pending,filter,flipCount,jobId:state()?.jobId,files:state()?.files.length,styles:state()?.styles.length})};
})();
