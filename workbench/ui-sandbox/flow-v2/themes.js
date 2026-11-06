/* Three dark palettes and an independent atmosphere control, shared by all demos. */
(() => {
  'use strict';
  const palettes = [
    {id:'obsidian',name:'黑曜紫',en:'OBSIDIAN',number:'01',description:'纯黑背景，深紫毛玻璃，银紫微光。',mood:'纯黑 · 深紫玻璃 · 浮游微光',effect:'运动粒子与微光',colors:['#000000','#181020','#2b1c37','#9f78b8','#f2edf7'],shader:['#69407f','#d3b9e3','#a67ebc'],defaults:{stars:false,particles:true,rain:false,intensity:80,paused:false}},
    {id:'nocturne',name:'墨蓝银',en:'NOCTURNE',number:'02',description:'墨蓝与石墨叠层，钢银描边，低饱和蓝光。',mood:'清冷 · 都市 · 夜雨',effect:'银色细雨与远星',colors:['#070a10','#151d2a','#29374b','#8297b5','#e8eef5'],shader:['#415676','#b9c5d7','#6d829f'],defaults:{stars:true,particles:false,rain:true,intensity:65,paused:false}},
    {id:'garnet',name:'烟晶酒红',en:'GARNET',number:'03',description:'炭黑、深酒红与烟玫瑰，带一点温暖的金属感。',mood:'温润 · 时装 · 浮金',effect:'烟金粒子与微光',colors:['#0d080b','#22151c','#41232f','#b78a94','#f5eae7'],shader:['#764752','#d6b9ad','#ad7e87'],defaults:{stars:false,particles:true,rain:false,intensity:70,paused:false}}
  ];
  const root=document.documentElement;
  const storageKey='aurora.flow.palette-studio';
  let saved={};
  try{saved=JSON.parse(localStorage.getItem(storageKey)||'{}')||{};}catch(_){}
  if(saved.obsidianFinish!=='blackglass-1'){
    saved.effects={...saved.effects,obsidian:{...palettes[0].defaults}};
    saved.tone='obsidian';saved.obsidianFinish='blackglass-1';
    try{localStorage.setItem(storageKey,JSON.stringify(saved));}catch(_){}
  }
  const query=new URLSearchParams(location.search).get('theme');
  const valid=id=>palettes.some(p=>p.id===id);
  let current=palettes.find(p=>p.id===(valid(query)?query:'obsidian'));
  let effectState={...current.defaults,...saved.effects?.[current.id]};
  effectState.intensity=Math.max(10,Math.min(100,Number(effectState.intensity)||60));
  let panel,trigger,returnFocus;
  root.dataset.tone=current.id;
  root.dataset.atmospherePaused=String(!!effectState.paused);
  function persist(){
    saved.tone=current.id;saved.effects=saved.effects||{};saved.effects[current.id]={...effectState};
    try{localStorage.setItem(storageKey,JSON.stringify(saved));}catch(_){}
  }
  function configureFX(){window.PrismFX?.configure?.({palette:current.shader,...effectState});}
  function updateControls(){
    if(!panel)return;
    panel.querySelectorAll('[data-tone-choice]').forEach(button=>{const on=button.dataset.toneChoice===current.id;button.classList.toggle('selected',on);button.setAttribute('aria-pressed',String(on));});
    panel.querySelectorAll('[data-atmosphere]').forEach(input=>{input.checked=!!effectState[input.dataset.atmosphere];});
    const range=panel.querySelector('#atmosphereIntensity');range.value=effectState.intensity;
    panel.querySelector('output').textContent=effectState.intensity+'%';
    const pause=panel.querySelector('#atmospherePause');pause.setAttribute('aria-pressed',String(!!effectState.paused));pause.innerHTML=(effectState.paused?'▶ 继续氛围':'Ⅱ 暂停氛围');
    trigger.querySelector('.studio-tone-name').textContent=current.name;
    trigger.querySelector('.studio-swatch').style.background=current.colors[3];
    panel.querySelector('.studio-current-effect').textContent=current.effect;
  }
  function applyTone(id,options={}){
    const palette=palettes.find(p=>p.id===id);if(!palette)return;
    current=palette;effectState={...palette.defaults,...saved.effects?.[id]};
    root.dataset.tone=id;root.dataset.atmospherePaused=String(!!effectState.paused);
    updateControls();configureFX();
    if(options.persist!==false)persist();
    if(options.url!==false){try{const url=new URL(location.href);url.searchParams.set('theme',id);history.replaceState(null,'',url);}catch(_){}}
    document.dispatchEvent(new CustomEvent('prism:appearance',{detail:{tone:id,effects:{...effectState}}}));
  }
  function setEffects(update,options={}){
    effectState={...effectState,...update};effectState.intensity=Math.max(10,Math.min(100,Number(effectState.intensity)||80));
    root.dataset.atmospherePaused=String(!!effectState.paused);configureFX();updateControls();if(options.persist!==false)persist();
    document.dispatchEvent(new CustomEvent('prism:atmosphere',{detail:{...effectState}}));
  }
  function closePanel(restore=true){
    if(!panel||panel.hidden)return;panel.hidden=true;trigger.setAttribute('aria-expanded','false');
    if(restore)returnFocus?.focus({preventScroll:true});
  }
  function positionPanel(){
    if(!panel||panel.hidden)return;
    const rect=trigger.getBoundingClientRect();
    const width=Math.min(366,innerWidth-24),left=Math.min(Math.max(12,rect.right-width),innerWidth-width-12);
    panel.style.width=width+'px';panel.style.left=left+'px';panel.style.top=Math.min(rect.bottom+9,Math.max(12,innerHeight-270))+'px';
    panel.style.maxHeight=Math.max(140,innerHeight-parseFloat(panel.style.top)-12)+'px';
  }
  function togglePanel(){
    if(!panel.hidden){closePanel();return;}
    returnFocus=document.activeElement;panel.hidden=false;trigger.setAttribute('aria-expanded','true');positionPanel();
    panel.querySelector('[data-tone-choice].selected')?.focus({preventScroll:true});
  }
  function setup(){
    configureFX();
    const bar=document.querySelector('.flow-bar');if(!bar)return;
    const launch=document.createElement('div');launch.className='palette-launch';
    launch.innerHTML='<button class="studio-trigger" id="paletteStudioBtn" type="button" aria-expanded="false" aria-controls="paletteStudio" title="切换三套色调与星光、粒子、雨幕"><i class="studio-swatch"></i><span class="studio-tone-name"></span><svg width="14" height="14" viewBox="0 0 20 20" aria-hidden="true"><path d="m5 7 5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg></button><a class="palette-overview-link" href="tones.html" title="三套色调总览">色调总览 ↗</a>';
    bar.insertBefore(launch,bar.querySelector('.scenario-tools'));
    trigger=launch.querySelector('button');
    panel=document.createElement('section');panel.className='palette-studio';panel.id='paletteStudio';panel.hidden=true;panel.setAttribute('aria-label','色调与画面氛围');
    panel.innerHTML=`<header><div><small>PALETTE & ATMOSPHERE</small><b>色调与画面氛围</b></div><button type="button" class="studio-close" aria-label="关闭色调面板">×</button></header><div class="studio-palettes">${palettes.map(p=>`<button type="button" data-tone-choice="${p.id}" aria-pressed="false"><span class="studio-palette-strip">${p.colors.slice(0,4).map(c=>`<i style="background:${c}"></i>`).join('')}</span><b>${p.name}</b><small>${p.en}</small></button>`).join('')}</div><div class="studio-atmosphere-head"><b>画面动效</b><span class="studio-current-effect"></span></div><div class="studio-effect-toggles"><label><input type="checkbox" data-atmosphere="stars"/><span>✦ 星光</span></label><label><input type="checkbox" data-atmosphere="particles"/><span>∙ 粒子</span></label><label><input type="checkbox" data-atmosphere="rain"/><span>╱ 细雨</span></label></div><label class="studio-intensity" for="atmosphereIntensity"><span>氛围强度</span><input id="atmosphereIntensity" type="range" min="10" max="100" step="5"/><output for="atmosphereIntensity">60%</output></label><footer><span>切换色调保留当前任务</span><button type="button" id="atmospherePause" aria-pressed="false">Ⅱ 暂停氛围</button></footer>`;
    document.body.appendChild(panel);updateControls();
    trigger.addEventListener('click',togglePanel);
    panel.querySelector('.studio-close').addEventListener('click',()=>closePanel());
    panel.querySelectorAll('[data-tone-choice]').forEach(button=>button.addEventListener('click',()=>applyTone(button.dataset.toneChoice)));
    panel.querySelectorAll('[data-atmosphere]').forEach(input=>input.addEventListener('change',()=>setEffects({[input.dataset.atmosphere]:input.checked})));
    panel.querySelector('input[type="range"]').addEventListener('input',event=>setEffects({intensity:Number(event.target.value)}));
    panel.querySelector('#atmospherePause').addEventListener('click',()=>setEffects({paused:!effectState.paused}));
    document.addEventListener('pointerdown',event=>{if(!panel.hidden&&!panel.contains(event.target)&&!launch.contains(event.target))closePanel(false);});
    document.addEventListener('keydown',event=>{if(panel.hidden)return;if(event.key==='Escape'){event.preventDefault();closePanel();}else if(event.key==='Tab'){const buttons=[...panel.querySelectorAll('button,input')].filter(e=>e.getClientRects().length);const first=buttons[0],last=buttons.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
    addEventListener('resize',positionPanel,{passive:true});
    new MutationObserver(()=>{if(document.querySelector('.app')?.inert)closePanel(false);}).observe(document.querySelector('.app'),{attributes:true,attributeFilter:['inert']});
  }
  window.PrismAppearance={palettes,setTone:applyTone,setEffects,close:closePanel,get tone(){return current.id;},get effects(){return {...effectState};},get palette(){return {...current};}};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup,{once:true});else setup();
})();
