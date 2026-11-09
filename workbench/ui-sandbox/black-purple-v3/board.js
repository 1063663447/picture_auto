(() => {
  'use strict';
  const {$,catalog}= { $:id=>document.getElementById(id),catalog:BlackPurplePalettes};
  const names={carbon:'01-carbon.html',smoke:'02-smoke.html',graphite:'03-graphite.html',plum:'04-plum.html',ultraviolet:'05-ultraviolet.html'};
  const frame=$('workbenchFrame'),well=$('previewWell'),viewport=$('previewViewport');
  let selected=catalog.get(new URLSearchParams(location.search).get('palette')).id,paused=false,width=1440,visible=false;
  const mini=p=>`<div class="mini" aria-hidden="true"><aside class="mini-left"><span class="mini-line"></span><div class="mini-upload"></div><div class="mini-expanded"></div><div class="mini-closed"></div><div class="mini-closed"></div><div class="mini-settings"></div></aside><div class="mini-right"><div class="mini-tabs">成品画廊<span>18 / 36</span></div><div class="mini-progress"><i></i></div><div class="mini-images"><div class="mini-img"><img src="../flow-v2/previews/06-hoodie--black.jpg" width="160" height="200" alt="" loading="lazy"/></div><div class="mini-img mini-back"><b>✧</b><small>等待显影</small></div></div><div class="mini-compose"><span></span><i></i></div></div></div>`;
  $('paletteGrid').innerHTML=catalog.palettes.map(p=>`<article class="palette-card${p.id===selected?' selected':''}" data-palette="${p.id}" style="--p-panel:${p.panel};--p-raised:${p.raised};--p-card:${p.card};--p-accent:${p.accent};--p-edge:${p.edge};--p-button:${p.buttonEnd};--p-radius:${Math.max(3,p.radius*.33)}px"><button type="button" class="card-select" data-select="${p.id}" aria-label="预览 ${p.number} ${p.name}" aria-pressed="${p.id===selected}"><div class="card-head"><span class="number">${p.number} / BLACK—PURPLE</span><span class="check" aria-hidden="true">✓</span></div>${mini(p)}<div class="card-body"><h2>${p.name}</h2><span class="card-en">${p.en}</span><p class="mood">${p.mood}</p><p class="card-description">${p.description}</p><div class="swatches" aria-label="黑色背景、面板、层次与紫色强调"><i style="background:#000"></i><i style="background:${p.panel}"></i><i style="background:${p.raised}"></i><i style="background:${p.accent}"></i></div><div class="weight"><span>黑 / 紫 · 视觉方向</span><strong>${p.ratio}</strong></div></div></button><div class="card-footer"><span class="tag">${p.tag}</span><button type="button" data-select="${p.id}">完整预览 ↓</button><a href="${names[p.id]}" target="_blank" rel="noopener" aria-label="独立打开 ${p.name}">↗</a></div></article>`).join('');
  function send(type,data={}){frame.contentWindow?.postMessage({type,...data},location.origin);}
  function select(id,{scroll=false,notify=true}={}){
    const p=catalog.apply(document.documentElement,id);selected=p.id;
    $('liveTitle').textContent=p.number+' '+p.name;$('liveDescription').textContent=p.description;$('selectedLabel').textContent=p.en;
    $('standalone').href=names[p.id];frame.title=p.name+'完整工作台';
    document.querySelectorAll('[data-palette]').forEach(card=>card.classList.toggle('selected',card.dataset.palette===p.id));
    document.querySelectorAll('.card-select').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.select===p.id)));
    if(notify)send('blackpurple:select',{id:p.id});
    try{const url=new URL(location.href);url.searchParams.set('palette',p.id);history.replaceState(null,'',url);}catch(_){}
    if(scroll)$('live').scrollIntoView({behavior:'instant',block:'start'});
  }
  function fit(){
    const padding=innerWidth<=760?20:44,available=Math.max(240,well.clientWidth-padding-2),height=width===390?800:900,scale=Math.min(available/width,1);
    frame.style.width=width+'px';frame.style.height=height+'px';frame.style.transform=`scale(${scale})`;
    viewport.style.width=Math.ceil(width*scale)+'px';viewport.style.height=Math.ceil(height*scale)+'px';
  }
  function syncVisibility(){const r=well.getBoundingClientRect();visible=!document.hidden&&r.bottom>0&&r.top<innerHeight;send('blackpurple:visibility',{visible});}
  function setSize(value){width=Number(value);document.querySelectorAll('[data-size]').forEach(button=>{const on=Number(button.dataset.size)===width;button.classList.toggle('active',on);button.setAttribute('aria-pressed',String(on));});fit();syncVisibility();}
  document.addEventListener('click',event=>{const target=event.target.closest('[data-select]');if(target)select(target.dataset.select,{scroll:true});const size=event.target.closest('[data-size]');if(size)setSize(size.dataset.size);});
  $('pauseMotion').addEventListener('click',()=>{paused=!paused;send('blackpurple:pause',{paused});$('pauseMotion').textContent=paused?'▶ 恢复微光':'Ⅱ 暂停微光';$('pauseMotion').setAttribute('aria-pressed',String(paused));});
  addEventListener('message',event=>{
    if(event.source!==frame.contentWindow||event.origin!==location.origin)return;
    if(event.data?.type==='blackpurple:ready'){select(selected);send('blackpurple:pause',{paused});well.classList.add('loaded');fit();syncVisibility();}
    if(event.data?.type==='blackpurple:changed'&&event.data.id!==selected)select(event.data.id,{notify:false});
    if(event.data?.type==='prism:renderer-ready'){well.classList.add('loaded');syncVisibility();}
  });
  frame.addEventListener('load',()=>{fit();select(selected);send('blackpurple:pause',{paused});syncVisibility();});
  const resize=new ResizeObserver(fit);resize.observe(well);
  const intersection=new IntersectionObserver(syncVisibility,{threshold:[0,.01]});intersection.observe(well);
  document.addEventListener('visibilitychange',syncVisibility);addEventListener('focus',syncVisibility);
  window.BlackPurpleBoard={select,setSize,get selected(){return selected;},get width(){return width;}};
  select(selected,{notify:false});fit();syncVisibility();
})();
