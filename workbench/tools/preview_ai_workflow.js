/* Visual inspection only: production markup/CSS in an isolated frame, no app/API calls. */
window.showAIWorkflowVisual=async function(){
  document.getElementById('aiVisualPreview')?.remove();
  const html=await(await fetch('/',{cache:'no-store'})).text();
  const frame=document.createElement('iframe');frame.id='aiVisualPreview';frame.style.cssText='position:fixed;inset:0;width:100vw;height:100dvh;border:0;z-index:2147483647;background:#000';
  frame.srcdoc=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace('<head>',`<head><base href="${location.origin}/">`);
  const loaded=new Promise(resolve=>frame.onload=resolve);document.body.appendChild(frame);await loaded;
  const w=frame.contentWindow,d=w.document,$=id=>d.getElementById(id);
  d.body.classList.remove('booting');d.body.dataset.mobilePane='results';
  const panel=$('harnessDrawer');panel.hidden=false;$('harnessScrim').hidden=false;$('harnessScrim').classList.add('on');
  $('harnessTitle').textContent='AI 创作工作室';$('harnessSub').textContent='2 张原图 · 6 个款色 · 先对齐需求，再确认生成';
  $('chatLog').innerHTML='<div class="msg user"><span class="who">你</span><div class="bubble">保留原图的正反面构图和各自印花，统一改为白色面料。</div></div><div class="msg assistant tool-row"><div class="tool-line done"><span class="ok">✓</span><span>对照图片已返回</span><span class="chev">▾</span></div></div><div class="msg assistant"><span class="who">AI 工作室</span><div class="bubble streaming">我已对照原始上传图和当前成品。正面胸前小标与背面大图案会分别保留；本轮只调整面料底色，不合并视角，也不增加人物。</div></div>';
  const code=await(await fetch('/ai-workflow.js',{cache:'no-store'})).text();new w.Function(code)();
  w.AIWorkflow.start('intake');w.AIWorkflow.tool('inspect_images','running');w.AIWorkflow.tool('inspect_images','result','原图与成品已对照');w.AIWorkflow.stream('我已对照原始上传图和当前成品。正面胸前小标与背面大图案会分别保留；本轮只调整面料底色，不合并视角，也不增加人物。');
  w.AIWorkflow.phase('processing','正在把已确认内容整理成图像模型可执行的约束。');
  requestAnimationFrame(()=>panel.classList.add('on'));
  $('harnessClose').onclick=()=>frame.remove();
  $('harnessSend').disabled=true;$('harnessApply').disabled=true;$('harnessDefault').onclick=()=>frame.remove();
  window.aiVisualPreview={frame,close:()=>frame.remove(),phase:key=>w.AIWorkflow.phase(key)};
  return {preview:true,note:'受控视觉样例；没有AI请求或生成调用',width:w.innerWidth,height:w.innerHeight};
};
