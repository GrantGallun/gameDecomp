/* Source/checkpoint-bound build evidence. Never a canonical installation claim. */
(() => {
 const el=id=>document.getElementById(id);
 let loading=false;
 const labels={not_run:'No sweep recorded',running:'Building disposable copy',rom_exact:'Whole ROM matched',preparation_blocked:'Preparation blocked',build_failed:'Build failed',integration_halted:'Integration halted',stale:'Stale bindings'};
 function add(tag,value,parent,cls){const node=document.createElement(tag);node.textContent=value;if(cls)node.className=cls;parent.append(node);return node;}
 function render(d){
  const box=el('integration-content'),opened=new Set([...box.querySelectorAll('details[open]')].map(n=>n.dataset.section));box.replaceChildren();
  el('integration-status').textContent=(labels[d.status]||d.status)+(d.status==='rom_exact'&&!d.current_binding?' · historical receipt':'');
  if(d.status==='not_run'){add('p','Function-boundary matches are waiting for a recorded integration sweep.',box);return;}
  const summary=add('div','',box,'integration-summary');add('strong',String((d.verified_union||[]).length),summary);add('span',d.current_binding?'current replacements verified together':'replacements in the recorded successful union',summary);
  add('p',`${(d.selected||[]).length} selected in this sweep · ${(d.prior_union||[]).length} previously verified replacements included.`,box);
  if(d.status==='rom_exact'&&(d.unverified_selection||[]).length)add('p',`${d.unverified_selection.length} selected candidates remain unverified; only the surviving combined union is counted.`,box,'note');
  if(!d.current_binding)add('p','This result does not establish a current successful combined build. See its status and binding details below.',box,'note');
  if(d.previous_success){
   const previous=d.previous_success;
   add('p',`Earlier successful union: ${previous.verified_union.length} replacements at checkpoint ${previous.checkpoint}. ${previous.current_binding?'Its source bindings and receipts are still current.':'Its bindings or receipts require revalidation.'}`,box,'note');
   for(const artifact of previous.artifacts||[])if(artifact.kind==='receipt'&&artifact.available&&artifact.url){const p=add('p','',box,'note');const a=add('a','Earlier successful receipt',p);a.href=artifact.url;add('div',artifact.path,p,'stage');add('code',artifact.sha256,p);}
  }
  for(const issue of d.issues||[])add('p',issue,box,'note');
  const details=add('details','',box,'integration-bindings');details.dataset.section='bindings';details.open=opened.has('bindings');add('summary','Candidate source and certificate bindings',details);
  for(const row of (d.functions||[]).slice(0,200)){
   const p=add('p','',details,'note');const link=add('a',row.name,p);link.href='/api/function?name='+encodeURIComponent(row.name);
   add('span',` · attempt ${row.attempt_id??'unknown'} · ${row.current_binding?'binding current':'binding changed or unavailable'}`,p);
   add('div','Source SHA256',p);add('code',row.source_sha256||'Unavailable',p);
   add('div','Certificate SHA256',p);add('code',row.verification_sha256||'Unavailable',p);
  }
  if((d.functions||[]).length>200)add('p','Showing the first 200 bindings; the sweep receipt contains the complete set.',details,'note');
  const receipts=add('details','',box,'integration-bindings');receipts.dataset.section='receipts';receipts.open=opened.has('receipts');add('summary','Build receipts and hashes',receipts);
  for(const artifact of d.artifacts||[]){
   const p=add('div','',receipts,'integration-artifact');
   if(artifact.available&&artifact.url){const a=add('a',artifact.kind==='receipt'?'Open receipt (hash checked)':'Open build log',p);a.href=artifact.url;}
   else add('span',artifact.kind==='rom'?'Archived ROM hash':`${artifact.kind||'Artifact'} unavailable`,p);
   add('div',artifact.path||'Unknown path',p,'stage');add('code',artifact.sha256||'Hash unavailable',p);
  }
  el('integration-freshness').textContent=`Sweep checkpoint ${d.checkpoint??'unknown'} · current checkpoint ${d.commit} · receipt and log downloads verify their recorded SHA256.`;
 }
 async function refresh(){if(loading||document.hidden||paused)return;loading=true;try{
  const response=await fetch('/api/integration',{cache:'no-store',signal:AbortSignal.timeout(15000)});const d=await response.json();if(!response.ok)throw Error(d.error||'Integration data unavailable');render(d);
 }catch(error){el('integration-status').textContent='Receipt view unavailable';el('integration-freshness').textContent=error.message+' · Any previously displayed result may be stale.';}finally{loading=false;}}
 el('refresh').addEventListener('click',refresh);el('freeze').addEventListener('click',()=>{if(!paused)refresh();});document.addEventListener('visibilitychange',refresh);refresh();setInterval(refresh,10000);
})();
