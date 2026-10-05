/* Deterministic squarified treemap; no network dependencies. */
(() => {
'use strict';
const el=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
// Parked is split by cause. Pipeline-blocked takes gold: the old brown (#8b6e30)
// sat 6.8 dE from compile-blocked orange (1.6 for deuteranopia). Gold passes the
// palette validator against every other map colour and both ends of the ramp.
// Assembly-by-design gets a HATCH rather than another hue: nine categories plus a
// continuous blue scale leave no hue clear of them all (every purple failed
// against Function exact), so texture is the secondary encoding.
const colors={exact:'#23845b',rom_verified:'#3fb9ac',function_exact:'#607ed0',partial:'#24688d',compile_blocked:'#aa603b',parked:'#d9b547',parked_asm:'#39424f',untouched:'#303f51',pending:'#586078'};
const labels={exact:'Object exact',rom_verified:'ROM verified',function_exact:'Function exact',partial:'Compiling',compile_blocked:'Compile blocked',parked:'Parked: pipeline gap',parked_asm:'Assembly by design',untouched:'Untouched',pending:'Pending'};
const HATCH_FG='#8e99a6', HATCH_BG=colors.parked_asm;
// Compiling functions shade by byte score on ONE blue hue, light (low) to deep (high).
// Steps 100-600 of the reference sequential ramp: 600 is the darkest that still clears
// this dark surface (validated ordinal, 2.32:1). The domain is a fixed 0-100, not the
// current min/max, so a tile only changes colour when its own score does.
const scoreRamp=['#cde2fb','#b7d3f6','#9ec5f4','#86b6ef','#6da7ec','#5598e7','#3987e5','#2a78d6','#256abf','#1c5cab','#184f95'];
function rampColor(score){const t=Math.min(1,Math.max(0,score/100))*(scoreRamp.length-1),i=Math.min(scoreRamp.length-2,Math.floor(t)),f=t-i;const hex=h=>[1,3,5].map(k=>parseInt(h.slice(k,k+2),16));const a=hex(scoreRamp[i]),b=hex(scoreRamp[i+1]);return'#'+a.map((v,k)=>Math.round(v+(b[k]-v)*f).toString(16).padStart(2,'0')).join('');}
function fillFor(r){if(r.category==='parked_asm')return'url(#asm-hatch)';return r.category==='partial'&&typeof r.score==='number'?rampColor(r.score):(colors[r.category]||colors.pending);}
function hatchDefs(){const defs=shape('defs',{},svg),p=shape('pattern',{id:'asm-hatch',width:6,height:6,patternUnits:'userSpaceOnUse',patternTransform:'rotate(45)'},defs);shape('rect',{width:6,height:6,fill:HATCH_BG},p);shape('rect',{width:2,height:6,fill:HATCH_FG},p);}
function distNote(r){return typeof r.instruction_distance==='number'?` · ${r.instruction_distance} instr / ${r.register_distance} reg`:'';}
function scoreNote(r){return r.category==='partial'?(typeof r.score==='number'?` · score ${Number(r.score).toFixed(3)}${distNote(r)}`:' · score unavailable'):'';}
let data=null, selected=null, group='', category='', loading=false, detailSequence=0, lastCommit=null;
const fmt=n=>Number(n||0).toLocaleString(), bytes=n=>n>=1048576?(n/1048576).toFixed(2)+' MiB':n>=1024?(n/1024).toFixed(1)+' KiB':fmt(n)+' B';
const svg=el('program-map');
function text(tag, value, parent, cls){const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;parent.append(n);return n;}
function shape(tag, attrs, parent){const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);parent.append(n);return n;}
function worst(row, side){if(!row.length)return Infinity;const sum=row.reduce((a,r)=>a+r.area,0),areas=row.map(r=>r.area);return Math.max(side*side*Math.max(...areas)/(sum*sum),sum*sum/(side*side*Math.min(...areas)));}
function tile(items,x,y,w,h){
 if(!items.length||w<=0||h<=0)return [];
 const total=items.reduce((a,r)=>a+r.weight,0);
 const remaining=items.map(r=>({...r,area:r.weight/total*w*h})).sort((a,b)=>b.area-a.area||a.key.localeCompare(b.key));
 const out=[];let row=[];
 function place(){const sum=row.reduce((a,r)=>a+r.area,0);if(w>=h){const width=sum/h;let yy=y;for(const r of row){const hh=r.area/width;out.push({...r,x,y:yy,w:width,h:hh});yy+=hh;}x+=width;w=Math.max(0,w-width);}else{const height=sum/w;let xx=x;for(const r of row){const ww=r.area/height;out.push({...r,x:xx,y,w:ww,h:height});xx+=ww;}y+=height;h=Math.max(0,h-height);}row=[];}
 for(const r of remaining){if(row.length&&worst([...row,r],Math.min(w,h))>worst(row,Math.min(w,h)))place();row.push(r);}if(row.length)place();return out;
}
const query=()=>el('map-search').value.trim().toLowerCase();
function matches(r){return (!query()||r.name.toLowerCase().includes(query()))&&(!category||r.category===category);}
function draw(){
 if(!data)return;svg.replaceChildren();hatchDefs();const w=svg.clientWidth,h=svg.clientHeight;if(!w||!h)return;
 svg.setAttribute('viewBox',`0 0 ${w} ${h}`);
 const nodes=data.functions.filter(r=>r.size&&(!group||r.group===group)), groups=new Map();
 for(const r of nodes){if(!groups.has(r.group))groups.set(r.group,[]);groups.get(r.group).push(r);}
 const chunks=tile([...groups].map(([key,rows])=>({key,rows,weight:rows.reduce((a,r)=>a+r.size,0)})),0,0,w,h);
 const active=new Set(data.active||[]);
 for(const g of chunks){
  const pad=group?0:Math.min(1.5,g.w/10,g.h/10);
  for(const t of tile(g.rows.map(r=>({key:r.name,row:r,weight:r.size})),g.x+pad,g.y+pad,Math.max(0,g.w-2*pad),Math.max(0,g.h-2*pad))){
   const r=t.row, show=matches(r), rect=shape('rect',{x:t.x,y:t.y,width:t.w,height:t.h,fill:fillFor(r),opacity:show?1:.16,'data-name':r.name,class:[selected===r.name?'selected':'',active.has(r.name)?'active':''].join(' ')},svg);
   shape('title',{},rect).textContent=`${r.name}\n${bytes(r.size)} · ${labels[r.category]}${scoreNote(r)}\n${r.group}`;
   rect.addEventListener('click',()=>select(r.name));
   rect.addEventListener('dblclick',()=>{group=r.group;el('map-group').value=group;draw();searchResults();});
   rect.addEventListener('pointerenter',()=>{el('map-hover').textContent=`${r.name} · ${bytes(r.size)} · ${labels[r.category]}${scoreNote(r)}`;});
   if(show&&t.w>78&&t.h>28){const limit=Math.max(5,Math.floor((t.w-10)/6));shape('text',{x:t.x+5,y:t.y+15},svg).textContent=r.name.length>limit?r.name.slice(0,limit-1)+'…':r.name;if(t.h>45)shape('text',{x:t.x+5,y:t.y+30,opacity:.72},svg).textContent=bytes(r.size);}
  }
 }
 el('map-loading').classList.toggle('hidden',nodes.length>0);if(!nodes.length)el('map-loading').textContent='No functions with known sizes in this view.';
 for(const b of el('map-legend').querySelectorAll('button'))b.setAttribute('aria-pressed',String(category===b.dataset.category));
}
function searchResults(){const box=el('map-matches');box.replaceChildren();if(!data||(!query()&&!category))return;const found=data.functions.filter(r=>(!group||r.group===group)&&matches(r));text('p',`${fmt(found.length)} matching functions`,box,'map-help');for(const r of found.slice(0,20)){const b=text('button',r.name,box);b.addEventListener('click',()=>select(r.name));}if(found.length>20)text('p','Showing the first 20. Refine your search to find another.',box,'map-help');}
async function select(name){
 selected=name;draw();const sequence=++detailSequence,box=el('map-detail');box.replaceChildren();text('h3',name,box);text('p','Loading function details…',box);
 try{const response=await fetch('/api/function?name='+encodeURIComponent(name),{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!response.ok)throw Error('Function details unavailable');const d=await response.json();if(sequence!==detailSequence)return;
  box.replaceChildren();text('div','Function inspector',box,'eyebrow');text('h3',name,box);text('span',labels[d.category]||d.status,box,'pill');
  const dl=document.createElement('dl');box.append(dl);const info=[['Target size',d.size?bytes(d.size):'Unknown'],['Instructions',d.instructions??'Unknown'],['Address',d.address==null?'Unknown':'0x'+Number(d.address).toString(16)],['Byte score',d.score==null?'Not available':Number(d.score).toFixed(3)],['Instruction distance',d.instruction_distance??'Not measured'],['Register distance',d.register_distance??'Not measured'],['Repair work items',fmt(d.work_items)],['Current attempt',d.attempt_id??'None'],['Campaign state',d.status]];for(const[k,v]of info){text('dt',k,dl);text('dd',v,dl);}
  text('h4','Code cluster',box);const zoom=text('button',d.group+' · zoom in',box);zoom.onclick=()=>{group=d.group;el('map-group').value=group;draw();searchResults();};
  if(d.compiler_error){text('h4','Compiler blocker',box);text('pre',d.compiler_error,box);}
  text('h4','Current differences',box);if(d.differences.length)text('pre',d.differences.join('\n'),box);else text('p',d.category==='exact'?'No object differences.':'No instruction diff recorded in this checkpoint.',box);
  if(d.category==='rom_verified')text('p','Verified in a disposable whole-game build. This does not install the source in the canonical game or grant an object-section certificate.',box,'map-help');
  if(d.instruction_delta!=null)text('p',`Instruction count delta: ${d.instruction_delta>=0?'+':''}${d.instruction_delta}`,box,'map-help');
  text('h4','Differential coverage',box);const c=d.coverage||{};text('p',d.semantic_status?d.semantic_status.replaceAll('_',' '):'Not recorded for this function.',box);for(const[key,label]of [['instruction_coverage','Target instructions'],['branch_edge_coverage','Target branch edges']])if(c[key]!=null)text('p',label+': '+(100*c[key]).toFixed(1)+'%',box);
  for(const debt of d.debt)text('p',debt,box,'map-help');
  text('h4','Recent repair work',box);for(const j of d.recent_work)text('p',`${(j.profile||'Unknown stage').replaceAll('_',' ')} · ${j.status||'unknown'}`,box,'map-help');if(!d.recent_work.length)text('p','No repair work recorded.',box);
  text('p','Checkpoint '+d.commit+' · work items can contain several compiler/model attempts.',box,'map-help');
 }catch(e){if(sequence===detailSequence){box.replaceChildren();text('h3',name,box);text('p',e.message,box);}}
}
async function refresh(){if(loading||document.hidden||paused)return;loading=true;try{
 const response=await fetch('/api/map',{cache:'no-store',signal:AbortSignal.timeout(20000)});if(!response.ok)throw Error('Map data unavailable');const next=await response.json();data=next;
 window.dispatchEvent(new CustomEvent('campaign-map-count',{detail:{commit:next.commit,count:next.functions.filter(r=>r.compiled===false).length}}));
 el('map-byte-percent').textContent=next.total_bytes?(100*next.exact_bytes/next.total_bytes).toFixed(1)+'%':'—';el('map-bytes').textContent=bytes(next.total_bytes);el('map-count').textContent=fmt(next.functions.length)+' / '+fmt(next.group_count);
 const names=[...new Set(next.functions.map(r=>r.group))].sort();const selector=el('map-group');const previous=group;selector.replaceChildren();const all=text('option','All code clusters',selector);all.value='';for(const name of names){const o=text('option',name,selector);o.value=name;}group=names.includes(previous)?previous:'';selector.value=group;
 el('map-freshness').textContent=`Checkpoint ${next.commit} · map refreshes every 10 seconds${next.unknown_sizes?' · '+next.unknown_sizes+' functions with unknown sizes are searchable but excluded from area':''}`;
 draw();searchResults();if(selected&&lastCommit!==next.commit)select(selected);lastCommit=next.commit;
 }catch(e){el('map-freshness').textContent=e.message+' — retrying automatically. Previous map may be stale.';if(!data)el('map-loading').textContent='Map unavailable. Retrying…';}finally{loading=false;}}
for(const[key,label]of Object.entries(labels)){const b=document.createElement('button');b.dataset.category=key;b.setAttribute('aria-pressed','false');const sw=text('i','',b,'swatch');sw.style.background=key==='partial'?`linear-gradient(90deg,${scoreRamp[0]},${scoreRamp[scoreRamp.length-1]})`:key==='parked_asm'?`repeating-linear-gradient(45deg,${HATCH_FG} 0 2px,${HATCH_BG} 2px 5px)`:colors[key];b.append(document.createTextNode(key==='partial'?`${label} (lighter = lower score)`:label));b.onclick=()=>{category=category===key?'':key;draw();searchResults();};el('map-legend').append(b);}
el('map-search').addEventListener('input',()=>{draw();searchResults();});el('map-group').onchange=()=>{group=el('map-group').value;draw();searchResults();};el('map-reset').onclick=()=>{group=category='';el('map-search').value='';el('map-group').value='';draw();searchResults();};
el('refresh').addEventListener('click',refresh);el('freeze').addEventListener('click',()=>{if(!paused)refresh();});
new ResizeObserver(draw).observe(svg);document.addEventListener('visibilitychange',refresh);refresh();setInterval(refresh,10000);
})();
