/* Five sandbox-only black-first palettes. No production preference keys are used. */
(() => {
  'use strict';
  const palettes = [
    {id:'carbon',number:'01',name:'碳黑哑光',en:'CARBON / AMETHYST',tag:'紫色最克制',mood:'哑光黑 × 极细紫线',description:'大面积中性炭黑，紫色只负责焦点、细线和少量微光。黑色最纯，画面最安静。',ratio:'92 / 8',panel:'#0b0b0e',raised:'#121216',card:'#0e0e12',inset:'#070709',accent:'#ad96c9',metal:'#a6a0b0',edge:'#82738d39',glow:'143 113 174',halo:'66 43 86',button:'#19151f',buttonEnd:'#292030',glass:'rgba(10,10,13,.94)',blur:12,radius:14,lift:'.025',spark:60,shader:['#35263f','#b9a6c9','#776188']},
    {id:'smoke',number:'02',name:'黑曜烟玻璃',en:'SMOKED / VIOLET',tag:'保留玻璃感',mood:'烟黑玻璃 × 雾紫反光',description:'黑色玻璃保持通透，紫光集中在边缘和悬浮处。延续毛玻璃质感，面板不再整块发紫。',ratio:'88 / 12',panel:'#0c0b10',raised:'#15121a',card:'#100e15',inset:'#08070b',accent:'#bba1da',metal:'#aaa1b8',edge:'#a286ba49',glow:'158 119 190',halo:'72 45 91',button:'#21172c',buttonEnd:'#33213e',glass:'rgba(11,10,15,.82)',blur:18,radius:18,lift:'.055',spark:70,shader:['#4b315b','#c9b1df','#8e6ca9']},
    {id:'graphite',number:'03',name:'石墨银紫',en:'GRAPHITE / CHROME',tag:'层次最清楚',mood:'石墨黑 × 冷银紫',description:'用石墨灰拉开黑色层次，细边偏银，紫色更冷更淡。信息边界清楚，整体偏精密工具感。',ratio:'90 / 10',panel:'#101014',raised:'#1a191f',card:'#131217',inset:'#09090c',accent:'#b7b0d3',metal:'#c1bdc9',edge:'#b8b0cc4b',glow:'146 134 177',halo:'61 53 77',button:'#222027',buttonEnd:'#34303f',glass:'rgba(15,15,19,.94)',blur:12,radius:11,lift:'.045',spark:60,shader:['#3c3549','#c9c4d5','#8a7e9f']},
    {id:'plum',number:'04',name:'暖墨葡萄紫',en:'INK / DARK PLUM',tag:'偏暖更柔和',mood:'暖墨黑 × 暗葡萄紫',description:'黑底略带暖感，辅色采用低饱和葡萄紫。比冷紫温和，适合服装与材质图片长时间浏览。',ratio:'89 / 11',panel:'#100c10',raised:'#1a131a',card:'#130e14',inset:'#090609',accent:'#c0a0bd',metal:'#b7a4b2',edge:'#a583a745',glow:'163 116 160',halo:'76 43 74',button:'#241925',buttonEnd:'#38243b',glass:'rgba(14,10,14,.91)',blur:14,radius:17,lift:'.04',spark:65,shader:['#4c304b','#d1b4cb','#9b7299']},
    {id:'ultraviolet',number:'05',name:'曜黑电紫',en:'NOIR / ULTRAVIOLET',tag:'强调最鲜明',mood:'曜黑底色 × 精准电紫',description:'画布依旧是黑的，只在选中态、进度和按钮边缘放出更鲜明的紫。动效更醒目，紫色不铺满面板。',ratio:'85 / 15',panel:'#09090d',raised:'#131019',card:'#0e0b13',inset:'#050508',accent:'#b49aff',metal:'#afa4c7',edge:'#9b7bdf50',glow:'152 113 231',halo:'66 37 106',button:'#21132f',buttonEnd:'#38204d',glass:'rgba(8,8,12,.93)',blur:14,radius:14,lift:'.07',spark:80,shader:['#4f2c7b','#cebaff','#9770d8']}
  ];
  function get(id){return palettes.find(p=>p.id===id)||palettes[0];}
  function apply(root,id){
    const p=get(id);root.dataset.black=p.id;root.dataset.tone='obsidian';
    const tokens={bg:'#000000',rail:'#060608',panel:p.panel,raised:p.raised,card:p.card,inset:p.inset,fg:'#eeedf2',muted:'#bbb6c6',subtle:'#96909f',accent:p.accent,metal:p.metal,button:p.button,'button-end':p.buttonEnd,glow:p.glow,halo:p.halo,line:p.edge,edge:p.edge,bad:'#d9a7ba',action:'#f4f0fa',orbit:p.accent};
    for(const [key,value] of Object.entries(tokens))root.style.setProperty('--tone-'+key,value);
    for(const [key,value] of Object.entries({glass:p.glass,blur:p.blur+'px',radius:p.radius+'px',lift:p.lift}))root.style.setProperty('--bp-'+key,value);
    return p;
  }
  window.BlackPurplePalettes={palettes,get,apply};
})();
