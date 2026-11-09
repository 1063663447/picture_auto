/* Evaluate on the logged-in workbench, then await runAIWorkflowRegression().
   Disposable production-source iframe; every API write is intercepted locally. */
window.runAIWorkflowRegression=async function(options){
  'use strict';
  const {keepPreview=false}=options||{};
  const results=[],errors=[],check=(label,passed,detail)=>results.push({label,passed:!!passed,...(detail===undefined?{}:{detail})});
  const origin=location.origin,read=async path=>{const r=await fetch(origin+path,{cache:'no-store'});if(!r.ok)throw Error(path+' '+r.status);return r;};
  const [html,app,ui,workflow,skill,providers,pricing,session,usage]=await Promise.all([
    read('/').then(r=>r.text()),read('/app.js').then(r=>r.text()),read('/blackglass-ui.js').then(r=>r.text()),read('/ai-workflow.js').then(r=>r.text()),
    read('/api/skill').then(r=>r.json()),read('/api/providers').then(r=>r.json()),read('/api/pricing').then(r=>r.json()),read('/api/session').then(r=>r.json()),read('/api/usage').then(r=>r.json())]);
  if(!html.includes('id="aiPhaseLabel"'))throw Error('New workbench HTML and login required');
  const frame=document.createElement('iframe');frame.id='aiWorkflowRegressionFrame';frame.title='Isolated graphite AI feedback regression';
  frame.style.cssText='position:fixed;left:-1600px;top:0;width:1440px;height:900px;border:0;background:#000;z-index:2147483647';
  const loaded=new Promise(resolve=>frame.onload=resolve);
  frame.srcdoc=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace('<head>',`<head><base href="${origin}/">`);
  document.body.appendChild(frame);await loaded;
  async function until(test,timeout=6000){const start=Date.now();while(!test()&&Date.now()-start<timeout)await sleep(35);return test();}
  const w=frame.contentWindow,d=w.document,$=id=>d.getElementById(id),sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms)),flush=()=>sleep(50);
  const response=(payload,status=200)=>new w.Response(JSON.stringify(payload),{status,headers:{'Content-Type':'application/json'}});
  const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
  const calls=[];let handler=null;
  const job=(id,status='done')=>({id,status,stage:status,progress:status==='done'?100:0,created_at:Date.now()/1000-10,updated_at:Date.now()/1000,duration:status==='done'?10:null,
    params:{garment_name:'测试连帽卫衣',provider:providers.providers[0].name,model:'fixture-image',estimate:{priced:true,unit:.038,total:.038,currency:'CNY'}},stats:{total:1,done:status==='done'?1:0,failed:0,skipped:0,queued:0,pending:0,running:status==='running'?1:0},tasks:[],log:[],has_zip:false});
  const defaults=path=>path==='/api/skill'?skill:path==='/api/providers'?providers:path==='/api/pricing'?pricing:path==='/api/session'?session:path==='/api/usage'?usage:
    path==='/api/health'?{ok:true,types:13,providers_ui:2}:path==='/api/jobs'?{ok:true,jobs:[]}:path==='/api/models'?{ok:true,models:[{id:'fixture-image',image:true}],current:'fixture-image'}:
    path.startsWith('/api/jobs/')?{ok:true,job:job(path.split('/')[3])}:{ok:true};
  w.fetch=async(input,options={})=>{const url=new URL(String(input),origin),c={path:url.pathname,url,method:options.method||'GET',options};calls.push(c);if(handler){const result=handler(c);if(result!==undefined)return await result;}if(c.method!=='GET')throw Error('Live write blocked: '+c.path);return response(defaults(c.path));};
  w.addEventListener('error',e=>errors.push(e.message));w.addEventListener('unhandledrejection',e=>{errors.push(String(e.reason?.message||e.reason));e.preventDefault();});
  const exports='state,beginGeneration,openHarness,sendHarnessMessage,harnessApplyAction,submitRework,harnessDefaultAction,createJob,closeHarness,openDrawer,updateSummary,resetChat,harnessContextKey';
  const instrumented=app.replace(/^  boot\(\)\.catch.*$/m,`  window.__aiTest={${exports}};window.__aiBoot=boot();`);
  if(instrumented===app)throw Error('Boot anchor changed');
  let t,s;
  const sse=(name,payload)=>`event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`;
  const fullStream=(requirements='保留正反面和印花，只调整版型')=>new w.Response(sse('delta',{text:'我会对照原图，保留正反面和各自印花。'})+sse('prompt',{prompt:'optimized execution prompt',requirements})+sse('done',{ok:true}),{headers:{'Content-Type':'text/event-stream'}});
  const writes=path=>calls.filter(c=>c.method==='POST'&&c.path===path).length;
  const pause=()=>sleep(285);
  async function reset(brief='按原图保留正反面'){
    t.closeHarness();await pause();
    if(s.pollTimer)w.clearTimeout(s.pollTimer);s.pollTimer=null;s.pollController?.abort();s.pollEpoch++;
    t.resetChat();s.harnessContextKey='';s.harnessMode=null;s.harnessBusy=false;s.harnessOpening=false;s.busy=false;s.jobLoading=false;s.jobId=null;s.job=null;s.pendingBrief='';s.draftId='';
    const slug=Object.keys(skill.types)[0];s.styles=[{slug,colors:new Set([skill.types[slug].colors[0].label])}];
    s.files=[{file:new w.File(['fixture-bytes'],'item-01_view-01.png',{type:'image/png'}),name:'item-01_view-01.png',loading:false}];
    $('briefInput').value=brief;$('subject').value='garment';t.updateSummary();
  }
  async function readyIntake(){
    await reset();handler=c=>c.path==='/api/drafts'&&c.method==='POST'?response({ok:true,draft_id:'d-fixture'}):c.path==='/api/harness/stream'?fullStream():undefined;
    await t.beginGeneration();await flush();
  }
  try{
    new w.Function(ui)();new w.Function(workflow)();new w.Function(instrumented)();await w.__aiBoot;await flush();t=w.__aiTest;s=t.state;
    check('Production initializes with the AI workflow and no runtime errors',!d.body.classList.contains('booting')&&!!w.AIWorkflow&&errors.length===0);
    check('No redundant communication button or rail shortcut',!$('openHarnessBtn')&&!$('railChat'));
    check('Graphite surfaces use black, neutral glass and silver-purple accent',w.getComputedStyle(d.body).backgroundColor==='rgb(0, 0, 0)'&&w.getComputedStyle(d.querySelector('.controls'),'::before').backgroundColor==='rgba(15, 15, 19, 0.94)'&&w.getComputedStyle(d.documentElement).getPropertyValue('--graphite-accent').trim()==='#b7b0d3');
    await reset('   ');const emptyBefore=writes('/api/jobs'),streamsBefore=writes('/api/harness/stream');
    handler=c=>c.path==='/api/jobs'&&c.method==='POST'?response({ok:false,error:'controlled no-cost rejection'},400):undefined;
    await t.beginGeneration();
    check('Empty brief submits default generation without opening AI',$('harnessDrawer').hidden&&writes('/api/jobs')===emptyBefore+1&&writes('/api/harness/stream')===streamsBefore);
    await reset();const draft=deferred(),connect=deferred();let controller;
    const stream=new w.ReadableStream({start(c){controller=c;}});
    handler=c=>c.path==='/api/drafts'&&c.method==='POST'?draft.promise:c.path==='/api/harness/stream'?connect.promise:undefined;
    const begin=t.beginGeneration();await flush();
    await until(()=>$('harnessDrawer').classList.contains('on')&&d.querySelector('.app').inert&&$('harnessDrawer').contains(d.activeElement));
    check('Animated modal appears before draft upload resolves',!$('harnessDrawer').hidden&&s.harnessOpening&&w.AIWorkflow.snapshot().phase==='preparing');
    check('Page is inert and keyboard focus stays in modal',d.querySelector('.app').inert&&$('harnessDrawer').contains(d.activeElement));
    const modal=$('harnessDrawer').getBoundingClientRect();check('AI workflow is centered and bounded on desktop',Math.abs(modal.left+modal.width/2-720)<2&&modal.top>=0&&modal.bottom<=900,{x:modal.x,y:modal.y,width:modal.width,height:modal.height});
    const editedBrief=$('harnessInput').value+'；补充：不要改变文字字形';$('harnessInput').value=editedBrief;
    draft.resolve(response({ok:true,draft_id:'d-stream'}));await flush();
    check('Actual connection request drives connecting state',w.AIWorkflow.snapshot().phase==='connecting'&&s.harnessBusy&&$('harnessApply').disabled);
    check('Typing during preparation survives draft completion and is sent',JSON.parse(calls.filter(c=>c.path==='/api/harness/stream').at(-1).options.body).instruction===editedBrief);
    connect.resolve(new w.Response(stream,{headers:{'Content-Type':'text/event-stream'}}));await flush();
    controller.enqueue(new w.TextEncoder().encode(sse('tool',{name:'inspect_images'})));await flush();
    check('Tool event drives image-inspection phase and counter',w.AIWorkflow.snapshot().phase==='inspecting'&&w.AIWorkflow.snapshot().toolCalls===1&&$('aiToolCount').textContent.includes('1'));
    controller.enqueue(new w.TextEncoder().encode(sse('result',{name:'inspect_images',text:'原始上传图 800×800；当前成品1254×1254。'})));await flush();
    check('Tool result remains inspectable in dialog',$('chatLog').querySelector('.tool-result').textContent.includes('1254×1254')&&$('chatLog').querySelector('.tool-line').classList.contains('done'));
    const sentence='保留正反面，背面骷髅图案与正面胸标分别保留。';
    const bytes=new w.TextEncoder().encode(sse('delta',{text:sentence}));controller.enqueue(bytes.slice(0,43));controller.enqueue(bytes.slice(43));await sleep(180);
    check('Streaming text updates actual character feedback',w.AIWorkflow.snapshot().characters===Array.from(sentence).length&&$('aiCharacterCount').textContent.includes(String(Array.from(sentence).length)));
    const during=writes('/api/harness/stream');t.closeHarness();await pause();
    check('Closing active AI shows a resumable actual-session indicator',!$('aiResumeBtn').hidden&&s.harnessBusy&&!d.querySelector('.app').inert);
    $('aiResumeBtn').click();await flush();
    await until(()=>$('harnessDrawer').classList.contains('on')&&!$('harnessDrawer').hidden);
    check('Resume does not create a second AI request',!$('harnessDrawer').hidden&&writes('/api/harness/stream')===during);
    t.closeHarness();t.openDrawer('harness');await sleep(320);
    await until(()=>$('harnessDrawer').classList.contains('on')&&!$('harnessDrawer').hidden);
    check('Fast close and reopen survives old animation timers',!$('harnessDrawer').hidden&&$('harnessDrawer').classList.contains('on'));
    controller.enqueue(new w.TextEncoder().encode(sse('prompt',{prompt:'safe optimized prompt',requirements:'保留双面，保持白色'})));
    controller.enqueue(new w.TextEncoder().encode(sse('done',{ok:true})));await flush();
    check('Server done frame does not unlock until stream closes',s.harnessBusy&&$('harnessApply').disabled&&w.AIWorkflow.snapshot().phase!=='ready');
    controller.close();await begin;await flush();
    check('Prompt-ready stage requires server completion',w.AIWorkflow.snapshot().phase==='ready'&&!$('harnessApply').disabled&&$('aiProgress').getAttribute('aria-busy')==='false');
    check('Stream text rendered completely without duplicate content',$('chatLog').textContent.includes(sentence)&&$('chatLog').textContent.split(sentence).length===2);
    check('Progress is truthful stages with no invented numeric percentage',!$('aiProgress').hasAttribute('aria-valuenow')&&$('aiProgress').getAttribute('aria-valuetext').includes('提示词已就绪'));
    const repeatBefore=writes('/api/harness/stream');t.closeHarness();await pause();await t.beginGeneration();await flush();
    check('Clicking generate with unchanged prepared brief restores result',writes('/api/harness/stream')===repeatBefore&&!$('harnessDrawer').hidden&&w.AIWorkflow.snapshot().phase==='ready');
    t.closeHarness();await pause();$('aiResumeBtn').focus();$('aiResumeBtn').click();
    await until(()=>$('harnessDrawer').contains(d.activeElement)&&$('harnessDrawer').classList.contains('on'));
    d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    await until(()=>$('harnessDrawer').hidden&&d.activeElement===$('aiResumeBtn'));
    check('Escape after resume restores the visible keyboard invoker',!$('aiResumeBtn').hidden&&d.activeElement===$('aiResumeBtn'));
    $('aiResumeBtn').click();await until(()=>$('harnessDrawer').classList.contains('on'));
    const staleBefore=writes('/api/jobs');$('subject').value='person';t.updateSummary();await t.harnessApplyAction();
    check('Changed settings cannot submit an old analysis',$('harnessApply').disabled&&w.AIWorkflow.snapshot().phase==='stale'&&writes('/api/jobs')===staleBefore);
    await reset();handler=c=>c.path==='/api/drafts'&&c.method==='POST'?response({ok:false,error:'controlled upload error'},400):undefined;
    await t.beginGeneration();await flush();
    check('Draft failure remains visible and does not claim generation started',w.AIWorkflow.snapshot().phase==='error'&&!s.harnessOpening&&!s.harnessBusy&&!$('harnessDrawer').hidden&&$('chatLog').textContent.includes('没有提交生图任务'));
    await reset();const oldDraft=deferred();handler=c=>c.path==='/api/drafts'&&c.method==='POST'?oldDraft.promise:undefined;
    const stalePreparation=t.beginGeneration();await flush();t.closeHarness();await pause();
    s.files=[{file:new w.File(['new-source'],'item-02_view-01.png',{type:'image/png'}),name:'item-02_view-01.png',loading:false}];s.draftId='';t.updateSummary();
    oldDraft.resolve(response({ok:true,draft_id:'d-stale-upload'}));await stalePreparation;
    check('Late original upload response cannot poison the next draft',s.draftId===''&&!s.harnessBusy&&!s.harnessOpening&&['error','stale'].includes(w.AIWorkflow.snapshot().phase),{draftId:s.draftId,phase:w.AIWorkflow.snapshot().phase});
    const freshDraftBefore=writes('/api/drafts');
    handler=c=>c.path==='/api/drafts'&&c.method==='POST'?response({ok:true,draft_id:'d-new-source'}):c.path==='/api/harness/stream'?fullStream():undefined;
    await t.beginGeneration();await flush();
    check('Restart after changed upload requests and analyzes the fresh draft',writes('/api/drafts')===freshDraftBefore+1&&s.draftId==='d-new-source'&&JSON.parse(calls.filter(c=>c.path==='/api/harness/stream').at(-1).options.body).draft_id==='d-new-source');
    await reset();handler=c=>c.path==='/api/drafts'&&c.method==='POST'?response({ok:true,draft_id:'d-broken'}):c.path==='/api/harness/stream'?new w.Response(sse('delta',{text:'仍在分析中'})+sse('prompt',{prompt:'incomplete',requirements:'未确认'}),{headers:{'Content-Type':'text/event-stream'}}):undefined;
    await t.beginGeneration();await flush();
    check('Premature SSE end becomes an error without automatic re-submit',w.AIWorkflow.snapshot().phase==='error'&&!s.harnessBusy&&$('aiPhaseDetail').textContent.includes('提前结束'));
    check('Incomplete stream never enables confirmation of a partial prompt',$('harnessApply').disabled&&!s.requirements&&!s.composedPrompt);
    await reset();handler=c=>c.path==='/api/drafts'&&c.method==='POST'?response({ok:true,draft_id:'d-crlf'}):c.path==='/api/harness/stream'?new w.Response((sse('delta',{text:'请说明保留哪些细节。'})+sse('done',{ok:true})).replace(/\n/g,'\r\n'),{headers:{'Content-Type':'text/event-stream'}}):undefined;
    await t.beginGeneration();await flush();
    check('CRLF event streams and follow-up-only answers are supported',w.AIWorkflow.snapshot().phase==='waiting'&&!s.harnessBusy&&$('harnessApply').disabled);
    await readyIntake();const post=deferred(),jobsBefore=writes('/api/jobs');
    handler=c=>c.path==='/api/jobs'&&c.method==='POST'?post.promise:undefined;
    const submit=t.harnessApplyAction();await flush();
    check('Manual confirmation displays submission phase with shared lock',w.AIWorkflow.snapshot().phase==='submitting'&&s.busy&&$('harnessApply').disabled);
    await t.harnessApplyAction();await t.sendHarnessMessage();
    check('Confirmation cannot submit duplicate jobs',writes('/api/jobs')===jobsBefore+1);
    post.resolve(response({ok:true,job:job('fixture-created')}));await submit;await flush();
    check('Server ID produces a visible task receipt and gallery action',w.AIWorkflow.snapshot().phase==='submitted'&&!$('harnessDrawer').hidden&&$('aiReceiptId').textContent==='fixture-created'&&!$('aiViewResults').hidden&&$('harnessApply').disabled);
    $('aiViewResults').click();await pause();
    check('View generated task closes modal and restores workspace',$('harnessDrawer').hidden&&!d.querySelector('.app').inert&&s.jobId==='fixture-created');
    await reset();const agentJob='fixture-agent-created';
    d.querySelector('[data-view="logs"]').click();w.WorkbenchUI.mobilePane('config');
    handler=c=>c.path==='/api/drafts'&&c.method==='POST'?response({ok:true,draft_id:'d-agent'}):c.path==='/api/harness/stream'?new w.Response(sse('prompt',{prompt:'done',requirements:'优化要求',job_id:agentJob})+sse('job',{job_id:agentJob})+sse('done',{ok:true}),{headers:{'Content-Type':'text/event-stream'}}):undefined;
    const beforeAgentJobs=writes('/api/jobs');await t.beginGeneration();await flush();
    check('Agent-created job does not trigger a second manual generation',w.AIWorkflow.snapshot().phase==='submitted'&&s.harnessSubmittedJob===agentJob&&writes('/api/jobs')===beforeAgentJobs);
    check('Duplicate prompt/job receipts watch one job once',calls.filter(c=>c.method==='GET'&&c.path==='/api/jobs/'+agentJob).length===1);
    $('aiViewResults').click();await pause();
    check('View progress selects gallery and results pane even when starting in logs',s.view==='gallery'&&!$('galleryPane').hidden&&d.body.dataset.mobilePane==='results');
    await reset();s.jobId='fixture-source';s.job=job('fixture-source');s.selected=new Set(['task-a']);
    handler=c=>c.path==='/api/models'?response({ok:true,models:[{id:'fixture-image',image:true}]}):undefined;
    await t.openHarness('rework',['task-a']);await flush();
    check('Existing rework entry opens the same studio without intake upload',!$('reworkModel').hidden&&w.AIWorkflow.snapshot().phase==='idle'&&$('harnessTitle').textContent==='AI 改图工作室');
    s.composedPrompt='preserve original front and back';s.requirements='恢复双面并保持白色';$('harnessInput').value='恢复双面';
    let reworkForm;handler=c=>c.path==='/api/jobs/fixture-source/rework'&&c.method==='POST'?(reworkForm=c.options.body,response({ok:true,job:job('fixture-reworked')})):undefined;
    await t.submitRework();await flush();
    check('Rework preserves original-source API, model, subject and prompt',reworkForm.get('subject')==='garment'&&reworkForm.get('task_ids')==='task-a'&&reworkForm.get('prompt_override')===s.composedPrompt&&reworkForm.get('provider')===providers.providers[0].name&&w.AIWorkflow.snapshot().phase==='submitted');
    for(const [width,height] of [[1440,900],[1024,768],[390,844],[360,740]]){
      frame.style.width=width+'px';frame.style.height=height+'px';w.AIWorkflow.start('intake');w.AIWorkflow.phase('ready');t.openDrawer('harness');await sleep(300);
      await until(()=>$('harnessDrawer').classList.contains('on')&&new w.DOMMatrix(w.getComputedStyle($('harnessDrawer')).transform).a>=.9999);
      const r=$('harnessDrawer').getBoundingClientRect(),foot=$('harnessActions').getBoundingClientRect();
      check(`AI modal stays inside ${width}×${height}`,r.left>=0&&r.top>=0&&r.right<=width+1&&r.bottom<=height+1&&foot.bottom<=r.bottom,{x:r.x,y:r.y,width:r.width,height:r.height,footerBottom:foot.bottom});
      check(`Graphite workspace does not overflow at ${width}px`,d.documentElement.scrollWidth<=width+1);
      t.closeHarness();await pause();
    }
    check('No uncaught production runtime errors',errors.length===0,errors);
    if(keepPreview){frame.style.left='0';frame.style.width='min(1440px,100vw)';frame.style.height='100dvh';s.harnessSubmittedJob='';s.harnessMode='intake';s.job=null;s.jobId=null;w.AIWorkflow.start('intake');w.AIWorkflow.phase('processing','正在对照原图，提炼正反面构图和印花约束。');t.openDrawer('harness');}
  }catch(error){check('Regression runner completed',false,error.stack||String(error));}
  finally{if(!keepPreview)frame.remove();}
  const report={passed:results.filter(r=>r.passed).length,failed:results.filter(r=>!r.passed).length,results,errors,liveWrites:0};
  window.aiWorkflowRegression=report;return report;
};
