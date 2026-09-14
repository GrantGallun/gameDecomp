# Campaign model endpoint recovery

The existing service stopped after three model-tag connection timeouts. The
dedicated Windows Ollama server on port 11435 was absent; the WSL gateway remained
172.28.32.1. Both Windows localhost and WSL checks failed before recovery.

Restored the existing `launch-campaign-gpu.ps1` configuration using its documented
PowerShell invocation: hidden Ollama PID 25536, one inference slot, f16 KV, Flash
Attention, 32,768 context, one loaded model maximum and 30-minute keepalive. WSL
then returned Ollama version 0.34.0. No model generation request was sent.

`pre-resume.json` records full verification of 16,100 frozen input hashes, function
inventory, model digest and absence of inflight work at checkpoint 2394. Existing
coverage was 664 object-exact functions out of 2,051, with 1,212 completed items,
112 score gains and 22 exact gains in the accumulated runtime metrics.

The previous medium-effort revision and the exact benchmark import were already
completed by Claude. The import receipt is
`../optimization-audit-20260911/exact-handoff-2403-v2/imported.json`: canonical
attempt 35239, proposal 2486, `initFixedTransform` object-exact.

The parent agent requested keeping the campaign drained for its scheduler
amendment. Service pause control/marker are set, service status is `paused`,
worker PID is null and the next service batch is configured as 200. No source
files, input pins, model identity or campaign checkpoint were changed here.
The parent owns the subsequent scheduler amendment and resume.
