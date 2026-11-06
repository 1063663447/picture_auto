(() => {
  'use strict';
  const palettes=PrismAppearance.palettes;
  const entries={obsidian:'tone-01-obsidian.html',nocturne:'tone-02-nocturne.html',garnet:'tone-03-garnet.html'};
  const cards=document.getElementById('toneCards');
  const preview=document.getElementById('workbenchPreview');
  const wrap=preview.parentElement;
  let selected='obsidian',paused=false;
  let initialized=false,syncTimer=0;
  function inView(){const r=preview.getBoundingClientRect();return !document.hidden&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth;}
  function syncLabel(){
    try{
      const w=preview.contentWindow,stats=w.PrismFX?.stats();if(!stats)return;
      paused=!!stats.paused;
      const button=document.getElementById('previewMotion');button.textContent=paused?'▶ 继续氛围':'Ⅱ 暂停氛围';button.setAttribute('aria-pressed',String(paused));
      const status=document.getElementById('previewAnimationStatus');if(status)status.textContent=stats.reducedMotion?'系统已减弱动态效果':paused?'氛围已暂停':stats.previewVisible?'粒子与微光正在运行':'预览进入视口后恢复动效';
    }catch(_){}
  }
  function syncPreview(attempt=0){
    clearTimeout(syncTimer);
    try{
      const w=preview.contentWindow;
      if(!w.PrismAppearance||!w.PrismFX?.setPreviewVisible){if(attempt<12)syncTimer=setTimeout(()=>syncPreview(attempt+1),100);return;}
      if(!initialized){
        w.PrismAppearance.setTone(selected,{persist:false,url:false});
        w.PrismAppearance.setEffects({...w.PrismAppearance.palette.defaults},{persist:false});
        w.document.addEventListener('prism:atmosphere',syncLabel);initialized=true;
      }
      w.PrismFX.setPreviewVisible(inView());wrap.classList.add('loaded');syncLabel();
    }catch(_){preview.contentWindow?.postMessage({type:'prism:preview-visibility',visible:inView()},'*');}
  }
  cards.innerHTML=palettes.map(p=>`<article class="tone-card${p.id===selected?' selected':''}" data-edition="${p.id}" style="--card-base:${p.colors[0]};--card-panel:${p.colors[1]};--card-raised:${p.colors[2]};--card-accent:${p.colors[3]};--card-text:${p.colors[4]}"><header class="tone-card-title"><div><span>${p.number}</span><h2>${p.name}</h2></div><small>${p.en}</small></header><div class="mini-preview" aria-label="${p.name}界面色调示意"><div class="mini-workbench"><aside class="mini-config"><span class="mini-label"></span><div class="mini-upload"></div><div class="mini-style expanded"><span></span><div class="mini-swatches-board"><i></i><i></i><i></i></div></div><div class="mini-style"><span></span></div><div class="mini-style"><span></span></div><div class="mini-settings">生成设定 ↗</div></aside><div class="mini-canvas"><div class="mini-top">成品画廊<span>18 / 36</span></div><div class="mini-progress"><i></i></div><div class="mini-grid"><div class="mini-image"><img src="previews/23-sorona-tee--black.jpg" alt="黑色服装样图" width="440" height="560" loading="lazy"/></div><div class="mini-image mini-back"><b>✧</b><small>正在生成</small><em></em></div><div class="mini-image mini-back"><b>✦</b><small>等待显影</small><em></em></div></div><div class="mini-compose"><span></span><b>生成 ↗</b></div></div></div><div class="mini-atmosphere" aria-hidden="true">${Array.from({length:16},(_,i)=>`<i style="--x:${(i*37+11)%100}%;--y:${(i*29+7)%100}%;--duration:${(p.id==='nocturne'?1.7:3.5)+(i%5)*.7}s;--delay:-${i*.43}s"></i>`).join('')}</div></div><div class="tone-card-body"><p class="tone-mood">${p.mood}</p><p class="tone-description">${p.description}<br>默认氛围：${p.effect}。</p><div class="tone-palette" aria-label="背景、面板、层次、强调和文字颜色">${p.colors.map(c=>`<i style="background:${c}" title="${c}"></i>`).join('')}</div><div class="tone-hex">${p.colors.map(c=>`<span>${c.toUpperCase()}</span>`).join('')}</div><div class="tone-card-actions"><a href="${entries[p.id]}" target="_blank" rel="noopener">打开完整版本 ↗</a><button type="button" data-preview="${p.id}" aria-pressed="${p.id===selected}">在此预览 ↓</button></div></div></article>`).join('');
  function select(id){
    const p=palettes.find(p=>p.id===id);if(!p)return;
    selected=id;paused=false;
    cards.querySelectorAll('.tone-card').forEach(card=>card.classList.toggle('selected',card.dataset.edition===id));
    cards.querySelectorAll('[data-preview]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.preview===id)));
    document.getElementById('liveTitle').textContent=p.name+' · 完整界面';
    document.getElementById('liveOpen').href=entries[id];
    const pause=document.getElementById('previewMotion');pause.textContent='Ⅱ 暂停氛围';pause.setAttribute('aria-pressed','false');
    initialized=false;wrap.classList.remove('loaded');preview.src='index.html?theme='+id;
    document.getElementById('livePreview').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
  }
  cards.addEventListener('click',event=>{const button=event.target.closest('[data-preview]');if(button)select(button.dataset.preview);});
  preview.addEventListener('load',()=>{initialized=false;wrap.classList.add('loaded');syncPreview();});
  window.addEventListener('message',event=>{if(event.source===preview.contentWindow&&event.data?.type==='prism:renderer-ready')syncPreview();});
  const visibilityObserver=new IntersectionObserver(()=>syncPreview(),{threshold:[0,.03]});visibilityObserver.observe(preview);
  document.addEventListener('visibilitychange',()=>syncPreview());
  window.addEventListener('focus',()=>syncPreview());
  document.getElementById('previewMotion').addEventListener('click',()=>{
    try{const appearance=preview.contentWindow.PrismAppearance;if(!appearance)return;appearance.setEffects({paused:!appearance.effects.paused},{persist:false});syncLabel();}catch(_){}
  });
  syncPreview();
  window.ToneBoard={select,get selected(){return selected;}};
})();
