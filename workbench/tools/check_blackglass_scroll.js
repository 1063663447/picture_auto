/* Evaluate in a same-origin browser, then await runBlackglassScrollCheck().
   Bounded, disposable 240-card/80-style fixture using the current production HTML,
   CSS, Three, atmosphere and effects. App/UI scripts are NEVER executed; fetch is
   NEVER replaced. Only static GETs and data-URI images; no API/provider/Feishu calls.
   The frame covers the page briefly and is always removed. Not an FPS benchmark. */
window.runBlackglassScrollCheck = async function () {
  'use strict';
  const results=[],errors=[],samples={},check=(label,passed,detail)=>results.push({label,passed:!!passed,...(detail===undefined?{}:{detail})});
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const html=await fetch(new URL('/',location.href),{cache:'no-store',signal:AbortSignal.timeout(5000)}).then(r=>{if(!r.ok)throw Error('Static HTML: '+r.status);return r.text();});
  if(!html.includes('id="galleryScroll"'))throw Error('Workbench HTML required.');
  const frame=document.createElement('iframe');
  frame.title='Isolated Blackglass scroll check';
  frame.style.cssText='position:fixed;left:0;top:0;width:1440px;height:900px;border:0;z-index:2147483647;background:#000';
  let io,w,d;
  try {
    const loaded=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture load timed out')),5000);frame.onload=()=>{clearTimeout(timer);resolve();};frame.onerror=()=>{clearTimeout(timer);reject(Error('Fixture load failed'));};});
    frame.srcdoc=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace('<head>',`<head><base href="${location.origin}/">`);
    document.body.appendChild(frame);await loaded;
    w=frame.contentWindow;d=w.document;
    w.addEventListener('error',e=>errors.push(e.message));
    w.addEventListener('unhandledrejection',e=>errors.push(String(e.reason?.message||e.reason)));
    d.body.classList.remove('booting');d.body.dataset.mobilePane='results';
    const gallery=d.getElementById('galleryScroll'),grid=d.getElementById('gridImgs'),styles=d.getElementById('styleList');
    d.getElementById('emptyState').hidden=true;d.getElementById('filterEmpty').hidden=true;grid.hidden=false;
    const image='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="160" height="188"><rect width="160" height="188" fill="#ddd4e6"/><path d="M50 20h60l30 30-20 25-12-10v100H52V65L40 75 20 50z" fill="#78598c"/></svg>');
    grid.innerHTML=Array.from({length:240},(_,i)=>`<article class="cardimg ${i%5===0?'st-running':'st-done image-ready flash-revealed'}" data-fixture-card="${i}"><div class="shot flash-card" aria-disabled="false" tabindex="0"><div class="card-turner"><div class="card-back"><div class="back-emblem"><svg></svg></div><b class="back-status">Generating</b><div class="back-meter"><i></i></div></div><div class="card-front"><img src="${image}" loading="lazy" decoding="async" alt="Fixture ${i}"></div></div></div><div class="meta"><i class="dot"></i><b>Style ${i}</b><span class="garment">Fixture</span></div><div class="acts"><button class="btn" type="button">View</button></div></article>`).join('');
    styles.innerHTML=Array.from({length:80},(_,i)=>`<div class="style-block"><button type="button" class="style-summary" data-fixture-style="${i}"><span class="style-summary-name"><b>Scroll fixture ${i}</b><span class="mini-swatches"><i></i><i></i></span></span><span class="style-count">3 colors</span><span class="chevron">⌄</span></button></div>`).join('');
    const load=path=>new Promise((resolve,reject)=>{const script=d.createElement('script'),timer=setTimeout(()=>reject(Error('Static script timed out: '+path)),5000);script.src=new URL(path,location.origin).href;script.onload=()=>{clearTimeout(timer);resolve();};script.onerror=()=>{clearTimeout(timer);reject(Error('Static script failed: '+path));};d.body.appendChild(script);});
    await load('/blackglass/vendor/three-0.160.1.min.js');await load('/blackglass/atmosphere.js');await load('/blackglass/effects.js');
    const fx=w.PrismFX;if(!fx?.active)throw Error('WebGL unavailable: '+JSON.stringify(fx?.stats()));
    // Same membership contract as blackglass-ui.js, without loading its app hooks.
    io=new w.IntersectionObserver(rows=>{rows.forEach(row=>row.target.classList.toggle('in-viewport',row.isIntersecting));fx.invalidate();},{root:gallery,rootMargin:'0px'});
    grid.querySelectorAll('.cardimg').forEach(card=>io.observe(card));
    await sleep(350);
    const nextFrame=()=>new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(Error('Fixture animation frame timed out')),2000);w.requestAnimationFrame(time=>{clearTimeout(timeout);resolve(time);});});
    const delta=(a,b)=>Object.fromEntries(Object.keys(b.geometry).map(key=>[key,b.geometry[key]-a.geometry[key]]));
    const scroll=async (element,steps,amount)=>{
      const before=fx.stats(),times=[];let previous=await nextFrame();
      for(let i=0;i<steps;i++){
        element.scrollTop+=amount;
        // Deliberate event bursts verify coalescing, not synthetic wheel speed/FPS.
        for(let j=0;j<6;j++)element.dispatchEvent(new w.Event('scroll'));
        const now=await nextFrame();times.push(now-previous);previous=now;
      }
      await sleep(80);const after=fx.stats();
      return {before,after,delta:delta(before,after),draws:after.draws-before.draws,frameIntervals:times};
    };
    const initial=fx.stats(),firstPixels=fx.samplePixels();
    check('64 particles retained',initial.atmosphere.particles===64,initial.atmosphere);
    check('Panel glass blur is retained on backing layer',w.getComputedStyle(d.querySelector('.workspace > .controls'),'::before').backdropFilter.includes('blur(12px)'));
    check('No blanket promotion of image cards',w.getComputedStyle(grid.firstElementChild).willChange==='auto');
    check('Offscreen skipping is on aspect-sized shots, not estimated card boxes',w.getComputedStyle(grid.firstElementChild).contentVisibility==='visible'&&w.getComputedStyle(grid.querySelector('.shot')).contentVisibility==='auto');
    const styleBefore=styles.scrollTop,initialHeight=gallery.scrollHeight;
    samples.gallery=await scroll(gallery,36,11);
    check('Gallery scroll does not move styles',styles.scrollTop===styleBefore&&gallery.scrollTop>0);
    check('Gallery scrolling does not remeasure fixed panels',samples.gallery.delta.panelReads===0,samples.gallery.delta);
    check('Scroll events are coalesced into render batches',samples.gallery.delta.scrollEvents>samples.gallery.delta.scrollBatches&&samples.gallery.delta.geometryBatches<=samples.gallery.draws+1,samples.gallery.delta);
    check('Card geometry is bounded by eight per membership refresh',samples.gallery.delta.cardReads<=samples.gallery.delta.cardQueries*8,samples.gallery.delta);
    check('No estimated-height grid shifts while scrolling',Math.abs(gallery.scrollHeight-initialHeight)<=1,{before:initialHeight,after:gallery.scrollHeight});
    const styleRect=styles.getBoundingClientRect(),x=styleRect.left+styleRect.width*.5,y=styleRect.top+30;
    const firstHit=d.elementFromPoint(x,y);firstHit.dispatchEvent(new w.PointerEvent('pointermove',{bubbles:true,clientX:x,clientY:y}));
    firstHit.dispatchEvent(new w.PointerEvent('pointerdown',{bubbles:true,clientX:x,clientY:y,button:0}));
    firstHit.closest('button')?.focus({preventScroll:true});
    await sleep(300);
    const galleryBefore=gallery.scrollTop;
    samples.styles=await scroll(styles,24,5);
    check('Style scroll does not move gallery',gallery.scrollTop===galleryBefore&&styles.scrollTop>0);
    check('Style scroll does not query or measure gallery cards/panels',samples.styles.delta.cardQueries===0&&samples.styles.delta.cardReads===0&&samples.styles.delta.panelReads===0,samples.styles.delta);
    const selector='button:not(:disabled),a[href],summary,.upload,.shot[aria-disabled="false"],.style-summary';
    const hit=d.elementFromPoint(x,y)?.closest(selector),hover=fx.stats();
    if(hit){const r=hit.getBoundingClientRect();check('Clicked stationary-pointer hover follows scrolled content',hover.hover&&Math.abs(hover.hoverRect.x-r.left-r.width/2)<1&&Math.abs(hover.hoverRect.y-r.top-r.height/2)<1,{actual:hover.hoverRect,target:hit.textContent.trim()});}
    else check('Clicked stationary-pointer hover clears over a gap',!hover.hover);
    const keyboard=styles.querySelector('[data-fixture-style="3"]');
    keyboard.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Tab',bubbles:true}));keyboard.focus({preventScroll:true});
    styles.scrollTop+=9;await sleep(100);
    const keyboardRect=keyboard.getBoundingClientRect(),keyboardHover=fx.stats();
    check('Keyboard focus edge is not stolen by the stationary pointer',keyboardHover.hover&&Math.abs(keyboardHover.hoverRect.y-keyboardRect.top-keyboardRect.height/2)<1);
    d.dispatchEvent(new w.PointerEvent('pointerleave'));keyboard.blur();
    check('Atmosphere keeps moving during independent scrolling',fx.stats().atmosphere.clock>initial.atmosphere.clock&&fx.stats().atmosphere.particles===64);
    const lastPixels=fx.samplePixels();
    check('Moving plane/particles still draw nonempty pixels',firstPixels.coloredPixels>0&&lastPixels.coloredPixels>0&&firstPixels.hash!==lastPixels.hash,{first:firstPixels,last:lastPixels});
    fx.setPreviewVisible(false);const hidden=fx.stats();await sleep(100);
    check('Hidden preview still suspends rendering',fx.stats().draws===hidden.draws);
    fx.setPreviewVisible(true);await sleep(100);
    check('Visible preview resumes all 64 particles',fx.stats().draws>hidden.draws&&fx.stats().atmosphere.particles===64);
    const resizeBefore=fx.stats();
    for(let i=0;i<20;i++)w.dispatchEvent(new w.Event('resize'));
    await sleep(100);const resizeAfter=fx.stats();
    check('Unchanged resize notifications do not reset render buffers',resizeAfter.geometry.bufferResizes===resizeBefore.geometry.bufferResizes,delta(resizeBefore,resizeAfter));
    gallery.scrollTop=0;styles.scrollTop=0;await sleep(150);
    const shot=grid.querySelector('.shot'),card=shot.closest('.cardimg'),turner=shot.querySelector('.card-turner');
    card.classList.add('flash-flipping','flash-revealed');
    const flip=turner.animate([{transform:'scaleX(.025)'},{transform:'scaleX(1)'}],{duration:160,easing:'ease-out'});
    const arrivalBefore=fx.stats().geometry.arrivalReads;
    for(let i=0;i<8;i++)fx.reveal(shot);
    check('Arrival bursts do not synchronously measure layout',fx.stats().geometry.arrivalReads===arrivalBefore);
    fx.burst(shot.getBoundingClientRect().left+20,shot.getBoundingClientRect().top+20);
    check('Flip transform promotion is scoped to the active flip',w.getComputedStyle(turner).willChange==='transform');
    await Promise.race([flip.finished,sleep(2000).then(()=>{throw Error('Flip timed out');})]);card.classList.remove('flash-flipping');
    check('Arrival geometry batch is bounded to the four visible ripple slots',fx.stats().geometry.arrivalReads-arrivalBefore===4);
    check('Flip promotion is released after animation',w.getComputedStyle(turner).willChange==='auto');
    const menu=d.querySelector('.more-actions');menu.open=true;await sleep(50);
    const menuRect=menu.querySelector('div').getBoundingClientRect(),menuHit=d.elementFromPoint(menuRect.left+menuRect.width/2,menuRect.top+menuRect.height/2);
    check('Composer menu remains visible and hit-testable',menuRect.width>0&&menu.contains(menuHit));menu.open=false;
    for(const width of [1440,1024,390,360]){
      frame.style.width=width+'px';d.body.dataset.mobilePane='results';fx.invalidate();await sleep(180);
      const left=d.querySelector('.workspace > .controls').getBoundingClientRect(),right=d.querySelector('.workspace > .canvas').getBoundingClientRect();
      check('No horizontal page overflow at '+width,d.documentElement.scrollWidth<=width+1,{width,scrollWidth:d.documentElement.scrollWidth});
      if(width>760)check('Panel alignment retained at '+width,Math.abs(left.top-right.top)<1&&Math.abs(left.bottom-right.bottom)<1,{leftTop:left.top,rightTop:right.top,leftBottom:left.bottom,rightBottom:right.bottom});
      else{
        check('Mobile gallery/composer remain bounded at '+width,gallery.getBoundingClientRect().height>40&&d.querySelector('.composer').getBoundingClientRect().bottom<=900);
        check('Mobile glass blur retained at '+width,w.getComputedStyle(d.querySelector('.workspace > .canvas'),'::before').backdropFilter.includes('blur(8px)'));
        d.body.dataset.mobilePane='config';fx.invalidate();await sleep(80);
        check('Mobile style pane keeps independent scrolling at '+width,styles.getBoundingClientRect().height>40&&styles.scrollHeight>styles.clientHeight);
      }
    }
    frame.style.width='1440px';d.body.dataset.mobilePane='results';await sleep(180);
    for(const compact of [false,true]){
      grid.classList.toggle('compact',compact);gallery.scrollTop=0;fx.invalidate();await sleep(120);
      const height=gallery.scrollHeight;gallery.scrollTop=height;await sleep(150);
      check('Grid height stable at far scroll ('+(compact?'compact':'normal')+')',Math.abs(height-gallery.scrollHeight)<=1,{before:height,after:gallery.scrollHeight});
    }
    check('No runtime errors',errors.length===0,errors);
  } catch(error){check('Runner completed',false,error.stack||String(error));}
  finally {io?.disconnect();w?.PrismFX?.dispose();frame.remove();}
  const report={passed:results.filter(r=>r.passed).length,failed:results.filter(r=>!r.passed).length,results,samples,errors,liveWrites:0};
  window.blackglassScrollReport=report;return report;
};