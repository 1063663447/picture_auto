/* Evaluate on the logged-in workbench, then await runClipboardCompletionRegression().
   Production-source disposable iframe only. All iframe fetches are intercepted;
   non-GET requests are blocked, and no generation, clipboard permission or live
   job mutation is used. Optional {sources, origin} supports offline fixture runs. */
window.runClipboardCompletionRegression=async function(options){
  'use strict';
  options=options||{};
  const results=[],errors=[],calls=[],check=(label,passed,detail)=>results.push({label,passed:!!passed,...(detail===undefined?{}:{detail})});
  const origin=options.origin||location.origin,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms)),flush=()=>sleep(40);
  const until=async(test,timeout=5000)=>{const start=Date.now();while(!test()&&Date.now()-start<timeout)await sleep(25);if(!test())throw Error('Timed out waiting for controlled iframe state');};
  const read=async path=>{const r=await fetch(origin+path,{cache:'no-store'});if(!r.ok)throw Error(path+' '+r.status);return r;};
  const sources=options.sources||Object.fromEntries(await Promise.all([
    ['html','/'],['app','/app.js'],['ui','/blackglass-ui.js'],['workflow','/ai-workflow.js'],
    ['skill','/api/skill'],['providers','/api/providers'],['pricing','/api/pricing'],['session','/api/session'],['usage','/api/usage'],
  ].map(async([key,path])=>[key,await read(path).then(r=>['html','app','ui','workflow'].includes(key)?r.text():r.json())])));
  const {html,app,ui,workflow,skill,providers,pricing,session,usage}=sources;
  if(!html.includes('id="gridImgs"'))throw Error('Production workbench HTML required; log in or supply isolated sources.');
  const frame=document.createElement('iframe');frame.id='clipboardCompletionRegressionFrame';frame.title='Isolated clipboard and completion regression';
  frame.style.cssText='position:fixed;left:-1700px;top:0;width:1440px;height:900px;border:0;';
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  frame.srcdoc=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace('<head>',`<head><base href="${origin}/">`);
  document.body.appendChild(frame);await loaded;
  const w=frame.contentWindow,d=w.document,$=id=>d.getElementById(id),timers=new Map();let timerId=800000,handler=null,blockedWrites=0;
  const realTimeout=w.setTimeout.bind(w),realClear=w.clearTimeout.bind(w),nativeBitmap=w.createImageBitmap.bind(w);
  const response=(data,status=200)=>new w.Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
  const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
  const storage=new Map();Object.defineProperty(w,'localStorage',{value:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)}});
  const defaults=path=>path==='/api/skill'?skill:path==='/api/providers'?providers:path==='/api/pricing'?pricing:path==='/api/session'?session:path==='/api/usage'?usage:
    path==='/api/health'?{ok:true,types:Object.keys(skill.types).length,providers_ui:providers.providers.length}:path==='/api/jobs'?{ok:true,jobs:[]}:
    path==='/api/models'?{ok:true,models:[{id:'clipboard-fixture-image',image:true}],current:'clipboard-fixture-image'}:path==='/api/gate'?{ok:true,enabled:false}:{ok:true};
  w.fetch=async(input,options={})=>{
    const url=new URL(String(input),origin),call={path:url.pathname,url,method:(options.method||'GET').toUpperCase(),options};calls.push(call);
    if(call.method!=='GET'){blockedWrites++;throw Error('Live write blocked by clipboard regression: '+call.method+' '+call.path);}
    if(handler){const value=handler(call);if(value!==undefined)return await value;}
    if(/^\/api\/jobs\//.test(call.path))throw Error('Uncontrolled job read blocked: '+call.path);
    return response(defaults(call.path));
  };
  w.addEventListener('error',event=>errors.push(event.message));
  w.addEventListener('unhandledrejection',event=>{errors.push(String(event.reason?.message||event.reason));event.preventDefault();});
  const instrumented=app.replace(/^  boot\(\)\.catch.*$/m,'  window.__clipboardTest={state,watchJob,renderJob,updateSummary,createJob,startBlockedReason,jobPhase,closeHarness};window.__clipboardBoot=boot();');
  if(instrumented===app){frame.remove();throw Error('Boot anchor changed; refusing to execute unguarded app.');}
  let s;
  const paste=(target,files,{items=true,fileList=true,text=''}={})=>{
    const data={items:items?files.map(file=>({kind:'file',type:file.type,getAsFile:()=>file})):[],files:fileList?files:[],getData:type=>type==='text/plain'?text:'',types:[...(files.length?['Files']:[]),...(text?['text/plain']:[])]};
    const event=new w.Event('paste',{bubbles:true,cancelable:true});Object.defineProperty(event,'clipboardData',{value:data});target.dispatchEvent(event);return event;
  };
  const readyPreviews=()=>until(()=>s.files.every(file=>!file.loading));
  try{
    new w.Function(ui)();new w.Function(workflow)();new w.Function(instrumented)();await w.__clipboardBoot;await flush();
    const t=w.__clipboardTest;s=t.state;await until(()=>!$('model').disabled&&$('model').value);
    check('Production code boots without runtime errors',!d.body.classList.contains('booting')&&errors.length===0,errors.slice());
    check('Original panel exposes paste hint and accessible description',$('clipboardHint')?.textContent.includes('Ctrl+V')&&$('drop').getAttribute('aria-describedby').includes('clipboardHint'));
    const canvas=d.createElement('canvas');canvas.width=640;canvas.height=800;canvas.getContext('2d').fillRect(0,0,640,800);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
    const original=new w.File([blob],'image.png',{type:'image/png',lastModified:1234});
    const previewWait=deferred();w.createImageBitmap=()=>previewWait.promise;$('drop').focus();
    const first=paste($('drop'),[original]);
    check('Clipboard items/files mirrors add one loading original',first.defaultPrevented&&s.files.length===1&&s.files[0].loading&&s.files[0].file.size===original.size&&$('startBtn').disabled);
    const firstEntry=s.files[0];previewWait.resolve(await nativeBitmap(original));w.createImageBitmap=nativeBitmap;await readyPreviews();
    const preview=new w.Image();preview.src=firstEntry.url;await preview.decode();
    const bytes=async file=>Array.from(new Uint8Array(await file.arrayBuffer())).join(',');
    check('Clipboard keeps full original bytes and existing bounded preview',await bytes(firstEntry.file)===await bytes(original)&&firstEntry.file.type==='image/png'&&preview.naturalWidth===160&&preview.naturalHeight===200&&firstEntry.url.startsWith('blob:'));
    check('Unnamed clipboard image receives item/view-compatible filename',firstEntry.name==='item-01_view-01.png');
    d.activeElement.blur();paste(d.body,[original]);await readyPreviews();
    check('Identical image can be pasted again in a future event',s.files.length===2&&s.files[1].name==='item-02_view-01.png');
    const named=new w.File([blob],'item-07_view-02.png',{type:'image/png',lastModified:1234});paste(d.body,[named]);await readyPreviews();
    check('Named multi-view originals keep their filename and File object',s.files.at(-1).name===named.name&&s.files.at(-1).file===named);
    const nativeBefore=s.files.length,nativeClipboard=new w.DataTransfer();nativeClipboard.items.add(named);
    const nativePaste=new w.ClipboardEvent('paste',{clipboardData:nativeClipboard,bubbles:true,cancelable:true});d.body.dispatchEvent(nativePaste);await readyPreviews();
    check('Native ClipboardEvent mirrors are deduplicated within the event',nativePaste.defaultPrevented&&s.files.length===nativeBefore+1&&await bytes(s.files.at(-1).file)===await bytes(named));
    const batchBefore=s.files.length,reserved=new w.File([blob],'item-90_view-02.png',{type:'image/png'});paste(d.body,[original,original,reserved]);await readyPreviews();
    check('Batch retains repeated images and reserves explicit item/view names',s.files.length===batchBefore+3&&s.files[batchBefore].name==='item-91_view-01.png'&&s.files[batchBefore+1].name==='item-92_view-01.png'&&s.files.at(-1).name===reserved.name);
    const beforeFallback=s.files.length;paste(d.body,[original],{items:false});await readyPreviews();paste(d.body,[original],{fileList:false});await readyPreviews();
    check('Both clipboard files-only and items-only payloads work',s.files.length===beforeFallback+2);
    const beforeText=s.files.length,textPaste=paste(d.body,[],{text:'plain text'});
    check('Page text paste is not prevented or treated as an image',!textPaste.defaultPrevented&&s.files.length===beforeText);
    for(const kind of ['textarea','input','contenteditable']){
      const editor=d.createElement(kind==='contenteditable'?'div':kind);if(kind==='contenteditable')editor.contentEditable='true';d.body.appendChild(editor);editor.focus();
      const event=paste(editor,[original],{text:'keep this text'}),pageEvent=paste(d.body,[original],{text:'focus guard'});
      check('Image/text paste stays native in '+kind,!event.defaultPrevented&&!pageEvent.defaultPrevented&&s.files.length===beforeText);editor.remove();
    }
    const drawer=$('harnessDrawer');drawer.hidden=false;drawer.classList.add('on');await flush();$('harnessSend').focus();
    const chatPaste=paste($('harnessSend'),[original],{text:'chat text'});
    check('AI chat focus never adds original images',!chatPaste.defaultPrevented&&s.files.length===beforeText);t.closeHarness();await sleep(300);d.activeElement?.blur();
    const gif=new w.File(['bad'],'image.gif',{type:'image/gif'});paste(d.body,[gif]);await flush();
    const large=new w.File([blob],'too-large.png',{type:'image/png'});Object.defineProperty(large,'size',{value:20*1024*1024+1});paste(d.body,[large]);await flush();
    check('Clipboard uses existing format and 20 MB validation',s.files.length===beforeText&&d.body.textContent.includes('PNG、JPG 或 WebP')&&d.body.textContent.includes('20 MB'));
    paste(d.body,[new w.File(['invalid image bytes'],'broken.png',{type:'image/png'})]);await readyPreviews();
    check('Failed asynchronous image decode removes the invalid original',s.files.length===beforeText&&d.body.textContent.includes('图片无法读取'));
    const removedWait=deferred();w.createImageBitmap=()=>removedWait.promise;paste(d.body,[original]);const removedEntry=s.files.at(-1);$('thumbs').lastElementChild.querySelector('button').click();
    removedWait.resolve(await nativeBitmap(original));w.createImageBitmap=nativeBitmap;await flush();
    check('Removing a clipboard image during preview does not resurrect it',s.files.length===beforeText&&!s.files.includes(removedEntry)&&!removedEntry.url);
    const inputData=new w.DataTransfer();inputData.items.add(named);$('fileInput').files=inputData.files;$('fileInput').dispatchEvent(new w.Event('change'));await readyPreviews();
    const dropData=new w.DataTransfer();dropData.items.add(named);
    const dropEvent=new w.Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(dropEvent,'dataTransfer',{value:dropData});$('drop').dispatchEvent(dropEvent);await readyPreviews();
    check('Existing picker and file-drop paths still keep original bytes',s.files.length===beforeText+2&&s.files.at(-1).name===named.name&&await bytes(s.files.at(-1).file)===await bytes(named)&&$('fileInput').value==='',{count:s.files.length,expected:beforeText+2,name:s.files.at(-1)?.name});
    w.setTimeout=(fn,delay,...args)=>{if(fn.name==='tick'&&String(fn).includes('current()')){const id=++timerId;timers.set(id,()=>{timers.delete(id);return fn(...args);});return id;}return realTimeout(fn,delay,...args);};
    w.clearTimeout=id=>{timers.delete(id);realClear(id);};
    const makeJob=(id,status='running',completed=8,failed=0)=>({id,status,stage:status==='done'?'旧重试提示':'生成中',progress:completed*10,created_at:Date.now()/1000-30,updated_at:Date.now()/1000,duration:status==='done'?30:null,
      params:{garment_name:'Offline fixture',provider:$('provider').value,model:$('model').value},stats:{total:10,done:completed,skipped:0,failed,pending:0,queued:10-completed-failed,running:0},
      tasks:Array.from({length:10},(_,i)=>({id:'task-'+i,item:'item-01',view:'view-01',color_name:'White',color_hex:'#fff',status:i<completed?'done':i<completed+failed?'failed':'queued',out:'',thumb:''})),log:[],has_zip:status==='done',zip:'/fixture/completed.zip',error:'接口限流，正在重试'});
    const pending=[];handler=call=>{if(/^\/api\/jobs\/[^/]+$/.test(call.path)){const wait=deferred();pending.push({wait,call});return wait.promise;}};
    const answer=async job=>{const request=pending.shift();if(!request)throw Error('Expected a controlled job request');request.wait.resolve(response({ok:true,job}));await flush();};
    const tick=()=>{const callback=timers.get(s.pollTimer);if(!callback)throw Error('Expected exactly one polling timer');callback();};
    const retrying=makeJob('completion','running',8,1);t.watchJob('completion',retrying);await answer(retrying);
    check('Retrying image phase stays generation-busy with one poll chain',$('startBtn').disabled&&$('startBtn').textContent.includes('生成中')&&d.body.classList.contains('busy')&&timers.size===1&&s.job.status==='running');
    tick();const packing=makeJob('completion','running',10);await answer(packing);
    check('All 10 images done shows packaging without faking terminal state',$('jobStage').textContent==='生成完成，正在打包'&&$('startBtn').textContent.includes('10 / 10 · 整理打包')&&$('progressStats').textContent.includes('正在打包')&&s.job.status==='running'&&s.job.progress===100);
    check('Unfinished packaging is below 100 percent and keeps actions locked',Number($('progressBar').getAttribute('aria-valuenow'))<100&&!$('progressBar').classList.contains('done')&&$('jobDuration').hidden&&$('downloadBtn').hidden&&$('startBtn').disabled&&$('directStartBtn').disabled&&timers.size===1);
    const beforeDuplicate=calls.length;await t.createJob('');
    check('Packaging cannot start a duplicate generation',calls.length===beforeDuplicate&&!!t.startBlockedReason()&&blockedWrites===0);
    tick();pending.shift().wait.reject(new Error('controlled poll timeout'));await flush();
    check('Polling error preserves packaging lock and schedules one retry',$('jobStage').textContent.includes('正在打包')&&$('jobStage').textContent.includes('正在重试')&&$('startBtn').disabled&&timers.size===1);
    tick();await answer(packing);
    check('Recovered polling clears temporary warning without duplicate chains',$('jobStage').textContent==='生成完成，正在打包'&&timers.size===1);
    tick();await answer(makeJob('completion','done',10));
    check('Only real terminal completion releases busy and stops polling',!$('startBtn').disabled&&!$('directStartBtn').disabled&&!d.body.classList.contains('busy')&&s.pollTimer===null&&timers.size===0&&$('progressBar').getAttribute('aria-valuenow')==='100'&&$('progressBar').classList.contains('done'));
    check('Final success wins over stale retry warning and enables archive',$('jobStage').textContent==='生成完成'&&!$('progressStats').textContent.includes('重试')&&!$('downloadBtn').hidden&&$('downloadBtn').getAttribute('href')==='/fixture/completed.zip'&&!$('jobDuration').hidden&&$('retryBtn').hidden);
    t.watchJob('completion',makeJob('completion','done',10));pending.shift().wait.reject(new Error('completed refresh timeout'));await flush();
    check('Refresh failure cannot overwrite authoritative final success',$('jobStage').textContent==='生成完成'&&!$('startBtn').disabled&&timers.size===0&&s.pollTimer===null);
    const partial=makeJob('completion','partial',9,1);partial.stage='部分完成';partial.error='仍有一张失败';t.renderJob(partial);
    check('Actual terminal failure details remain visible',$('jobStage').textContent==='部分完成'&&$('progressStats').textContent.includes('仍有一张失败')&&!$('retryBtn').hidden);
    t.watchJob('A',makeJob('A'));const oldA=pending.shift();t.watchJob('B');const oldB=pending.shift();t.watchJob('A',makeJob('A'));const priorA=pending.shift();t.watchJob('A',makeJob('A'));const latestA=pending.shift();
    latestA.wait.resolve(response({ok:true,job:makeJob('A','running',10)}));await flush();const latestStage=$('jobStage').textContent;
    oldA.wait.reject(new Error('obsolete poll failure'));oldB.wait.resolve(response({ok:true,job:makeJob('B','done',10)}));priorA.wait.resolve(response({ok:true,job:makeJob('A','done',10)}));await flush();
    check('A/B/A and repeated watch guards reject stale success and failures',s.jobId==='A'&&s.job.id==='A'&&s.job.status==='running'&&$('jobStage').textContent===latestStage&&timers.size===1&&!s.jobLoading&&oldA.call.options.signal.aborted&&oldB.call.options.signal.aborted&&priorA.call.options.signal.aborted);
    t.renderJob(makeJob('B','done',10));
    check('Direct stale render cannot overwrite current packaging job',s.job.id==='A'&&s.job.status==='running'&&$('jobStage').textContent===latestStage);
    tick();await answer(makeJob('A','done',10));
    check('Latest terminal response ends the single surviving poll chain',timers.size===0&&s.pollTimer===null&&!$('startBtn').disabled);
    check('No API writes or live generation were attempted',blockedWrites===0&&calls.every(call=>call.method==='GET'));
    check('No uncaught runtime errors',errors.length===0,errors.slice());
  }catch(error){check('Unexpected runner failure',false,error.stack||String(error));}
  finally{
    if(s){++s.pollEpoch;s.pollController?.abort();if(s.pollTimer)w.clearTimeout(s.pollTimer);s.files.forEach(file=>{if(file.url)w.URL.revokeObjectURL(file.url);});}
    w.createImageBitmap=nativeBitmap;w.setTimeout=realTimeout;w.clearTimeout=realClear;frame.remove();
  }
  const report={passed:results.filter(row=>row.passed).length,failed:results.filter(row=>!row.passed).length,results,errors,liveWrites:0,blockedWrites};
  window.clipboardCompletionRegression=report;return report;
};