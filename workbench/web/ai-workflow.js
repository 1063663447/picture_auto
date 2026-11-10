/* Visual-only AI workflow. Business requests and submission locks remain in app.js. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const phases={
    preparing:{index:0,label:'正在准备素材',detail:'整理原图、款色和本轮设定，不会提前提交生图任务。'},
    connecting:{index:1,label:'正在连接 AI',detail:'请求已发送，等待服务端开始返回内容。'},
    idle:{index:0,label:'素材已就绪',detail:'说明需要修改的地方，发送后 AI 会开始对齐。'},
    processing:{index:1,label:'AI 正在对齐需求',detail:'流式整理你的要求；工具执行状态会实时显示。'},
    inspecting:{index:1,label:'正在对照图片',detail:'按原始上传图与当前成品的角色核对画面内容。'},
    optimizing:{index:1,label:'正在整理执行约束',detail:'保留已确认内容，提炼本轮需要改变的部分。'},
    ready:{index:2,label:'提示词已就绪',detail:'请核对对话结果，确认后才会调用生图接口。'},
    waiting:{index:2,label:'还有细节需要确认',detail:'在下方补充一句即可继续，本轮尚未提交生图。'},
    reworking:{index:3,label:'改图任务已提交',detail:'已按框选位置提交改图；出图后会在对话里返给你看，满意再确认这张基准。'},
    submitting:{index:3,label:'正在提交生图任务',detail:'等待服务器确认任务 ID，请勿重复操作。'},
    submitted:{index:4,label:'任务已创建',detail:'生图已进入真实队列，可前往画廊查看进度。'},
    error:{index:-1,label:'本轮处理需要留意',detail:'已保留当前需求和对话，不会自动重发付费请求。'},
    stale:{index:-1,label:'本轮设定已发生变化',detail:'为避免错用旧分析，请关闭后重新点击生成。'}
  };
  const toolLabels={inspect_images:'对照图片',list_image_models:'读取模型',estimate_cost:'核对费用',get_prompt:'检查提示词',set_requirements:'提炼约束',submit_generation:'提交生图',submit_rework:'提交返工',job_status:'读取进度'};
  let bridge,session=null,timer=0,renderTimer=0;
  const terminal=()=>session&&['idle','ready','waiting','reworking','submitted','error','stale'].includes(session.phase);
  function elapsed(){return session?Math.max(0,Math.floor(((session.endedAt||Date.now())-session.startedAt)/1000)):0;}
  function render(){
    clearTimeout(renderTimer);renderTimer=0;if(!session)return;
    const panel=$('harnessDrawer'),info=phases[session.phase];if(!panel||!info)return;
    panel.dataset.aiPhase=session.phase;panel.dataset.aiSubmitted=String(session.phase==='submitted');
    $('aiPhaseLabel').textContent=info.label;$('aiPhaseDetail').textContent=session.detail||info.detail;
    $('aiElapsed').textContent=`已用时 ${elapsed()}s`;
    $('aiCharacterCount').textContent=session.characters?`已接收 ${session.characters} 字`:'等待流式内容';
    $('aiToolCount').textContent=session.toolCalls?`已调用 ${session.toolCalls} 个工具`:'工具按需执行';
    $('aiSignal').textContent=session.phase==='submitted'?'任务 ID 已确认':terminal()?'本轮已暂停等待操作':session.characters?'正在接收流式内容':'等待服务端响应';
    $('aiProgress').setAttribute('aria-valuetext',info.label+'；'+(session.detail||info.detail));
    $('aiProgress').setAttribute('aria-busy',String(!terminal()));
    panel.querySelectorAll('[data-ai-step]').forEach((step,index)=>{
      const status=info.index<0?'idle':index<info.index?'done':index===info.index?'active':'idle';
      step.dataset.state=status;
      if(status==='active')step.setAttribute('aria-current','step');else step.removeAttribute('aria-current');
    });
    const view=$('aiViewResults');view.hidden=session.phase!=='submitted';
    $('harnessActions').dataset.submitted=String(session.phase==='submitted');
    $('aiReceipt').hidden=session.phase!=='submitted';
    $('aiReceiptId').textContent=session.jobId||'';
    const minimized=panel.hidden||!panel.classList.contains('on');
    const resume=$('aiResumeBtn');resume.hidden=!minimized||session.phase==='submitted';
    $('aiResumeLabel').textContent=session.phase==='ready'?'AI 结果已就绪 · 查看':session.phase==='waiting'?'AI 等待补充 · 查看':session.phase==='reworking'?'改图已提交 · 查看':session.phase==='error'?'AI 连接提示 · 查看':session.phase==='stale'?'AI 结果需更新 · 查看':'AI 整理中 · 查看';
    resume.dataset.busy=String(!terminal());
  }
  function queue(){if(!renderTimer)renderTimer=setTimeout(render,90);}
  function tick(){clearTimeout(timer);if(!session||terminal())return;render();timer=setTimeout(tick,1000);}
  function start(mode){
    clearTimeout(timer);session={mode,phase:'preparing',startedAt:Date.now(),endedAt:0,characters:0,toolCalls:0,jobId:'',detail:''};
    render();tick();
  }
  function phase(key,detail=''){
    if(!session||!phases[key])return;
    if(session.phase==='submitted'&&key!=='submitted')return;
    session.phase=key;session.detail=detail;
    session.endedAt=['idle','ready','waiting','reworking','submitted','error','stale'].includes(key)?Date.now():0;
    render();if(terminal())clearTimeout(timer);else tick();
  }
  function stream(value){
    if(!session||session.phase==='submitted')return;
    session.characters+=Array.from(String(value||'')).length;
    if(['preparing','connecting'].includes(session.phase))session.phase='processing';
    queue();
  }
  function tool(name,status,value=''){
    if(!session||session.phase==='submitted')return;
    if(status==='running'){
      session.toolCalls++;
      const key=name==='inspect_images'?'inspecting':['submit_generation','submit_rework'].includes(name)?'submitting':['set_requirements','get_prompt'].includes(name)?'optimizing':'processing';
      phase(key,`正在${toolLabels[name]||name}，以下工具记录来自服务端事件。`);
    }else{
      const failed=/^(失败|错误|工具.+执行失败|Error:)/i.test(String(value).trim());
      phase('processing',failed?`${toolLabels[name]||name}返回了提示，AI 将继续处理；请查看工具记录。`:`${toolLabels[name]||name}已返回，继续整理本轮需求。`);
    }
  }
  function submitted(jobId){if(!session)start('intake');session.jobId=String(jobId);phase('submitted');}
  function error(message){phase('error',message||phases.error.detail);}
  function bind(api){
    bridge=api;
    $('aiResumeBtn').addEventListener('click',()=>{bridge.open();render();});
    $('aiViewResults').addEventListener('click',()=>bridge.results());
    new MutationObserver(()=>queue()).observe($('harnessDrawer'),{attributes:true,attributeFilter:['hidden','class']});
    document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);}else if(session&&!terminal())tick();});
    window.addEventListener('pagehide',()=>{clearTimeout(timer);clearTimeout(renderTimer);});
  }
  window.AIWorkflow={bind,start,phase,stream,tool,submitted,error,
    refresh:render,snapshot:()=>session?{...session,elapsed:elapsed()}:null};
})();
