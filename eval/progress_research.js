/* Independent from campaign polling: a missing campaign must not disable research. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let busy = false, sending = false, latest = null, historyKey = '';
  const labels = {off:'Off', starting:'Starting', running:'Running locally', stopping:'Stopping',
    stopped:'Stopped', finished:'Run finished', failed:'Needs attention', interrupted:'Interrupted'};
  function render(d) {
    latest = d;
    el('research-status').textContent = labels[d.status] || d.status;
    el('research-status').className = 'badge ' + (d.active ? '' : d.status === 'failed' ? 'warn' : 'idle');
    el('research-phase').textContent = d.phase;
    el('research-title').textContent = d.current_title || 'Choose a budget and start a run.';
    el('research-calls').textContent = `${d.calls || 0} / ${d.max_calls || 12}`;
    el('research-compiles').textContent = `${d.compiles || 0} / ${d.max_compiles || 72}`;
    el('research-findings').textContent = `${d.confirmed || 0}`;
    el('research-time').textContent = `${Math.floor((d.elapsed_seconds || 0)/60)} / ${d.minutes || 30} min`;
    el('research-totals').textContent = `${d.experiments || 0} experiments · ${d.counterexamples || 0} counterexamples · ${d.errors || 0} errors · $0 API spend`;
    el('research-error').textContent = d.last_error || '';
    el('research-error').classList.toggle('hidden', !d.last_error);
    el('research-notebook').textContent = d.notebook ? `Notebook: ${d.notebook}` : 'Findings and failed experiments persist between runs.';
    el('research-start').disabled = sending || d.active || !d.controls_enabled;
    el('research-stop').disabled = sending || !d.active || d.status === 'stopping' || !d.controls_enabled;
    for (const id of ['research-model', 'research-minutes', 'research-limit']) el(id).disabled = d.active || sending;
    const history = d.recent || [], key = JSON.stringify(history);
    if (key !== historyKey) {
      historyKey = key;
      const root = el('research-history'); root.replaceChildren();
      for (const row of history) {
        const detail = document.createElement('details'), summary = document.createElement('summary');
        summary.textContent = `${row.proposal.title} — ${row.status.replaceAll('_',' ')}`;
        detail.append(summary);
        const why = document.createElement('p'); why.textContent = row.proposal.rationale; detail.append(why);
        const prediction = document.createElement('p');
        prediction.textContent = `Prediction: after ${row.proposal.metric} ${row.proposal.relation} than before. Game transfer: untested.`;
        detail.append(prediction);
        const pre = document.createElement('pre');
        pre.textContent = `BEFORE\n${row.proposal.before}\n\nAFTER\n${row.proposal.after}\n\nMEASUREMENTS\n` +
          (row.measurements || []).map(m => {
            const value = side => {
              const r = m[side];
              if (!r.compiled) return `compile failed: ${r.stderr || 'see run log'}`;
              const f = r.features || {};
              return row.proposal.metric === 'saved_register_count' ? (f.saved_registers || []).filter(x=>x!=='ra').length : f[row.proposal.metric];
            };
            return `${m.phase} K=${m.k}: ${value('before')} → ${value('after')}`;
          }).join('\n');
        detail.append(pre); root.append(detail);
      }
    }
  }
  async function refresh() {
    if (busy) return; busy = true;
    try {
      const response = await fetch('/api/research', {cache:'no-store', signal:AbortSignal.timeout(6000)});
      const d = await response.json(); if (!response.ok) throw Error(d.error || 'Research unavailable');
      render(d);
    } catch (e) {
      el('research-status').textContent = 'Disconnected';
      el('research-phase').textContent = e.message;
      el('research-start').disabled = true;
    } finally { busy = false; }
  }
  async function control(action) {
    if (sending) return; sending = true;
    if (latest) render(latest);
    try {
      const body = {action};
      if (action === 'start') body.options = {model:el('research-model').value,
        minutes:Number(el('research-minutes').value), max_calls:Number(el('research-limit').value)};
      const response = await fetch('/api/research/control', {method:'POST',
        headers:{'Content-Type':'application/json','X-Campaign-Control':'ui'},
        body:JSON.stringify(body), signal:AbortSignal.timeout(10000)});
      const d = await response.json(); if (!response.ok) throw Error(d.error || 'Research control failed');
      sending = false; render(d);
    } catch (e) {
      sending = false;
      if (latest) render(latest);
      el('research-error').textContent = e.message; el('research-error').classList.remove('hidden');
    }
  }
  el('research-start').addEventListener('click', () => control('start'));
  el('research-stop').addEventListener('click', () => control('stop'));
  el('refresh').addEventListener('click', refresh);
  refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
