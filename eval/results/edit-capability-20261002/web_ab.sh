#!/bin/bash
# Web A/B on the anonymized exam (web_exam.py), queued after a self-curriculum round. One GPU job at a time:
# lora `read` (web off, on) -> stop server -> gpt-oss via Ollama (web off, on) -> unload -> grade.
set -uo pipefail
P=$HOME/decomp/experiments/edit-capability-20261002
D=$P/web-exam-v1
WAIT_FOR=${1:-$P/self-curriculum-v1/round.done}
HERE=/mnt/c/Code/gameDecomp/eval/results/edit-capability-20261002
PY=$HOME/decomp/sbk1/.venv/bin/python
log() { echo "[$(date '+%F %T')] $*" | tee -a "$D/ab.log"; }
fail() { log "FAILED: $*"; echo "$*" > "$D/FAILED"; pkill -f 'tools.lora_serve.server'; exit 1; }
rm -f "$D/FAILED"
until [ -f "$WAIT_FOR" ] || [ -f "$P/self-curriculum-v1/FAILED" ]; do sleep 60; done
cd "$HERE" || fail cd

(cd /mnt/c/Code/gameDecomp && SOLVER_VLLM_GPU_UTILIZATION=0.70 SOLVER_VLLM_MAX_SEQS=16 SOLVER_VLLM_CONTINUOUS=1 \
   SOLVER_VLLM_EAGER=0 SOLVER_VLLM_LORA_RANK=8 SOLVER_VLLM_BATCHED_TOKENS=4096 SOLVER_VLLM_KV_GIB=2.5 \
   setsid nohup bash .cache/recon/serve_lowfootprint.sh --adapter "read=$P/reading-v1/adapter_read" \
   > "$D/serve.log" 2>&1 < /dev/null &)
for _ in $(seq 120); do curl -sf http://127.0.0.1:8101/v1/models > /dev/null && break; sleep 5; done
curl -sf http://127.0.0.1:8101/v1/models > /dev/null || fail "server did not start"
for web in off on; do
  $PY web_exam.py run --dir "$D" --arm lora:read --web $web --jobs 8 > "$D/run_read_$web.log" 2>&1 || fail "read $web"
  log "read web=$web done"
done
pkill -f 'tools.lora_serve.server'; sleep 20
for web in off on; do
  $PY web_exam.py run --dir "$D" --arm ollama:gpt-oss:20b --web $web --jobs 3 > "$D/run_gptoss_$web.log" 2>&1 \
      || fail "gpt-oss $web"
  log "gpt-oss web=$web done"
done
GW=$(ip route show default | awk '{print $3}')
curl -s "http://$GW:11434/api/generate" -d '{"model":"gpt-oss:20b","keep_alive":0}' > /dev/null || true
$PY web_exam.py grade --dir "$D" > "$D/grade.log" 2>&1 || fail grade
log done
echo done > "$D/done"
