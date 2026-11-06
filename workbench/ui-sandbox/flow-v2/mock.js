/* Local API simulator. API calls never reach a provider or the real workbench. */
(() => {
  'use strict';
  const catalog = window.FLOW_CATALOG;
  const bySlug = Object.fromEntries(catalog.map(s => [s.slug, s]));
  const baseSlugs = ['23-sorona-tee','24-long-sleeve','21-kids-sorona-tee','10-stretch-tee','03-crewneck-sweatshirt','06-hoodie'];
  const nativeFetch = window.fetch.bind(window);
  const jobs = new Map();
  const resources = new Set();
  const requests = [];
  let prepared = null;
  let bridge = null;
  let sequence = 0;
  let scenario = 'normal';
  let timeOffset = 0;
  const now = () => Date.now() + timeOffset;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const copy = value => JSON.parse(JSON.stringify(value));
  const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: {'Content-Type':'application/json'} });
  const prices = {'gemini-3.1-flash-image-preview':.045, 'gpt-image-1.5':.085, 'seedream-4.5':.038};
  function selectionsFor(stress = false) {
    return (stress ? catalog.filter(s => s.colors.length >= 3).map(s => s.slug) : baseSlugs).map(slug => ({garment_type:slug, garment_name:bySlug[slug].display_name, colors:bySlug[slug].colors.slice(0,3).map(c => c.label)}));
  }
  function getSummary(job) {
    materialize(job);
    const stats = {total:job.tasks.length,pending:0,queued:0,running:0,done:0,failed:0,skipped:0,canceled:0,finished:0,rate_limited:0};
    job.tasks.forEach(t => { stats[t.status]++; if (['done','failed','skipped','canceled'].includes(t.status)) stats.finished++; if(t.status === 'failed' && t.error_kind === 'rate_limit') stats.rate_limited++; });
    job.stats = stats;
    job.progress = stats.total ? stats.finished / stats.total * 100 : 0;
    job.params.queue = {ahead:0,queued:stats.queued,limit:4};
    job.tasks.filter(t => t.status === 'queued').forEach((task, index) => { task.queue_ahead = index; });
    if (job._canceled) { job.status='canceled'; job.stage=`已取消 · 保留 ${stats.done} 张成品`; }
    else if (stats.finished === stats.total) { job.status=stats.failed ? 'partial':'done'; job.stage=stats.failed ? `已结束 · ${stats.failed} 张可重试`:'全部完成 · 演示成品可下载'; }
    else { job.status='running'; job.stage=stats.running ? `正在生成 ${stats.running} 张 · 后续自动排队`:'提交成功 · 等待生成'; }
    job.has_zip = stats.done > 0;
    job.zip = '#download-' + job.id;
    job.updated_at = now()/1000;
    return job;
  }
  function materialize(job) {
    if(job._canceled) return;
    const elapsed = now() - job._epoch;
    let changed = false;
    job.tasks.forEach((task,index) => {
      if (['done','failed','canceled'].includes(task.status)) return;
      let next = elapsed < task._start ? 'queued' : (elapsed < task._end ? 'running' : 'done');
      if (next === 'done' && task._fail && task.attempts === 1) next='failed';
      if(next === task.status) return;
      task.status=next; changed=true;
      task.queue_ahead = Math.max(0, index - job.tasks.filter(t => ['done','failed'].includes(t.status)).length - 4);
      if(next === 'running') task.started_at=(job._epoch+task._start)/1000;
      if(next === 'done') {
        task.thumb=task._sample.preview; task.preview=task._sample.preview; task.full=task._sample.full;
        task.out=`${task.garment_name.replace(/\s/g,'')}_${task.color_name}_${task.item}.jpg`;
        task.duration=((task._end-task._start)/1000).toFixed(1); task.version=`${job.id}-${task.id}-${task.attempts}`;
        task.finished_at=(job._epoch+task._end)/1000;
      }
      if(next === 'failed') {
        task.error_kind=index%2 ? 'rate_limit':'timeout';
        task.error=task.error_kind==='rate_limit' ? '接口限流，稍后重试':'响应超时，可以单独重试';
      }
    });
    if(changed) {
      job.log.push({t:now()/1000,level:job.tasks.some(t=>t.status==='failed')?'warn':'info',msg:`已回传 ${job.tasks.filter(t=>t.status==='done').length} / ${job.tasks.length} 张 · 单张结果立即可用`});
    }
  }
  function makeJob(picks, inputs, model, kind = '', sourceTasks = null) {
    const id = 'flow-' + (++sequence).toString().padStart(3,'0');
    const tasks = [];
    let index = 0;
    const workers=[900,1050,1150,1250];
    const appendTask = (pick,label,item,sampleTask) => {
      const type=bySlug[pick.garment_type];
      const color=type.colors.find(c=>c.label===label)||type.colors[0];
      const duration = scenario==='slow' ? 3200+(index*197%1700) : scenario==='stress' ? 420+(index*23%330) : 1000+(index*113%700);
      const worker=workers.indexOf(Math.min(...workers));
      const start=workers[worker]; workers[worker]=start+duration+100;
      const task={id:`${id}-t${index+1}`,item,view:'01',color:label,color_name:color.name_zh,color_hex:color.hex,garment:pick.garment_type,garment_name:type.display_name,status:'queued',attempts:1,error:'',error_kind:'',out:'',thumb:'',preview:'',full:color.full,source:sampleTask?.source||'本地演示素材',master:false,queue_ahead:Math.max(0,index-3),_sample:color,_start:start,_end:start+duration,_fail:scenario==='failure'&&[5,10].includes(index)};
      tasks.push(task); index++;
    };
    if(sourceTasks) sourceTasks.forEach(task=>appendTask({garment_type:task.garment},task.color,task.item,task));
    else inputs.forEach((name) => picks.forEach(pick=>pick.colors.forEach(label=>appendTask(pick,label,name.replace(/\.[^.]+$/,'')))));
    const unit=prices[model]??.045;
    const job={id,status:'queued',stage:'正在提交任务…',created_at:now()/1000,updated_at:now()/1000,progress:0,error:'',batch_name:'春夏色彩 · 模拟任务',has_zip:false,zip:'#download-'+id,params:{garment_type:picks[0]?.garment_type,garment_name:picks[0]?.garment_name,selections:copy(picks),provider:'demo-primary',model,size:'1536x2048',kind,estimate:{priced:true,count:tasks.length,unit,total:unit*tasks.length,currency:'CNY'},feishu_rows:0},tasks,log:[{t:now()/1000,level:'info',msg:`收到 ${tasks.length} 张任务，已建立占位，等待逐张回传。`}],_epoch:now(),_canceled:false};
    jobs.set(id,job); return getSummary(job);
  }
  function prepare(form) {
    const picks=JSON.parse(form.get('selections')||'[]');
    const inputs=form.getAll('files').map(file=>file.name);
    const job=makeJob(picks,inputs,form.get('model')||'gemini-3.1-flash-image-preview');
    Object.assign(job.params,{provider:form.get('provider'),size:form.get('size'),quality:form.get('quality'),subject:form.get('subject'),requirements:form.get('extra_requirements')||''});
    prepared=job; return copy(job);
  }
  function completedSeed() {
    const job=makeJob(selectionsFor(),['item-01.jpg','item-02.jpg'],'gemini-3.1-flash-image-preview');
    job._epoch=now()-120000; job.tasks.forEach(t=>t._fail=false);getSummary(job);job.params.feishu_rows=job.tasks.length;job.stage='上轮样图 · 可直接开始新一轮'; return job;
  }
  function sources(stress=false) {
    const count=stress?5:2;
    return Array.from({length:count},(_,i)=>{
      const type=bySlug[baseSlugs[i%baseSlugs.length]];
      const color=type.colors.find(c=>c.label==='white')||type.colors[0];
      const bytes=Uint8Array.from(atob(type.source_b64),char=>char.charCodeAt(0));
      const file=new File([bytes],`item-${String(i+1).padStart(2,'0')}.jpg`,{type:'image/jpeg'});
      return {file,url:color.preview,name:file.name,demo:true};
    });
  }
  function seed(api) {
    bridge=api;
    if(bridge.state.pollTimer)clearTimeout(bridge.state.pollTimer);
    bridge.state.files.filter(f=>!f.demo && f.url.startsWith('blob:')).forEach(f=>URL.revokeObjectURL(f.url));
    bridge.state.styles=selectionsFor(scenario==='stress').map(p=>({slug:p.garment_type,colors:new Set(p.colors)}));
    bridge.state.files=sources(scenario==='stress');
    const tier=document.getElementById('sizeTier'); if(tier)tier.value='2K';
    bridge.renderStyles();bridge.renderThumbs();
    let initial=[...jobs.values()].find(j=>j.params.feishu_rows);
    if(!initial)initial=completedSeed();
    bridge.watchJob(initial.id,copy(getSummary(initial)));
    document.body.classList.remove('booting');
    document.dispatchEvent(new CustomEvent('flow:seeded'));
  }
  function retry(job,form,limited) {
    getSummary(job); const ids=form?.getAll('task_ids')||[];
    const elapsed=now()-job._epoch; let n=0;const workers=[elapsed+200,elapsed+350,elapsed+500,elapsed+650];
    job._canceled=false;
    job.tasks.forEach(t=>{
      if(t.status!=='failed' && !(ids.length && ids.includes(t.id) && t.status==='canceled'))return;
      if(ids.length&&!ids.includes(t.id))return;if(limited&&t.error_kind!=='rate_limit')return;
      const worker=workers.indexOf(Math.min(...workers));
      t.status='queued';t.error='';t.error_kind='';t.attempts++;t._start=workers[worker];t._end=t._start+1300;workers[worker]=t._end+80;t._fail=false;n++;
    });
    job.log.push({t:now()/1000,level:'info',msg:`已重试 ${n} 张；已完成图片保留。`});
    return getSummary(job);
  }
  const promptText='【本地模拟提示词】\n保持原图主体、印花、背景与光线。按所选款式锁定版型、袖口与下摆工艺，仅改变面料底色。\n参考当前色卡，不新增人物，不移动原有图案，保留面料纹理与自然阴影。';
  function sse(payload) {
    const encoder=new TextEncoder();let canceled=false;
    return new Response(new ReadableStream({async start(controller){
      const emit=(event,data)=>{if(!canceled)controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));};
      await wait(350);emit('tool',{name:'inspect_images'});await wait(650);emit('result',{name:'inspect_images',text:'演示素材已读取。将保留图案、背景和主体，仅调整款式及面料色彩。'});
      const text=payload.mode==='rework' ? `已选中 ${payload.task_ids?.length||1} 张图片。建议保留版型和印花位置，按你的需求调整细节。确认后会创建独立的返工任务，原成品会保留。` : `这批素材可以分款式生成。会保留原图案与背景，并按照所选色卡控制底色。图片将逐张返回，已完成的图片可以提前查看、下载或改图。`;
      for(let i=0;i<text.length;i+=8){await wait(65);emit('delta',{text:text.slice(i,i+8)});}
      emit('prompt',{prompt:promptText+'\n本次需求：'+payload.instruction,requirements:payload.instruction||'保持图案和背景，仅更换面料底色。'});emit('done',{ok:true});if(!canceled)controller.close();
    },cancel(){canceled=true;}}),{headers:{'Content-Type':'text/event-stream'}});
  }
  const providers=[{name:'demo-primary',kind:'openai_compat',label:'主生图接口 · 模拟',model:'gemini-3.1-flash-image-preview',ready:true,max_images:200,max_concurrency:4,default_concurrency:4,unlimited:false,supports_negative:true,model_prices:prices},{name:'demo-backup',kind:'openai_compat',label:'备用生图接口 · 模拟',model:'seedream-4.5',ready:true,max_images:200,max_concurrency:4,default_concurrency:4,unlimited:false,supports_negative:true,model_prices:prices}];
  function usage() {const ready=[...jobs.values()].reduce((n,j)=>n+getSummary(j).stats.done,0);return {currency:'CNY',today:{images:ready,cost:ready*.045},total:{images:ready,cost:ready*.045},jobs:jobs.size,unpriced_images:0};}
  function records() {return [...jobs.values()].flatMap(j=>getSummary(j).tasks.filter(t=>t.out).slice(0,6).map(t=>({record_id:t.id,name:t.out,job_id:j.id,color:t.color_name,model:j.params.model,size:j.params.size,status:'已完成',amount:.045,time:j.created_at*1000,attachment_count:1,file_token:t.id,download_url:t.full})));}
  window.fetch=async(input,options={})=>{
    const raw=typeof input==='string'?input:input.url;
    const path=new URL(raw,raw.startsWith('/api/')?'https://flow.invalid':document.baseURI);
    if(!path.pathname.startsWith('/api/'))return nativeFetch(input,options);
    const route=decodeURIComponent(path.pathname);requests.push({path:route,method:options.method||'GET',at:performance.now()});
    const body=options.body;
    if(route==='/api/harness/stream')return sse(JSON.parse(body||'{}'));
    if(route==='/api/feishu/export'){await wait(650);const ids=JSON.parse(body).record_ids;return new Response(await zipBlob([...jobs.values()].flatMap(j=>j.tasks).filter(t=>ids.includes(t.id))),{headers:{'Content-Type':'application/zip'}});}
    if(route==='/api/jobs'&&options.method==='POST'){
      const job=prepared||makeJob(JSON.parse(body.get('selections')||'[]'),body.getAll('files').map(f=>f.name),body.get('model'));prepared=null;await wait(scenario==='slow'?1400:550);return response({ok:true,job:copy(getSummary(job))});
    }
    const jobMatch=/^\/api\/jobs\/([^/]+)(?:\/(.+))?$/.exec(route);
    if(jobMatch){
      const job=jobs.get(jobMatch[1]);if(!job)return response({ok:false,error:'演示任务不存在'},404);
      const action=jobMatch[2]||'';
      await wait(action?180:70);
      if(action==='cancel'){getSummary(job);job._canceled=true;job.tasks.forEach(t=>{if(['pending','queued','running'].includes(t.status))t.status='canceled';});return response({ok:true,job:copy(getSummary(job))});}
      if(action==='retry')return response({ok:true,job:copy(retry(job,body,body?.get('only')==='rate_limit'))});
      if(action==='feishu'){job.params.feishu_rows=job.tasks.filter(t=>t.out).length;return response({ok:true,rows:job.params.feishu_rows,table:'本地演示历史'});}
      if(action==='rework'){
        const ids=body.getAll('task_ids');const selected=job.tasks.filter(t=>ids.includes(t.id));const next=makeJob(job.params.selections,[],body.get('model'),'rework',selected);next.params.source_job=job.id;next.params.instruction=body.get('instruction');return response({ok:true,job:copy(next)});
      }
      if(!action)return response({ok:true,job:copy(getSummary(job))});
      return response({ok:false,error:'此二进制资源请使用演示下载入口'},404);
    }
    await wait(['/api/skill','/api/providers','/api/pricing'].includes(route)?170:90);
    if(route==='/api/session')return response({ok:true,authenticated:true,account:'demo',display:'演示账号',role:'admin',admin:true,source:'local'});
    if(route==='/api/logout')return response({ok:true});
    if(route==='/api/health')return response({ok:true,types:catalog.length,providers:2,providers_ui:2});
    if(route==='/api/skill')return response({ok:true,types:bySlug,negative_prompt:'不添加人物，不改变印花',max_bytes:20*1024*1024,warnings:[],hidden:[]});
    if(route==='/api/providers')return response({ok:true,providers,defaults:{size:'1536x2048',quality:'high',subject:'auto',output_format:'png'},ui:{}});
    if(route==='/api/models'){const models=Object.keys(prices).map(id=>({id,image:true,price:prices[id],currency:'CNY'}));return response({ok:true,current:providers.find(p=>p.name===path.searchParams.get('provider'))?.model,models,image_models:models,cached:true});}
    if(route==='/api/gate'){const stats=[...jobs.values()].map(j=>getSummary(j).stats);return response({ok:true,config:{enabled:true,provider:'feishu'},in_use:Math.min(4,stats.reduce((n,s)=>n+s.running,0)),limit:4,queued:stats.reduce((n,s)=>n+s.queued,0),ahead:0});}
    if(route==='/api/pricing')return response({ok:true,currency:'CNY',source:'local',models:Object.entries(prices).map(([model,unit])=>({model,unit,currency:'CNY',priced:true})),usage:usage()});
    if(route==='/api/jobs')return response({ok:true,jobs:[...jobs.values()].reverse().map(j=>{const item=copy(getSummary(j));delete item.tasks;delete item.log;return item;})});
    if(route==='/api/drafts')return response({ok:true,draft_id:'flow-local-draft',count:body.getAll('files').length});
    if(route==='/api/preview')return response({ok:true,single_pass:promptText+'\n需求：'+(JSON.parse(body||'{}').requirement||'按所选色卡生成'),negative:'禁止新增图案、人物或水印'});
    if(route==='/api/feishu/records')return response({ok:true,records:records(),table:'本地模拟历史',links:true});
    if(route==='/api/accounts'){const u=usage();return response({currency:'CNY',source:'local',usage_source:'local',accounts:[{username:'demo',display:'演示账号',active:true,role:'admin',images:u.today.images,cost:u.today.cost,jobs:u.jobs,quota:100,quota_left:100-u.today.cost,quota_pct:u.today.cost,last_at:now()/1000,recent:[{detail:'春夏系列 · 模拟创作',images:u.today.images,cost:u.today.cost}]},{username:'designer',display:'设计成员',active:true,role:'member',images:18,cost:.81,jobs:1}]});}
    return response({ok:false,error:'沙箱未实现的接口：'+route},404);
  };
  let downloadDataPromise;
  function loadDownloadData(){
    if(window.FLOW_DOWNLOAD_BYTES)return Promise.resolve(window.FLOW_DOWNLOAD_BYTES);
    if(!downloadDataPromise)downloadDataPromise=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='download-data.js';script.onload=()=>resolve(window.FLOW_DOWNLOAD_BYTES);script.onerror=()=>{downloadDataPromise=null;script.remove();reject(new Error('无法读取本地样图数据'));};document.head.appendChild(script);});
    return downloadDataPromise;
  }
  const enc=new TextEncoder();
  const crcTable=Array.from({length:256},(_,n)=>{for(let k=0;k<8;k++)n=n&1?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
  const crc32=bytes=>{let crc=0xffffffff;for(const b of bytes)crc=crcTable[(crc^b)&255]^(crc>>>8);return(crc^0xffffffff)>>>0;};
  async function zipBlob(tasks){
    const data=await loadDownloadData();
    const files=[{name:'README.txt',bytes:enc.encode('本地沙箱模拟导出。图片为款式库样图的轻量预览，不是真实生图结果。\n')},{name:'manifest.json',bytes:enc.encode(JSON.stringify(tasks.map(t=>({item:t.item,style:t.garment_name,color:t.color_name,status:t.status})),null,2))}];
    for(const task of tasks.filter(t=>t.out)){const encoded=data[task._sample?.preview||task.thumb];if(encoded)files.push({name:task.out,bytes:Uint8Array.from(atob(encoded),ch=>ch.charCodeAt(0))});}
    const chunks=[],directory=[];let offset=0;
    for(const file of files){const name=enc.encode(file.name),crc=crc32(file.bytes),header=new Uint8Array(30+name.length),view=new DataView(header.buffer);view.setUint32(0,0x04034b50,true);view.setUint16(4,20,true);view.setUint16(6,0x800,true);view.setUint32(14,crc,true);view.setUint32(18,file.bytes.length,true);view.setUint32(22,file.bytes.length,true);view.setUint16(26,name.length,true);header.set(name,30);chunks.push(header,file.bytes);const central=new Uint8Array(46+name.length),cv=new DataView(central.buffer);cv.setUint32(0,0x02014b50,true);cv.setUint16(4,20,true);cv.setUint16(6,20,true);cv.setUint16(8,0x800,true);cv.setUint32(16,crc,true);cv.setUint32(20,file.bytes.length,true);cv.setUint32(24,file.bytes.length,true);cv.setUint16(28,name.length,true);cv.setUint32(42,offset,true);central.set(name,46);directory.push(central);offset+=header.length+file.bytes.length;}
    const directorySize=directory.reduce((n,b)=>n+b.length,0),end=new Uint8Array(22),ev=new DataView(end.buffer);ev.setUint32(0,0x06054b50,true);ev.setUint16(8,files.length,true);ev.setUint16(10,files.length,true);ev.setUint32(12,directorySize,true);ev.setUint32(16,offset,true);return new Blob([...chunks,...directory,end],{type:'application/zip'});
  }
  async function exportJob(id,ids){
    const job=jobs.get(id);if(!job)throw new Error('未找到演示任务');getSummary(job);
    const tasks=job.tasks.filter(t=>t.out&&(!ids?.length||ids.includes(t.id)));
    const btn=document.getElementById(ids?.length?'batchExportBtn':'downloadBtn');const original=btn?.textContent;if(btn){btn.classList.add('loading');btn.setAttribute('aria-busy','true');}
    try{const blob=await zipBlob(tasks);const url=URL.createObjectURL(blob);resources.add(url);const link=document.createElement('a');link.href=url;link.download=`${id}-demo-${tasks.length}.zip`;link.click();setTimeout(()=>{URL.revokeObjectURL(url);resources.delete(url);},30000);window.FlowUI?.notify(`已导出 ${tasks.length} 张演示样图`);return blob;}finally{if(btn){btn.classList.remove('loading');btn.removeAttribute('aria-busy');}}
  }
  document.addEventListener('click',event=>{const link=event.target.closest('a[href]');if(!link)return;const hash=link.getAttribute('href');if(hash?.startsWith('#download-')){event.preventDefault();exportJob(hash.slice(10)).catch(err=>window.FlowUI?.notify(err.message,true));}},true);
  window.addEventListener('pagehide',()=>{resources.forEach(url=>URL.revokeObjectURL(url));});
  window.FlowDemo={seed,prepare,requests,bySlug,exportJob,zipBlob,getJob:id=>copy(getSummary(jobs.get(id))),setScenario(value){scenario=value;if(bridge)seed(bridge);},reset(){scenario=document.getElementById('demoScenario')?.value||'normal';if(bridge)seed(bridge);},imageUrl(id,taskId){return jobs.get(id)?.tasks.find(t=>t.id===taskId)?.full||'';},advance(ms){timeOffset+=ms;},get scenario(){return scenario;}};
})();
