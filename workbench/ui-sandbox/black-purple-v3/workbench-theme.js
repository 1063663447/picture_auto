/* Runs only in the sandbox clone; reuses its local mocked interactions. */
(() => {
  'use strict';
  const catalog=BlackPurplePalettes,root=document.documentElement;
  const initial=new URLSearchParams(location.search).get('palette')||root.dataset.black||'carbon';
  let current=catalog.apply(root,initial),paused=false;
  const effects=()=>({stars:false,particles:true,rain:false,intensity:70,paused});
  function configure(){window.PrismFX?.configure({palette:current.shader,...effects()});}
  function select(id,{url=true}={}){
    current=catalog.apply(root,id);configure();
    const pick=document.getElementById('blackPalette');if(pick)pick.value=current.id;
    const brand=document.querySelector('.brand small');if(brand)brand.textContent=current.en;
    document.title=current.number+' '+current.name+' · 主黑副紫沙箱';
    if(url){try{const target=new URL(location.href);target.searchParams.set('palette',current.id);history.replaceState(null,'',target);}catch(_){}}
    if(parent!==window)parent.postMessage({type:'blackpurple:changed',id:current.id},location.origin);
    return current;
  }
  function setPaused(value){paused=!!value;root.dataset.atmospherePaused=String(paused);configure();const b=document.getElementById('blackMotion');if(b){b.textContent=paused?'▶ 微光':'Ⅱ 微光';b.setAttribute('aria-pressed',String(paused));}}
  window.PrismAppearance={get palette(){return {...current,defaults:effects()};},get effects(){return effects();}};
  function ready(){
    const bar=document.querySelector('.flow-bar');
    const controls=document.createElement('div');controls.className='bp-switch';controls.innerHTML='<span class="bp-label">主黑副紫</span><select id="blackPalette" aria-label="切换黑紫色调"></select><button type="button" id="blackMotion" aria-pressed="false">Ⅱ 微光</button><a href="../black-purple-v3/index.html" target="_top">五套总览 ↗</a>';
    for(const p of catalog.palettes)controls.querySelector('select').add(new Option(p.number+' '+p.name,p.id));
    bar.insertBefore(controls,bar.querySelector('.scenario-tools'));
    controls.querySelector('select').addEventListener('change',event=>select(event.target.value));
    controls.querySelector('button').addEventListener('click',()=>setPaused(!paused));
    const home=document.querySelector('.rail .logo');if(home)home.href='../black-purple-v3/index.html';
    const link=document.querySelector('.demo-pill');if(link)link.title='本地沙箱 · 无真实生图调用';
    select(current.id,{url:false});setPaused(false);
    if(parent!==window)parent.postMessage({type:'blackpurple:ready',id:current.id},location.origin);
  }
  addEventListener('message',event=>{
    if(event.source!==parent||event.origin!==location.origin)return;
    if(event.data?.type==='blackpurple:select')select(event.data.id);
    if(event.data?.type==='blackpurple:pause')setPaused(event.data.paused);
    if(event.data?.type==='blackpurple:visibility')window.PrismFX?.setPreviewVisible?.(event.data.visible);
  });
  window.BlackPurpleDemo={select,setPaused,get current(){return current.id;}};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ready,{once:true});else ready();
})();
