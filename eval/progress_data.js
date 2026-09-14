/* These counts describe recorded data evidence, not overall game completion. */
(() => {
 const el=id=>document.getElementById(id);
 const number=value=>Number.isSafeInteger(value)&&value>=0?value.toLocaleString():'—';
 let loading=false;
 function add(tag,text,parent,cls){const node=document.createElement(tag);node.textContent=text;if(cls)node.className=cls;parent.append(node);return node;}
 function render(d){
  const box=el('data-content');box.replaceChildren();
  el('data-status').textContent=d.status==='recorded'?'Catalog recorded':d.status==='not_built'?'Waiting for a catalog':'Data evidence unavailable';
  el('data-freshness').textContent=`Checkpoint ${d.commit??'unknown'} · recorded binary data evidence`;
  if(d.status!=='recorded'){add('p',d.status==='not_built'?'Data measurements will appear after the campaign builds its catalog.':'The checkpoint does not contain a valid catalog summary.',box);return;}
  const s=d.summary||{};
  const grid=add('div','',box,'data-grid');
  for(const [label,key] of [['ROM-checked initial bytes','rom_verified_bytes'],['Reconstructed data bytes','reconstructed_bytes'],['BSS storage bytes','unbacked_bss_bytes'],['Typed C verified bytes','typed_c_verified_bytes']]){
   const stat=add('div','',grid,'stat');add('strong',number(s[key]),stat);add('small',label,stat);
  }
  add('p',`${number(s.functions_with_data_context)} functions with data evidence · ${number(s.functions_with_readonly_inputs)} functions with readonly test inputs`,box,'note');
  add('p',`${number(s.readonly_initial_bytes)} readonly initial bytes · ${number(s.mutable_initial_bytes)} mutable initial bytes · ${number(s.address_references)} named address references`,box,'note');
  if(s.omitted_regions||s.omitted_bytes||s.unparsed_directives)add('p',`${number(s.omitted_regions)} spans omitted · ${number(s.omitted_bytes)} bytes omitted · ${number(s.unparsed_directives)} unsupported directives`,box,'note');
  const details=add('details','',box,'integration-bindings');add('summary','Catalog identity',details);
  for(const [label,key] of [['Catalog SHA256','catalog_sha256'],['Input set SHA256','input_sha256'],['Receipt SHA256','sha256']]){add('div',label,details,'stage');add('code',(d.hashes||{})[key]||'Unavailable',details);}
 }
 async function refresh(){if(loading||document.hidden||paused)return;loading=true;try{
  const response=await fetch('/api/data',{cache:'no-store',signal:AbortSignal.timeout(15000)});
  const d=await response.json();if(!response.ok)throw Error(d.error||'Data progress unavailable');render(d);
 }catch(error){el('data-status').textContent='Data view unavailable';el('data-freshness').textContent=error.message+' · Previously displayed counts may be stale.';}finally{loading=false;}}
 el('refresh').addEventListener('click',refresh);el('freeze').addEventListener('click',()=>{if(!paused)refresh();});document.addEventListener('visibilitychange',refresh);refresh();setInterval(refresh,10000);
})();
