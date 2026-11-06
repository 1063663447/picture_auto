/* A single Three.js screen-space plane: aurora silk, foil, hover edges and ripples. */
(() => {
  'use strict';
  const canvas = document.getElementById('prismCanvas');
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  const start = performance.now();
  let renderer;
  try {
    if (!window.THREE || !canvas) throw new Error('Three.js unavailable');
    renderer = new THREE.WebGLRenderer({canvas, alpha:true, antialias:false, depth:false, stencil:false, powerPreference:'low-power'});
  } catch (error) {
    document.documentElement.dataset.prismRenderer = 'css-fallback';
    window.PrismFX = {active:false, invalidate(){}, reveal(){}, burst(){}, configure(){}, dispose(){}, stats:()=>({active:false,reason:error.message})};
    return;
  }

  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1,1,1,-1,0,1);
  const clicks = Array.from({length:6}, () => new THREE.Vector4(-1000,-1000,-100,0));
  const arrivals = Array.from({length:4}, () => new THREE.Vector4(-1000,-1000,-100,0));
  const cards = Array.from({length:8}, () => new THREE.Vector4(0,0,0,0));
  const panels = Array.from({length:3}, () => new THREE.Vector4(0,0,0,0));
  const uniforms = {
    uResolution:{value:new THREE.Vector2(innerWidth,innerHeight)},
    uTime:{value:0}, uMotion:{value:motion.matches?0:1},
    uAmbienceTime:{value:0},uViolet:{value:new THREE.Color('#644279')},uPearl:{value:new THREE.Color('#c5b2d4')},uRose:{value:new THREE.Color('#946f9f')},
    uPointer:{value:new THREE.Vector2(-1000,-1000)},
    uHover:{value:new THREE.Vector4(0,0,0,0)},
    uHoverStrength:{value:0}, uCardCount:{value:0}, uPanelCount:{value:0},
    uClicks:{value:clicks}, uArrivals:{value:arrivals}, uCards:{value:cards}, uPanels:{value:panels}
  };
  const material = new THREE.ShaderMaterial({
    transparent:true, depthTest:false, depthWrite:false,
    uniforms,
    vertexShader:`varying vec2 vUv;
      void main(){vUv=uv;gl_Position=vec4(position.xy,0.0,1.0);}`,
    fragmentShader:`precision highp float;
      varying vec2 vUv;
      uniform vec2 uResolution;
      uniform float uTime,uAmbienceTime,uMotion,uHoverStrength,uCardCount,uPanelCount;
      uniform vec3 uViolet,uPearl,uRose;
      uniform vec2 uPointer;
      uniform vec4 uHover,uClicks[6],uArrivals[4],uCards[8],uPanels[3];
      float boxDistance(vec2 p,vec2 b,float r){vec2 q=abs(p)-b+r;return length(max(q,0.0))+min(max(q.x,q.y),0.0)-r;}
      void main(){
        vec2 p=vec2(vUv.x,1.0-vUv.y)*uResolution;
        vec2 uv=p/uResolution;
        float t=uAmbienceTime*uMotion;
        vec3 violet=uViolet,pearl=uPearl,rose=uRose;
        vec3 light=vec3(0.0);
        float a=uv.y-.20-.065*sin(uv.x*6.0-t*.13)-.038*sin(uv.x*13.0+t*.08);
        float b=uv.y-.91+.11*sin(uv.x*5.0+t*.10);
        float band=exp(-a*a*78.0)*(.6+.4*sin(uv.x*5.0+t*.12));
        light+=violet*band*.16;
        light+=pearl*exp(-b*b*100.0)*.09;
        float thread=exp(-abs(a-.02*sin(uv.x*26.0+t*.18))*330.0);
        light+=mix(violet,pearl,uv.x)*thread*.12;
        float panelCover=0.0;vec3 panelLight=vec3(0.0);
        for(int i=0;i<3;i++){
          if(float(i)<uPanelCount){
            vec4 r=uPanels[i];vec2 q=p-r.xy;
            float d=boxDistance(q,r.zw*.5-vec2(1.0),17.0);
            panelCover=max(panelCover,1.0-smoothstep(-2.0,5.0,d));
            float angle=atan(q.y/max(r.w,1.0),q.x/max(r.z,1.0));
            float orbit=pow(.5+.5*cos(angle-t*.6+float(i)*1.8),14.0);
            float edge=exp(-abs(d)*.9),bloom=exp(-abs(d)*.10);
            panelLight+=mix(violet,pearl,.7)*(edge*(.035+orbit*.42)+bloom*orbit*.025);
          }
        }
        light*=1.0-panelCover*.74;light+=panelLight;
        vec2 pointer=p-uPointer;
        float halo=exp(-dot(pointer,pointer)/24000.0);
        light+=mix(violet,pearl,uv.x)*halo*.065*uMotion;
        if(uHoverStrength>.001){
          float d=boxDistance(p-uHover.xy,uHover.zw*.5+vec2(2.0),10.0);
          float edge=exp(-abs(d)*.65)+exp(-abs(d)*.08)*.13;
          float sweep=.48+.52*sin((p.x+p.y)*.017-t*1.9);
          light+=mix(violet,pearl,.5+.5*sin(t*.7+uv.x*4.0))*edge*sweep*.24*uHoverStrength;
        }
        for(int i=0;i<8;i++){
          if(float(i)<uCardCount){
            vec4 r=uCards[i];vec2 local=p-r.xy;
            if(abs(local.x)<r.z*.5-8.0&&abs(local.y)<r.w*.5-8.0){
              float wave=.5+.5*sin(local.x*.018+local.y*.012-t*2.1+float(i)*.7);
              float foil=pow(wave,12.0);
              float center=exp(-dot(local/r.zw,local/r.zw)*6.0);
              light+=mix(violet,pearl,.5+.5*sin(local.x*.009+t*.6))*foil*.09;
              light+=rose*center*(.025+.018*sin(t*2.7+float(i)));
            }
          }
        }
        for(int i=0;i<6;i++){
          vec4 ripple=uClicks[i];float age=uTime-ripple.z;
          if(age>0.0&&age<1.25){
            float radius=age*135.0+5.0;
            float dist=length(p-ripple.xy);
            float ring=exp(-abs(dist-radius)*.28)*pow(1.0-age/1.25,2.0);
            float core=exp(-dist*dist/2000.0)*exp(-age*7.0);
            light+=mix(violet,pearl,ripple.w)*(ring*.34+core*.17)*uMotion;
          }
        }
        for(int i=0;i<4;i++){
          vec4 hit=uArrivals[i];float age=uTime-hit.z;
          if(age>0.0&&age<1.15){
            vec2 q=p-hit.xy;float dist=length(q),fade=pow(1.0-age/1.15,2.0);
            float ring=exp(-abs(dist-age*170.0)*.12)*fade;
            float star=exp(-min(abs(q.x+q.y),abs(q.x-q.y))*.8)*exp(-dist*.017)*exp(-age*5.0);
            light+=(pearl*.45+rose*.3)*(ring*.42+star*.28)*uMotion;
          }
        }
        float peak=max(light.r,max(light.g,light.b));
        gl_FragColor=vec4(light/max(peak,.001),min(peak,.58));
      }`
  });
  const geometry = new THREE.PlaneGeometry(2,2);
  scene.add(new THREE.Mesh(geometry,material));
  const atmosphere=window.PrismAtmosphere.create(THREE,scene,uniforms);
  renderer.setClearColor(0x000000,0);
  let frameId=0, dirty=true, resizing=true, disposed=false, contextLost=false;
  let pointerX=-1000, pointerY=-1000, hoverTarget=null, hoverLevel=0;
  const pointerTarget=new THREE.Vector2(-1000,-1000);
  let activeUntil=0, lastDraw=-100, draws=0, slowFrames=0, quality=.55, lastFrame=0, frameStrain=0;
  let clickIndex=0, arrivalIndex=0;
  let paused=false,ambientClock=0,ambientLast=0,previewVisible=true;
  const seconds = () => (performance.now()-start)/1000;
  const listeners=[];
  function listen(target,name,handler,options){target.addEventListener(name,handler,options);listeners.push(()=>target.removeEventListener(name,handler,options));}
  function invalidate(){dirty=true;wake();}
  function wake(){if(!frameId&&!disposed&&!contextLost&&!document.hidden&&previewVisible)frameId=requestAnimationFrame(frame);}
  function resize(){
    renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.4)*quality);
    renderer.setSize(innerWidth,innerHeight,false);
    uniforms.uResolution.value.set(innerWidth,innerHeight);
    resizing=false;dirty=true;
  }
  function updateRects(){
    if(hoverTarget?.isConnected&&hoverTarget.getClientRects().length){
      const r=hoverTarget.getBoundingClientRect();
      uniforms.uHover.value.set(r.left+r.width/2,r.top+r.height/2,r.width,r.height);
    }else hoverTarget=null;
    const running=[...document.querySelectorAll('.st-running.in-viewport .flash-card')].slice(0,8);
    uniforms.uCardCount.value=running.length;
    running.forEach((card,index)=>{const r=card.getBoundingClientRect();cards[index].set(r.left+r.width/2,r.top+r.height/2,r.width,r.height);});
    const frames=[...document.querySelectorAll('.workspace > .controls,.workspace > .canvas,.canvas > .composer')].filter(el=>el.getClientRects().length).slice(0,3);
    uniforms.uPanelCount.value=frames.length;
    frames.forEach((element,index)=>{const r=element.getBoundingClientRect();panels[index].set(r.left+r.width/2,r.top+r.height/2,r.width,r.height);});
    const gallery=document.getElementById('galleryScroll');
    atmosphere.setGallery(gallery?.getClientRects().length?gallery.getBoundingClientRect():null);
    dirty=false;
  }
  function frame(now){
    frameId=0;if(disposed||contextLost||document.hidden||!previewVisible)return;
    if(resizing)resize();if(dirty)updateRects();
    if(ambientLast&&!paused&&!motion.matches)ambientClock+=Math.min((now-ambientLast)/1000,.25);
    ambientLast=now;
    const interactive=now<activeUntil||uniforms.uCardCount.value>0;
    if(interactive&&lastFrame){frameStrain=now-lastFrame>27?frameStrain+1:Math.max(0,frameStrain-.1);}
    if(frameStrain>6&&quality>.42){quality=.42;resizing=true;frameStrain=0;}
    lastFrame=now;
    const interval=interactive?15:31;
    if(now-lastDraw>=interval||motion.matches){
      uniforms.uTime.value=(now-start)/1000;
      uniforms.uMotion.value=motion.matches?0:1;
      uniforms.uAmbienceTime.value=ambientClock;atmosphere.tick(ambientClock);
      uniforms.uPointer.value.lerp(pointerTarget.set(pointerX,pointerY),.18);
      hoverLevel+=(Number(!!hoverTarget)-hoverLevel)*.16;
      uniforms.uHoverStrength.value=hoverLevel;
      const before=performance.now();renderer.render(scene,camera);draws++;
      if(performance.now()-before>19)slowFrames++;else slowFrames=Math.max(0,slowFrames-1);
      if(slowFrames>10&&quality>.42){quality=.42;resizing=true;slowFrames=0;}
      lastDraw=now;
    }
    if(!motion.matches&&(!paused||now<activeUntil))wake();
  }
  function burst(x,y,strength=.5){
    if(motion.matches)return;
    clicks[clickIndex++%clicks.length].set(x,y,seconds(),strength);
    activeUntil=performance.now()+1400;wake();
  }
  function reveal(element){
    if(motion.matches||!element?.isConnected)return;
    const r=element.getBoundingClientRect();
    arrivals[arrivalIndex++%arrivals.length].set(r.left+r.width/2,r.top+r.height*.4,seconds(),1);
    activeUntil=performance.now()+1400;invalidate();
  }
  function configure(options={}){
    if(options.palette?.length===3){uniforms.uViolet.value.set(options.palette[0]);uniforms.uPearl.value.set(options.palette[1]);uniforms.uRose.value.set(options.palette[2]);}
    paused=!!options.paused;ambientLast=0;atmosphere.configure(options);
    dirty=true;activeUntil=performance.now()+500;wake();
  }
  function setPreviewVisible(visible){
    previewVisible=!!visible;ambientLast=0;
    cancelAnimationFrame(frameId);frameId=0;
    if(previewVisible){dirty=true;resizing=true;lastDraw=-100;wake();}
  }
  function samplePixels(){
    if(disposed||contextLost)return {available:false};
    if(resizing)resize();if(dirty)updateRects();
    renderer.render(scene,camera);
    const gl=renderer.getContext(),w=gl.drawingBufferWidth,h=gl.drawingBufferHeight;
    const bytes=new Uint8Array(w*h*4);gl.readPixels(0,0,w,h,gl.RGBA,gl.UNSIGNED_BYTE,bytes);
    let colored=0,maxAlpha=0,hash=2166136261;
    for(let i=0;i<bytes.length;i+=4){if(bytes[i+3]>3&&(bytes[i]+bytes[i+1]+bytes[i+2])>10)colored++;maxAlpha=Math.max(maxAlpha,bytes[i+3]);hash=Math.imul(hash^bytes[i],16777619);hash=Math.imul(hash^bytes[i+3],16777619);}
    return {available:true,width:w,height:h,coloredPixels:colored,maxAlpha,hash:hash>>>0,clock:ambientClock};
  }
  listen(window,'message',event=>{
    if(window.parent!==window&&event.source===window.parent&&event.data?.type==='prism:preview-visibility')setPreviewVisible(event.data.visible);
  });
  const interactiveSelector='button:not(:disabled),a[href],summary,.upload,.shot[aria-disabled="false"],.style-summary';
  listen(document,'pointermove',event=>{pointerX=event.clientX;pointerY=event.clientY;activeUntil=performance.now()+350;wake();},{passive:true});
  listen(document,'pointerover',event=>{const next=event.target.closest(interactiveSelector);if(next!==hoverTarget){hoverTarget=next;activeUntil=performance.now()+700;invalidate();}},{passive:true});
  listen(document,'pointerdown',event=>{if(event.button!==0||!event.target.closest(interactiveSelector))return;burst(event.clientX,event.clientY,event.target.closest('.shot') ? .9 : .35);},{passive:true});
  listen(document,'pointerleave',()=>{pointerX=-1000;pointerY=-1000;hoverTarget=null;invalidate();});
  listen(document,'focusin',event=>{hoverTarget=event.target.closest(interactiveSelector);invalidate();});
  listen(document,'focusout',()=>{hoverTarget=null;invalidate();});
  listen(document,'scroll',invalidate,{passive:true,capture:true});
  listen(window,'resize',()=>{resizing=true;invalidate();},{passive:true});
  listen(document,'visibilitychange',()=>{ambientLast=0;if(document.hidden){cancelAnimationFrame(frameId);frameId=0;}else{lastDraw=-100;wake();}});
  listen(motion,'change',()=>{uniforms.uMotion.value=motion.matches?0:1;wake();});
  listen(canvas,'webglcontextlost',event=>{event.preventDefault();contextLost=true;cancelAnimationFrame(frameId);frameId=0;document.documentElement.dataset.prismRenderer='css-fallback';});
  listen(canvas,'webglcontextrestored',()=>{contextLost=false;resizing=true;document.documentElement.dataset.prismRenderer='three-webgl';wake();});
  function dispose(){
    if(disposed)return;disposed=true;cancelAnimationFrame(frameId);frameId=0;
    listeners.forEach(remove=>remove());atmosphere.dispose();geometry.dispose();material.dispose();renderer.dispose();renderer.forceContextLoss();
  }
  listen(window,'pagehide',event=>{if(event.persisted){cancelAnimationFrame(frameId);frameId=0;}else dispose();});
  listen(window,'pageshow',event=>{if(event.persisted){resizing=true;lastDraw=-100;wake();}});
  document.documentElement.dataset.prismRenderer='three-webgl';
  window.PrismFX={active:true,invalidate,reveal,burst,configure,setPreviewVisible,samplePixels,dispose,stats:()=>({active:!disposed&&!contextLost,three:THREE.REVISION,draws,drawCalls:renderer.info.render.calls,pixelRatio:renderer.getPixelRatio(),quality,cardEffects:uniforms.uCardCount.value,reducedMotion:motion.matches,paused,previewVisible,atmosphere:atmosphere.stats()})};
  resize();wake();
  if(window.PrismAppearance)configure({palette:window.PrismAppearance.palette.shader,...window.PrismAppearance.effects});
  document.dispatchEvent(new CustomEvent('prism:renderer-ready'));
  if(window.parent!==window)window.parent.postMessage({type:'prism:renderer-ready'},'*');
})();
