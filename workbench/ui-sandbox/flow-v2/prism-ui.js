/* Flat card reveals and the avatar-anchored personal menu. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  let bridge, profileAnchor, profileTimer, revealCount=0, animatedCount=0;
  const menu = $('profileMenu');
  function setText(element,text){if(element&&element.textContent!==text)element.textContent=text;}
  function resetCard(entry){
    entry._revealEpoch=(entry._revealEpoch||0)+1;
    entry.flipAnimation?.cancel();entry.flipAnimation=null;
    entry.el.classList.remove('image-ready','flash-revealed','flash-flipping');
  }
  function updateCard(entry,task){
    const labels={pending:'等待生成',queued:'排队中',running:'正在生成',failed:'这张暂未完成',canceled:'已取消',skipped:'成品已就绪',done:'正在读取成品图'};
    const messages={pending:'已为这张成品保留位置',queued:Number(task.queue_ahead)>0?`前方还有 ${task.queue_ahead} 张，完成后自动开始`:'即将开始生成',running:'版型与色彩正在逐步显影',failed:'可单独重试，其他成品会保留',canceled:'已经完成的成品仍可查看',skipped:'点击查看完整图片',done:entry.el.classList.contains('image-ready')?'点击查看完整图片':'成品已返回，正在解码图片'};
    setText(entry.backText,labels[task.status]||'等待生成');
    setText(entry.backMessage,messages[task.status]||'');
    if(task.status==='done'&&entry.el.classList.contains('image-ready'))setText(entry.backText,'显影完成');
  }
  async function revealCard(entry,task){
    const epoch=(entry._revealEpoch||0)+1;entry._revealEpoch=epoch;
    entry.el.classList.add('image-ready');
    revealCount++;
    const turner=entry.shot.querySelector('.card-turner');
    const animate=!reduced()&&entry.el.classList.contains('in-viewport')&&turner?.animate&&!document.hidden;
    if(!animate){entry.el.classList.add('flash-revealed');updateCard(entry,task);return;}
    animatedCount++;entry.el.classList.add('flash-flipping');
    const fold=turner.animate([{transform:'scaleX(1)',opacity:1},{transform:'scaleX(.025)',opacity:.82}],{duration:210,easing:'cubic-bezier(.55,0,.9,.5)',fill:'forwards'});
    entry.flipAnimation=fold;
    try{await fold.finished;}catch(_){return;}
    if(!entry.el.isConnected||entry._revealEpoch!==epoch)return;
    entry.el.classList.add('flash-revealed');
    const unfold=turner.animate([{transform:'scaleX(.025)',opacity:.82},{transform:'scaleX(1)',opacity:1}],{duration:350,easing:'cubic-bezier(.12,.65,.18,1)',fill:'none'});
    entry.flipAnimation=unfold;fold.cancel();
    window.PrismFX?.reveal(entry.shot);
    try{await unfold.finished;}catch(_){return;}
    if(!entry.el.isConnected||entry._revealEpoch!==epoch)return;
    entry.el.classList.remove('flash-flipping');entry.flipAnimation=null;
    updateCard(entry,task);
    entry.el.dispatchEvent(new CustomEvent('prism:card-revealed',{bubbles:true,detail:{task:task.id}}));
  }
  function visibleAnchor(){
    const preferred=innerWidth>760?$('railAvatar'):$('mobileProfile');
    return preferred?.getClientRects().length?preferred:profileAnchor;
  }
  function positionProfile(){
    const anchor=visibleAnchor();if(!anchor||menu.hidden)return;
    const rect=anchor.getBoundingClientRect();
    const width=menu.offsetWidth,height=menu.offsetHeight;
    const desktop=innerWidth>760;
    let left=desktop?rect.right+15:rect.right-width;
    let top=desktop?rect.bottom-height:rect.bottom+12;
    if(!desktop&&top+height>innerHeight-12)top=rect.top-height-12;
    left=Math.max(12,Math.min(left,innerWidth-width-12));
    top=Math.max(12,Math.min(top,innerHeight-height-12));
    menu.style.left=`${left}px`;menu.style.top=`${top}px`;
    menu.style.transformOrigin=desktop?`0px ${Math.max(0,rect.top-top+rect.height/2)}px`:'90% 0%';
  }
  function closeProfile(restoreFocus=true){
    clearTimeout(profileTimer);
    if(menu.hidden)return;
    menu.classList.remove('on');
    document.querySelectorAll('.profile-trigger').forEach(el=>el.setAttribute('aria-expanded','false'));
    profileTimer=setTimeout(()=>{menu.hidden=true;},reduced()?0:180);
    if(restoreFocus)visibleAnchor()?.focus({preventScroll:true});
  }
  function toggleProfile(anchor){
    clearTimeout(profileTimer);
    if(!menu.hidden&&menu.classList.contains('on')){closeProfile();return;}
    profileAnchor=anchor||visibleAnchor();
    menu.hidden=false;positionProfile();
    document.querySelectorAll('.profile-trigger').forEach(el=>el.setAttribute('aria-expanded','true'));
    menu.classList.add('on');
    menu.querySelector('.profile-option:not([hidden])')?.focus({preventScroll:true});
    window.PrismFX?.invalidate();
  }
  function ready(api){
    bridge=api;
    $('mobileProfile').addEventListener('click',()=>toggleProfile($('mobileProfile')));
    $('profileClose').addEventListener('click',()=>closeProfile());
    menu.addEventListener('click',event=>{
      if(event.target.closest('.profile-option,#logoutBtn'))closeProfile(false);
    },true);
    document.addEventListener('pointerdown',event=>{
      if(!menu.hidden&&!menu.contains(event.target)&&!event.target.closest('.profile-trigger'))closeProfile(false);
    });
    document.addEventListener('focusin',event=>{
      if(!menu.hidden&&menu.classList.contains('on')&&!menu.contains(event.target)&&!event.target.closest('.profile-trigger'))closeProfile(false);
    });
    document.addEventListener('keydown',event=>{
      if(menu.hidden||!menu.classList.contains('on'))return;
      if(event.key==='Escape'){event.preventDefault();closeProfile();return;}
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
      event.preventDefault();
      const rows=[...menu.querySelectorAll('.profile-option:not([hidden]),#logoutBtn')];
      const current=rows.indexOf(document.activeElement);
      const next=event.key==='Home'?0:event.key==='End'?rows.length-1:event.key==='ArrowDown'?(current+1)%rows.length:(current-1+rows.length)%rows.length;
      rows[next]?.focus({preventScroll:true});
    });
    window.addEventListener('resize',()=>{if(!menu.hidden)positionProfile();},{passive:true});
    ['usageChip','accountsBtn'].forEach(id=>$(id).addEventListener('click',()=>{
      const heading=$('accountsDrawer').querySelector('.drawer-head .who b');
      setText(heading,id==='usageChip'?'用量与花费':'账号与成员');
    },true));
    $('settingsDetails').addEventListener('toggle',()=>{
      const open=$('settingsDetails').open;
      const summary=$('settingsDetails').querySelector('summary');
      summary.setAttribute('aria-label',`${open?'收起':'展开'}生成设定`);
      summary.querySelector('.settings-cta').firstChild.textContent=open?'收起参数 ':'调整参数 ';
      window.PrismFX?.invalidate();
    });
    $('lightbox').addEventListener('close',()=>{
      $('lightboxStage').setAttribute('aria-busy','false');
      window.PrismFX?.invalidate();
    });
  }
  function syncJob(){window.PrismFX?.invalidate();}
  window.PrismUI={resetCard,updateCard,revealCard,ready,syncJob,toggleProfile,closeProfile,stats:()=>({reveals:revealCount,animatedReveals:animatedCount,profileOpen:!menu.hidden&&menu.classList.contains('on')})};
})();
