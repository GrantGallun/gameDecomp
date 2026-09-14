/* Fresh observed entries are extra test inputs, not a whole-game verdict. */
(() => {
 const el=id=>document.getElementById(id);
 const labels={not_run:'Waiting for an eligible capture',capturing:'Capturing game state',replaying:'Comparing captured entries',passed:'Captured comparisons passed',failed:'Captured mismatch found',unavailable:'Capture unavailable'};
 let loading=false;
 function add(tag,value,parent,cls){const node=document.createElement(tag);node.textContent=value;if(cls)node.className=cls;parent.append(node);return node;}
 function render(d){
  const box=el('runtime-content');box.replaceChildren();
  el('runtime-status').textContent=(labels[d.status]||d.status)+(d.status==='passed'&&!d.current_pass?' · requires revalidation':'');
  if(d.status==='not_run'){add('p','Fresh captures will appear here when the campaign runs a configured capture plan.',box);el('runtime-freshness').textContent=`Checkpoint ${d.commit}`;return;}
  const heading=add('p','',box);const link=add('a',d.function||'Unknown function',heading);link.href='/api/function?name='+encodeURIComponent(d.function||'');
  const summary=add('div','',box,'integration-summary');add('strong',String(d.capture_count),summary);add('span','observed entries in this capture job',summary);
  const counts=d.counts||{};add('p',`${counts.passed||0} passed · ${counts.failed||0} mismatched · ${counts.inconclusive||0} inconclusive`,box);
  if(d.error)add('p',d.error,box,'note');
  for(const issue of d.issues||[])add('p',issue,box,'note');
  const details=add('details','',box,'integration-bindings');add('summary','Source binding and capture receipts',details);
  const binding=d.source_binding||{};add('p',`Attempt ${binding.attempt_id??'unknown'} · ${d.current_binding?'source binding current':'source binding changed or unavailable'}`,details,'note');
  add('div','Source SHA256',details,'stage');add('code',binding.source_sha256||'Unavailable',details);
  add('div','Certificate SHA256',details,'stage');add('code',binding.verification_sha256||'Unavailable',details);
  for(const artifact of d.artifacts||[]){const p=add('div','',details,'integration-artifact');
   if(artifact.available&&artifact.url){const a=add('a',artifact.kind==='receipt'?'Open capture/replay receipt':'Open capture log',p);a.href=artifact.url;}
   else add('span',`${artifact.kind||'Artifact'} unavailable`,p);
   add('div',artifact.path||'Unknown path',p,'stage');add('code',artifact.sha256||'Hash unavailable',p);
  }
  const updated=Number.isFinite(d.updated_at)?new Date(d.updated_at*1000).toLocaleString():'not recorded';
  el('runtime-freshness').textContent=`Capture checkpoint ${d.checkpoint??'unknown'} · current checkpoint ${d.commit} · last phase update ${updated}`;
 }
 async function refresh(){if(loading||document.hidden||paused)return;loading=true;try{
  const response=await fetch('/api/runtime',{cache:'no-store',signal:AbortSignal.timeout(15000)});const d=await response.json();if(!response.ok)throw Error(d.error||'Capture progress unavailable');render(d);
 }catch(error){el('runtime-status').textContent='Capture view unavailable';el('runtime-freshness').textContent=error.message+' · Any previously displayed result may be stale.';}finally{loading=false;}}
 el('refresh').addEventListener('click',refresh);el('freeze').addEventListener('click',()=>{if(!paused)refresh();});document.addEventListener('visibilitychange',refresh);refresh();setInterval(refresh,10000);
})();
