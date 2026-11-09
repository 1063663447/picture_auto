/* Run in a logged-in workbench browser with runBlackglassRegression().
   Uses current production HTML/JS inside a disposable iframe. Every iframe API
   request is intercepted; no POST reaches a provider, Feishu or the server.
   Serve this file on loopback with CORS, evaluate it, then invoke the function. */
window.runBlackglassRegression = async function () {
  'use strict';
  const results=[],errors=[],checks=(label,passed,detail)=>results.push({label,passed:!!passed,...(detail?{detail}: {})});
  const origin=location.origin,read=async path=>{const r=await fetch(origin+path,{cache:'no-store'});if(!r.ok)throw Error(path+': '+r.status);return r;};
  const [html,app,ui,skill,providers,pricing,session,usage,workflow]=await Promise.all([
    read('/').then(r=>r.text()),read('/app.js').then(r=>r.text()),read('/blackglass-ui.js').then(r=>r.text()),
    read('/api/skill').then(r=>r.json()),read('/api/providers').then(r=>r.json()),read('/api/pricing').then(r=>r.json()),
    read('/api/session').then(r=>r.json()),read('/api/usage').then(r=>r.json()),read('/ai-workflow.js').then(r=>r.text())]);
  if(!html.includes('id="gridImgs"'))throw Error('Log in to the workbench before running this test.');
  const frame=document.createElement('iframe');frame.id='blackglassRegressionFrame';
  frame.style.cssText='position:fixed;left:-1700px;top:0;width:1440px;height:900px;border:0;';
  document.body.appendChild(frame);
  frame.srcdoc=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace('<head>',`<head><base href="${origin}/">`);
  await new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  const w=frame.contentWindow,d=w.document,$=id=>d.getElementById(id);
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms)),flush=()=>sleep(30);
  const response=(data,status=200)=>new w.Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
  const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
  const calls=[];let handler=null;
  const defaults=path=>{
    if(path==='/api/skill')return skill;if(path==='/api/providers')return providers;if(path==='/api/pricing')return pricing;
    if(path==='/api/session')return session;if(path==='/api/usage')return usage;
    if(path==='/api/health')return {ok:true,types:Object.keys(skill.types).length,providers_ui:providers.providers.length};
    if(path==='/api/jobs')return {ok:true,jobs:[]};
    if(path==='/api/gate')return {ok:true,enabled:false};
    if(path==='/api/models')return {ok:true,models:[{id:'bg-default-model',image:true}],current:'bg-default-model'};
    if(path.startsWith('/api/cache'))return {ok:true,files:0,bytes:0};
    return {ok:true};
  };
  w.fetch=async (input,options={})=>{
    const url=new URL(String(input),origin),method=options.method||'GET';
    const call={path:url.pathname,url,method,options};calls.push(call);
    if(handler){const value=handler(call);if(value!==undefined)return await value;}
    if(method!=='GET')throw Error('Live write blocked by regression runner: '+method+' '+url.pathname);
    return response(defaults(url.pathname));
  };
  w.addEventListener('error',e=>errors.push(e.message));
  w.addEventListener('unhandledrejection',e=>{errors.push(String(e.reason?.message||e.reason));e.preventDefault();});
  const exportNames='state,watchJob,renderJob,loadModels,loadReworkModels,updateSummary,sendHarnessMessage,createJob,submitRework,harnessDefaultAction,harnessApplyAction,openHarness,cancelJob,retryJob,retrySingle,exportSelection,closeHarness,handleFrame,loadSession,beginGeneration,priceOf,formatJobDuration,openDrawer,harnessContextKey';
  const replaced=app.replace(/^  boot\(\)\.catch.*$/m,`  window.__bgTest={${exportNames}};window.__bgBoot=boot();`);
  if(replaced===app)throw Error('Startup anchor changed; update the runner rather than executing unguarded code.');
  try {
    new w.Function(ui)();new w.Function(workflow)();new w.Function(replaced)();await w.__bgBoot;await flush();
    const t=w.__bgTest,s=t.state;
    checks('Production code boots without runtime errors',!d.body.classList.contains('booting')&&errors.length===0,errors);
    checks('Production DOM has no duplicate IDs',new Set([...d.querySelectorAll('[id]')].map(e=>e.id)).size===d.querySelectorAll('[id]').length);
    checks('Graphite finish and removed redundant communication entries',d.documentElement.dataset.finish==='graphite'&&!$('openHarnessBtn')&&!$('railChat'));
    checks('Graphite black canvas and silver-purple accent',w.getComputedStyle(d.body).backgroundColor==='rgb(0, 0, 0)'&&w.getComputedStyle(d.documentElement).getPropertyValue('--graphite-accent').trim()==='#b7b0d3');
    checks('Primary and backup have role-only visible names',JSON.stringify([...$('provider').options].map(o=>o.textContent))===JSON.stringify(['主接口','备用接口']));
    checks('Configured primary supplies fixed price for any model or size',t.priceOf('any-image','3072x4096','a6api').unit===0.038);
    handler=c=>c.path==='/api/session'?response({...session,authenticated:true,account:'regression-member',display:'Member',role:'member',admin:false}):undefined;
    await t.loadSession();
    checks('Member role hides admin link and account aggregates',$('adminLink').hidden&&$('accountsBtn').hidden&&$('whoRole').textContent==='成员');
    checks('Member retains personal usage and Feishu history entries',!$('usageChip').hidden&&!$('feishuBtn').hidden);
    handler=c=>c.path==='/api/session'?response({...session,authenticated:true,account:'admin',role:'admin',admin:true}):undefined;
    await t.loadSession();
    checks('Administrator role retains management actions',!$('adminLink').hidden&&!$('accountsBtn').hidden);
    const names=['bg-test-a','bg-test-b','bg-test-c'];
    s.providers=names.map((name,index)=>({name,label:name,ready:true,model:'model-'+index}));
    $('provider').replaceChildren(...names.map(name=>new w.Option(name,name)));
    $('reworkProvider').replaceChildren(...names.map(name=>new w.Option(name,name)));
    handler=c=>c.path==='/api/models'?response({ok:true,models:[{id:c.url.searchParams.get('provider')+'-model',image:true}]}):undefined;
    $('provider').value=names[0];await t.loadModels(names[0]);
    $('model').value='__manual__';$('model').dispatchEvent(new w.Event('change'));
    $('modelManual').value='custom-image-one';$('modelManual').dispatchEvent(new w.Event('input'));
    $('provider').value=names[1];await t.loadModels(names[1]);
    checks('Manual model text does not leak between providers',$('modelManual').value==='');
    $('provider').value=names[0];await t.loadModels(names[0]);
    checks('Manual sentinel and text survive model refresh',$('model').value==='__manual__'&&!$('modelManual').hidden&&$('modelManual').value==='custom-image-one');
    const modelWait={};handler=c=>{if(c.path==='/api/models'){const name=c.url.searchParams.get('provider'),wait=deferred();modelWait[name]=wait;return wait.promise;}};
    s.harnessMode='rework';s.reworkTargets=['task-1'];s.reworkSourceJob='source';s.composedPrompt='adjust only';
    $('reworkProvider').value=names[1];const reworkB=t.loadReworkModels(names[1]);
    $('reworkProvider').value=names[2];const reworkC=t.loadReworkModels(names[2]);
    const postBefore=calls.filter(c=>c.method!=='GET').length;
    $('harnessInput').value='adjust';await t.submitRework();await t.sendHarnessMessage();
    checks('Rework and analysis are blocked while model list loads',$('reworkModelSel').disabled&&$('harnessApply').disabled&&$('harnessSend').disabled&&calls.filter(c=>c.method!=='GET').length===postBefore);
    modelWait[names[2]].resolve(response({ok:true,models:[{id:'latest-c-model',image:true}]}));await reworkC;
    modelWait[names[1]].resolve(response({ok:true,models:[{id:'obsolete-b-model',image:true}]}));await reworkB;
    checks('Older rework model responses cannot overwrite current provider',$('reworkProvider').value===names[2]&&$('reworkModelSel').value==='latest-c-model'&&!$('reworkModelSel').disabled);
    s.harnessMode='intake';s.files=[{file:new w.File(['test'],'item-01_view-01.png',{type:'image/png'}),name:'item-01_view-01.png',loading:false}];
    const slug=Object.keys(skill.types)[0];s.styles=[{slug,colors:new Set([skill.types[slug].colors[0].label])}];s.job=null;s.draftId='offline-draft';
    $('provider').value=names[0];$('model').value='__manual__';$('modelManual').value='custom-image-one';t.updateSummary();
    $('subject').value='garment';
    let directWrites=0,alignmentCalls=0,submittedRequirements='',alignmentPayload;
    handler=c=>{
      if(c.path==='/api/jobs'&&c.method==='POST'){directWrites++;submittedRequirements=c.options.body.get('extra_requirements')||'';return response({ok:false,error:'controlled generation rejection'},400);}
      if(c.path==='/api/harness/stream'){
        alignmentCalls++;alignmentPayload=JSON.parse(c.options.body);
        return new w.Response('event: prompt\ndata: {"prompt":"optimized prompt","requirements":"优化后的明确执行要求"}\n\nevent: done\ndata: {}\n\n',{headers:{'Content-Type':'text/event-stream'}});
      }
    };
    $('briefInput').value='   ';await t.beginGeneration();
    checks('Empty brief goes directly to default generation',directWrites===1&&alignmentCalls===0&&submittedRequirements==='');
    $('briefInput').value='保留印花，只改变版型与颜色';await t.beginGeneration();
    checks('Nonempty brief opens harness and does not submit a raw job',directWrites===1&&alignmentCalls===1&&!$('harnessDrawer').hidden);
    checks('Harness preserves explicit subject and both-reference policy',alignmentPayload.subject==='garment'&&alignmentPayload.send_reference===true&&alignmentPayload.shape_lock==='master'&&alignmentPayload.instruction==='保留印花，只改变版型与颜色');
    await t.harnessApplyAction();
    checks('Aligned generation submits optimized requirements only',directWrites===2&&submittedRequirements==='优化后的明确执行要求');
    t.closeHarness();$('briefInput').value='';await sleep(260);
    s.harnessContextKey='';
    let streamController;const stream=new w.ReadableStream({start(controller){streamController=controller;}});
    handler=c=>c.path==='/api/harness/stream'?new w.Response(stream,{headers:{'Content-Type':'text/event-stream'}}):undefined;
    $('harnessInput').value='Inspect the garment first';const streamPromise=t.sendHarnessMessage();await flush();
    checks('Harness locks all start actions while streaming',s.harnessBusy&&$('startBtn').disabled&&$('directStartBtn').disabled&&$('harnessDefault').disabled&&$('harnessSend').disabled);
    streamController.enqueue(new w.TextEncoder().encode('event: prompt\ndata: {"prompt":"one","requirements":"adjust"}\n\n'));await flush();
    checks('Intermediate prompt does not unlock manual generation',$('harnessApply').disabled&&s.harnessBusy);
    const guardedBefore=calls.filter(c=>c.method!=='GET').length;
    await t.createJob('');await t.harnessDefaultAction();await t.harnessApplyAction();await t.retryJob();await t.submitRework();
    t.closeHarness();await t.createJob('');
    checks('Closing analysis drawer cannot bypass shared submission lock',calls.filter(c=>c.method!=='GET').length===guardedBefore);
    streamController.enqueue(new w.TextEncoder().encode('event: done\ndata: {}\n\n'));await flush();
    checks('SSE done frame alone does not release active connection lock',s.harnessBusy);
    streamController.close();await streamPromise;
    checks('Stream close restores eligible start actions',!s.harnessBusy&&!$('startBtn').disabled&&!$('harnessApply').disabled);
    s.harnessMode='rework';s.reworkTargets=['task-1'];s.reworkSourceJob='original-source';s.composedPrompt='adjust only';s.requirements='adjust';
    $('harnessInput').value='adjust';t.updateSummary();
    const reworkWait=deferred();let reworkWrites=0,reworkBody;
    handler=c=>{if(c.method==='POST'){reworkWrites++;reworkBody=c.options.body;return reworkWait.promise;}};
    const reworkPromise=t.submitRework();await flush();await t.submitRework();await t.createJob('');await t.sendHarnessMessage();
    checks('Rework POST locks duplicate and competing submissions',s.busy&&reworkWrites===1&&$('harnessSend').disabled);
    checks('Rework keeps selected provider model and source',calls.at(-1).path==='/api/jobs/original-source/rework'&&reworkBody.get('provider')===names[2]&&reworkBody.get('model')==='latest-c-model');
    reworkWait.resolve(response({ok:false,error:'controlled offline rejection'},400));await reworkPromise;
    checks('Rejected rework restores controls without automatic retry',!s.busy&&reworkWrites===1&&!$('harnessApply').disabled);
    const makeJob=(id,status='done')=>({id,status,stage:id+' '+status,progress:status==='done'?100:0,created_at:Date.now()/1000,updated_at:Date.now()/1000,
      duration:status==='done'?42.25:null,
      params:{garment_name:'Offline fixture',provider:names[0],model:'custom-image-one'},stats:{total:1,done:status==='done'?1:0,skipped:0,failed:0,pending:0,queued:0,running:status==='running'?1:0},
      tasks:[{id:'task-1',item:'item-01',view:'view-01',color_name:'White',color_hex:'#fff',status:status==='done'?'done':'running',out:status==='done'?'staging/style/file.png':'',garment_name:'Fixture'}],log:[],has_zip:false});
    const pending=[];handler=c=>{if(/^\/api\/jobs\/[^/]+$/.test(c.path)&&c.method==='GET'){const wait=deferred();pending.push({wait,id:c.path.split('/').pop()});return wait.promise;}};
    t.watchJob('A',makeJob('A'));await flush();
    checks('Completed project displays total duration',!$('jobDuration').hidden&&$('jobDuration').textContent.includes('42.3秒')&&t.formatJobDuration(3661.2)==='1小时 1分 1.2秒');
    t.watchJob('B');
    checks('Historical task switch locks old cards and action buttons',s.jobLoading&&$('galleryPane').inert&&$('cancelBtn').disabled&&$('batchReworkBtn').disabled&&d.querySelector('[data-rework]').disabled&&!!$('jobLoadingSurface'));
    checks('Switching jobs hides previous total duration',$('jobDuration').hidden);
    const writesBeforeSwitch=calls.filter(c=>c.method!=='GET').length;
    await t.cancelJob();await t.retrySingle('task-1');await t.openHarness('rework',['task-1']);t.exportSelection();
    checks('Old task actions cannot target a newly selected job',calls.filter(c=>c.method!=='GET').length===writesBeforeSwitch);
    const realTimeout=w.setTimeout.bind(w),realClear=w.clearTimeout.bind(w),timers=new Map();let timerId=900000;
    w.setTimeout=(fn,delay,...args)=>{if(fn.name==='tick'&&String(fn).includes('current()')){const id=++timerId;timers.set(id,()=>{timers.delete(id);fn(...args);});return id;}return realTimeout(fn,delay,...args);};
    w.clearTimeout=id=>{timers.delete(id);realClear(id);};
    t.watchJob('A',makeJob('A'));t.watchJob('A',makeJob('A'));await flush();
    const newest=pending.at(-1);newest.wait.resolve(response({ok:true,job:makeJob('A','running')}));await flush();
    const finalStage=$('jobStage').textContent;
    pending[0].wait.reject(new Error('obsolete request failure'));
    pending[1].wait.resolve(response({ok:true,job:makeJob('B','running')}));
    pending[2].wait.resolve(response({ok:true,job:makeJob('A','running')}));await flush();
    checks('A to B to A and duplicate watches leave one polling chain',timers.size===1&&s.jobId==='A'&&s.job.id==='A');
    checks('Obsolete failures cannot overwrite the current job stage',$('jobStage').textContent===finalStage&&!$('jobStage').textContent.includes('obsolete'));
    checks('New task render clears stale operation lock',!s.jobLoading&&!$('galleryPane').inert&&!$('jobLoadingSurface'));
    const tick=timers.get(s.pollTimer);tick();await flush();pending.at(-1).wait.resolve(response({ok:true,job:makeJob('A')}));await flush();
    checks('Terminal task stops the only polling chain',timers.size===0&&s.pollTimer===null);
    w.setTimeout=realTimeout;w.clearTimeout=realClear;
    handler=null;s.job=makeJob('A');t.updateSummary();
    for(const width of [1440,1024,390,360]){
      frame.style.width=width+'px';await sleep(150);w.WorkbenchUI.mobilePane('config');await flush();
      const controls=d.querySelector('.controls').getBoundingClientRect(),canvas=d.querySelector('.workspace>.canvas').getBoundingClientRect();
      checks(`No page overflow at ${width}px`,d.documentElement.scrollWidth<=width+1,{scrollWidth:d.documentElement.scrollWidth,width});
      if(width>760)checks(`Main panel visible bounds align at ${width}px`,Math.abs(controls.top-canvas.top)<2&&Math.abs(controls.bottom-canvas.bottom)<2,{leftTop:controls.top,rightTop:canvas.top,leftBottom:controls.bottom,rightBottom:canvas.bottom});
      else{w.WorkbenchUI.mobilePane('results');await flush();const composer=d.querySelector('.composer')||$('briefInput').closest('section')||$('briefInput').parentElement;checks(`Mobile results and generation remain available at ${width}px`,$('galleryScroll').getBoundingClientRect().height>50&&$('startBtn').getBoundingClientRect().bottom<=900&&composer.getBoundingClientRect().height>0);}
    }
    checks('No uncaught browser runtime errors',errors.length===0,errors);
  }catch(error){results.push({label:'Unexpected runner failure',passed:false,detail:error.stack||String(error)});}
  finally{
    for(const name of ['bg-test-a','bg-test-b','bg-test-c'])for(const prefix of ['model.','modelManual.'])localStorage.removeItem('recolor.'+prefix+name);
    frame.remove();
  }
  const report={passed:results.filter(r=>r.passed).length,failed:results.filter(r=>!r.passed).length,results,errors,liveWrites:0};
  window.blackglassRegression=report;return report;
};