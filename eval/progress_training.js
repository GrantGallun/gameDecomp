/* Separate control plane from campaign polling and from compiler research: training
   is an explicit action with its own limits, and a missing worker must not disable it. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let busy = false, sending = false, latest = null;
  // `completed` is a claim about the run only: the trainer exited 0 and its receipt
  // was written. It is not a claim that the adapter is better than the baseline.
  const labels = {off:'Off', starting:'Starting', running:'Training locally', stopping:'Stopping',
    stopped:'Stopped', completed:'Completed', failed:'Needs attention', interrupted:'Interrupted'};
  function render(d) {
    latest = d;
    el('training-status').textContent = labels[d.status] || d.status;
    el('training-status').className = 'badge ' +
      (d.active ? '' : (d.status === 'failed' || d.status === 'interrupted') ? 'warn' : 'idle');
    el('training-phase').textContent = d.phase || '';
    el('training-stage').textContent = d.stage || 'idle';
    el('training-steps').textContent = `${d.steps || 0} / ${d.max_steps || 600}`;
    el('training-examples').textContent =
      `${d.examples_used || 0} / ${d.max_examples || 20000}`;
    el('training-time').textContent =
      `${Math.floor((d.elapsed_seconds || 0)/60)} / ${Math.round((d.max_seconds || 3600)/60)} min`;
    el('training-loss').textContent = d.last_loss === null || d.last_loss === undefined
      ? '—' : Number(d.last_loss).toFixed(4);
    el('training-reason').textContent = d.terminal_reason
      ? `Terminal reason: ${d.terminal_reason}` : '';
    el('training-reason').classList.toggle('hidden', !d.terminal_reason);
    // Only a completed run with a verified receipt names an artifact. A stopped,
    // interrupted or failed run publishes nothing -- checked here as well as in the
    // control plane, so the panel cannot display an adapter for a run that was cut off.
    const adapter = d.status === 'completed' ? d.published_adapter : null;
    el('training-adapter').textContent = adapter
      ? `${adapter} (unverified: evaluate before any promotion)`
      : (d.active ? 'No adapter is published while a run is in progress.'
                  : 'No adapter is published from this run.');
    el('training-error').textContent = d.last_error || '';
    el('training-error').classList.toggle('hidden', !d.last_error);
    el('training-start').disabled = sending || d.active || !d.controls_enabled;
    el('training-stop').disabled = sending || !d.active || d.status === 'stopping'
      || !d.controls_enabled;
    for (const id of ['training-steps-limit', 'training-minutes', 'training-examples-limit'])
      el(id).disabled = d.active || sending;
    if (d.stop_pending) el('training-phase').textContent =
      'Stop requested: the trainer process group is being terminated.';
  }
  async function refresh() {
    if (busy) return; busy = true;
    try {
      const response = await fetch('/api/training', {cache:'no-store',
        signal:AbortSignal.timeout(6000)});
      const d = await response.json();
      if (!response.ok) throw Error(d.error || 'Training status unavailable');
      render(d);
    } catch (e) {
      el('training-status').textContent = 'Disconnected';
      el('training-phase').textContent = e.message;
      el('training-start').disabled = true;
    } finally { busy = false; }
  }
  async function control(action) {
    if (sending) return; sending = true;
    if (latest) render(latest);
    try {
      const body = {action};
      if (action === 'start') body.options = {
        max_steps: Number(el('training-steps-limit').value),
        minutes: Number(el('training-minutes').value),
        max_examples: Number(el('training-examples-limit').value)};
      const response = await fetch('/api/training/control', {method:'POST',
        headers:{'Content-Type':'application/json','X-Campaign-Control':'ui'},
        body:JSON.stringify(body), signal:AbortSignal.timeout(20000)});
      const d = await response.json();
      if (!response.ok) throw Error(d.error || 'Training control failed');
      sending = false; render(d);
    } catch (e) {
      sending = false;
      if (latest) render(latest);
      el('training-error').textContent = e.message;
      el('training-error').classList.remove('hidden');
    }
  }
  el('training-start').addEventListener('click', () => control('start'));
  el('training-stop').addEventListener('click', () => control('stop'));
  refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
