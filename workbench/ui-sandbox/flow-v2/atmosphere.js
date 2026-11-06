/* One instanced, screen-space sprite batch: stars, drifting dust and fine rain. */
(() => {
  'use strict';
  window.PrismAtmosphere={create(THREE,scene,shared){
    const counts={stars:108,particles:64,rain:76};
    const total=counts.stars+counts.particles+counts.rain;
    const seeds=new Float32Array(total*4),kinds=new Float32Array(total);
    let randomState=16893;
    const random=()=>{randomState=(1664525*randomState+1013904223)>>>0;return randomState/4294967296;};
    for(let i=0;i<total;i++){
      seeds.set([random(),random(),random(),random()],i*4);
      kinds[i]=i<counts.stars?0:i<counts.stars+counts.particles?1:2;
    }
    const geometry=new THREE.InstancedBufferGeometry();
    geometry.setAttribute('position',new THREE.Float32BufferAttribute([-1,-1,0,1,-1,0,1,1,0,-1,-1,0,1,1,0,-1,1,0],3));
    geometry.setAttribute('aSeed',new THREE.InstancedBufferAttribute(seeds,4));
    geometry.setAttribute('aKind',new THREE.InstancedBufferAttribute(kinds,1));
    geometry.instanceCount=total;
    const uniforms={
      uResolution:shared.uResolution,uPointer:shared.uPointer,uPanels:shared.uPanels,uPanelCount:shared.uPanelCount,
      uClock:{value:0},uEffects:{value:new THREE.Vector3(1,1,0)},uIntensity:{value:.6},
      uTint:{value:new THREE.Color('#c5b2d4')},uWarm:{value:new THREE.Color('#946f9f')},
      uGallery:{value:new THREE.Vector4(0,0,0,0)}
    };
    const material=new THREE.ShaderMaterial({
      transparent:true,depthTest:false,depthWrite:false,side:THREE.DoubleSide,blending:THREE.AdditiveBlending,
      uniforms,
      vertexShader:`precision highp float;
        attribute vec4 aSeed;attribute float aKind;
        uniform float uClock,uIntensity;uniform vec2 uResolution,uPointer;uniform vec3 uEffects;
        varying vec2 vCoord,vScreen;varying float vKind,vOpacity;
        void main(){
          float t=uClock;vec2 center=aSeed.xy;vec2 size=vec2(1.0);float opacity=1.0;
          vKind=aKind;vCoord=position.xy;
          if(aKind<.5){
            center+=vec2(sin(t*.12+aSeed.z*35.0),cos(t*.08+aSeed.w*29.0))*.0025;
            float large=step(.93,aSeed.z);
            size=vec2(1.4+aSeed.w*1.5+large*3.0);
            opacity=(.24+.44*pow(.5+.5*sin(t*(.7+aSeed.z)+aSeed.w*60.0),2.0))*uEffects.x;
          }else if(aKind<1.5){
            center.x=fract(center.x+sin(t*.20+aSeed.z*31.0)*.052);
            center.y=fract(center.y-t*(.017+aSeed.w*.016));
            vec2 delta=center-uPointer/uResolution;
            float influence=exp(-dot(delta*vec2(1.0,.7),delta*vec2(1.0,.7))*110.0);
            center+=delta*influence*.045;
            size=vec2(2.3+aSeed.z*3.4);
            opacity=(.48+.16*sin(aSeed.w*45.0+t*.9))*uEffects.y;
          }else{
            center.y=fract(center.y+t*(.18+aSeed.w*.13));
            center.x=fract(center.x+t*.025-center.y*.065);
            size=vec2(.65+aSeed.z*.65,11.0+aSeed.w*24.0);
            opacity=(.15+aSeed.z*.16)*uEffects.z;
          }
          vec2 local=position.xy*size;
          if(aKind>1.5)local.x+=position.y*size.y*.14;
          vScreen=center*uResolution+local;
          vec2 clip=vScreen/uResolution*2.0-1.0;clip.y=-clip.y;
          gl_Position=vec4(clip,0.0,1.0);
          vOpacity=opacity*(.35+uIntensity*1.35);
        }`,
      fragmentShader:`precision highp float;
        uniform vec3 uTint,uWarm;uniform vec4 uPanels[3],uGallery;uniform float uPanelCount;
        varying vec2 vCoord,vScreen;varying float vKind,vOpacity;
        void main(){
          if(vOpacity<.001)discard;
          float alpha;vec3 color=uTint;
          if(vKind<.5){
            float core=exp(-dot(vCoord,vCoord)*9.0);
            float rays=exp(-min(abs(vCoord.x),abs(vCoord.y))*30.0)*max(0.0,1.0-length(vCoord));
            alpha=core+rays*.43;
          }else if(vKind<1.5){
            alpha=exp(-dot(vCoord,vCoord)*4.2)+exp(-dot(vCoord,vCoord)*20.0)*.3;color=mix(uWarm,uTint,.78);
          }else{
            alpha=exp(-vCoord.x*vCoord.x*7.0)*(1.0-smoothstep(.0,1.0,abs(vCoord.y)));
            color=mix(uTint,vec3(.85),.18);
          }
          float cover=0.0;
          for(int i=0;i<3;i++)if(float(i)<uPanelCount){vec4 r=uPanels[i];vec2 q=abs(vScreen-r.xy)-r.zw*.5;if(q.x<0.0&&q.y<0.0)cover=1.0;}
          alpha*=mix(1.0,.64,cover);
          vec2 image=abs(vScreen-uGallery.xy)-uGallery.zw*.5;
          if(image.x<0.0&&image.y<0.0)alpha*=.08;
          gl_FragColor=vec4(color,alpha*vOpacity);
        }`
    });
    const mesh=new THREE.Mesh(geometry,material);mesh.frustumCulled=false;mesh.renderOrder=2;scene.add(mesh);
    let state={stars:false,particles:true,rain:false,intensity:80};
    return {
      tick(time){uniforms.uClock.value=time;},
      setGallery(rect){if(rect)uniforms.uGallery.value.set(rect.left+rect.width/2,rect.top+rect.height/2,rect.width,rect.height);else uniforms.uGallery.value.set(0,0,0,0);},
      configure(options){
        state={...state,...options};
        uniforms.uEffects.value.set(state.stars?1:0,state.particles?1:0,state.rain?1:0);
        uniforms.uIntensity.value=Math.max(.1,Math.min(1,Number(state.intensity)/100));
        if(options.palette){uniforms.uTint.value.set(options.palette[1]);uniforms.uWarm.value.set(options.palette[2]);}
        mesh.visible=state.stars||state.particles||state.rain;
      },
      stats(){return {stars:state.stars?counts.stars:0,particles:state.particles?counts.particles:0,rain:state.rain?counts.rain:0,intensity:state.intensity,clock:uniforms.uClock.value,instances:total};},
      dispose(){scene.remove(mesh);geometry.dispose();material.dispose();}
    };
  }};
})();
