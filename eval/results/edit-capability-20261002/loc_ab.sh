#!/bin/bash
# Localization A/B, queued after grpo_mt.sh: mixed adapter (lora_serve) and gpt-oss:20b (Ollama), each on the same
# frozen explain tasks with and without the localization block; then compile-graded comparison.
set -uo pipefail
P=$HOME/decomp/experiments/edit-capability-20261002
M=$P/grpo-mt-v1
O=$P/loc-ab
L=$P/logic-pilot-v3
REPO=/mnt/c/Code/gameDecomp
HERE=$REPO/eval/results/edit-capability-20261002
PY=$HOME/decomp/sbk1/.venv/bin/python
log() { echo "[$(date '+%F %T')] $*" | tee -a "$O/ab.log"; }
fail() { log "FAILED: $*"; echo "$*" > "$O/FAILED"; exit 1; }

while ps -eo args | grep -v grep | grep -q 'grpo_mt.sh'; do sleep 60; done
[ -f "$M/done" ] || log "note: multi-turn pilot ended without done; continuing with the A/B"
for pid in $(pgrep -f "tools.lora_serve.server"); do kill "$pid"; done; sleep 10

(cd "$REPO" && SOLVER_VLLM_GPU_UTILIZATION=0.70 SOLVER_VLLM_MAX_SEQS=4 setsid nohup bash .cache/recon/serve_lowfootprint.sh \
   --adapter "mixed=$L/adapter_mixed" > "$O/serve.log" 2>&1 < /dev/null &)
for _ in $(seq 120); do curl -sf http://127.0.0.1:8101/v1/models > /dev/null && break; sleep 10; done
curl -sf http://127.0.0.1:8101/v1/models > /dev/null || fail "lora server did not start"
cd "$HERE"
for v in plain loc; do
  $PY loc_ab.py run --arm lora:mixed --variant $v > "$O/run_mixed_$v.log" 2>&1 || fail "mixed $v"
  log "mixed $v done"
done
for pid in $(pgrep -f "tools.lora_serve.server"); do kill "$pid"; done; sleep 15
for v in plain loc; do
  $PY loc_ab.py run --arm ollama:gpt-oss:20b --variant $v > "$O/run_gptoss_$v.log" 2>&1 || fail "gpt-oss $v"
  log "gpt-oss $v done"
done
$PY loc_ab.py grade > "$O/grade.txt" 2>&1 || fail "grade"
log "done"; echo done > "$O/done"
