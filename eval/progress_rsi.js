/* One bounded experiment mode beside the campaign view: the narrow-RSI run's own state
   file, read-only, plus Start/Stop. Its own control plane, so a missing runner must not
   affect campaign polling or the research/training panels.
   Every dynamic value is written as TEXT: a hypothesis, a gate reason and a runner log
   tail are runner output and are never inserted as HTML. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let busy = false, sending = false, latest = null;
  // The stage vocabulary of the state contract. An unrecognised stage keeps its own name
  // and is treated as a warning rather than silently mapped onto a known one.
  const labels = {idle:'Idle', unknown:'Unknown stage', frozen:'Parent frozen',
    research:'Researching', verify:'Verifying hypothesis', assemble:'Assembling candidate',
    train:'Training candidate', 'candidate-frozen':'Candidate frozen', evaluate:'Evaluating',
    accept:'Accepting candidate', done:'Finished', stopped:'Stopped', failed:'Needs attention',
    'keep-parent':'Parent kept', inconclusive:'Inconclusive'};
  const warn = new Set(['failed', 'keep-parent', 'inconclusive', 'unknown']);
  // Budget bars are PLAIN TEXT, so they read the same in a terminal, a log or here.
  const bar = (used, cap) => {
    const width = 10, n = cap > 0 ? Math.max(0, Math.min(width, Math.round(width*used/cap))) : 0;
    return '[' + '#'.repeat(n) + '-'.repeat(width-n) + ']';
  };
  function budget(b) {
    const kinds = (b && b.kinds) || [];
    if (!kinds.length) return 'No budget has been reported yet.';
    const caps = (b && b.caps) || {}, spent = (b && b.spent) || {}, left = (b && b.remaining) || {};
    return kinds.map(kind => {
      const cap = caps[kind] == null ? null : caps[kind];
      const used = spent[kind] == null ? 0 : spent[kind];
      // The control plane already derives a missing `remaining` from cap-minus-spent; this
      // only has to keep the two columns from silently disagreeing.
      const rest = left[kind] == null ? (cap == null ? '—' : Math.max(0, cap-used)) : left[kind];
      return `${String(kind).padEnd(12)} ${bar(used, cap)} ${used} / ${cap == null ? '—' : cap} spent · ${rest} left`;
    }).join('\n');
  }
  function render(d) {
    latest = d;
    // Relevant = there is an experiment to watch, or this dashboard can start one.
    el('rsi-panel').classList.toggle('hidden', !d.present && !d.controls_enabled);
    const stage = d.stage || 'idle';
    el('rsi-status').textContent = labels[stage] || stage;
    el('rsi-status').className = 'badge ' + (d.active ? '' : warn.has(stage) ? 'warn' : 'idle');
    el('rsi-phase').textContent = d.phase || '';
    el('rsi-stage').textContent = stage;
    const candidate = d.candidate || (d.active ? 'pending' : '—');
    el('rsi-generation').textContent =
      `${d.generation ? d.generation + ' · ' : ''}${d.parent || '—'} → ${candidate}`;
    el('rsi-evidence').textContent = d.evidence_status || 'unknown';
    const evaluation = d.evaluation || {};
    el('rsi-coverage').textContent = evaluation.coverage == null
      ? '—' : (100*Number(evaluation.coverage)).toFixed(1) + '%';
    el('rsi-panel-note').textContent = 'Evaluation · ' + (evaluation.panel || 'no panel') +
      (evaluation.functions == null ? '' : ` · ${evaluation.functions} functions`);
    el('rsi-hypothesis').textContent =
      d.hypothesis || (d.present ? 'No hypothesis is recorded for this stage yet.' : '');
    el('rsi-budget').textContent = budget(d.budget);
    el('rsi-gate').textContent = d.gate_reason ? 'Gate: ' + d.gate_reason : '';
    el('rsi-gate').classList.toggle('hidden', !d.gate_reason);
    el('rsi-error').textContent = d.last_error || '';
    el('rsi-error').classList.toggle('hidden', !d.last_error);
    el('rsi-start').disabled = sending || d.active || !d.controls_enabled;
    el('rsi-stop').disabled = sending || !d.active || d.stop_pending || !d.controls_enabled;
    el('rsi-minutes').disabled = sending || d.active;
    if (d.stop_pending) el('rsi-phase').textContent =
      'Stop requested: the STOP file is written and the run is stopping.';
  }
  async function refresh() {
    if (busy) return; busy = true;
    try {
      const response = await fetch('/api/rsi', {cache:'no-store',
        signal:AbortSignal.timeout(6000)});
      const d = await response.json();
      if (!response.ok) throw Error(d.error || 'Narrow-RSI state unavailable');
      render(d);
    } catch (e) {
      el('rsi-status').textContent = 'Disconnected';
      el('rsi-phase').textContent = e.message;
      el('rsi-start').disabled = true;
    } finally { busy = false; }
  }
  async function control(action) {
    if (sending) return; sending = true;
    if (latest) render(latest);
    try {
      const body = {action};
      if (action === 'start') body.options = {minutes: Number(el('rsi-minutes').value)};
      const response = await fetch('/api/rsi/control', {method:'POST',
        headers:{'Content-Type':'application/json','X-Campaign-Control':'ui'},
        body:JSON.stringify(body), signal:AbortSignal.timeout(30000)});
      const d = await response.json();
      if (!response.ok) throw Error(d.error || 'Narrow-RSI control failed');
      sending = false; render(d);
    } catch (e) {
      sending = false;
      if (latest) render(latest);
      el('rsi-error').textContent = e.message;
      el('rsi-error').classList.remove('hidden');
    }
  }
  el('rsi-start').addEventListener('click', () => control('start'));
  el('rsi-stop').addEventListener('click', () => control('stop'));
  refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
