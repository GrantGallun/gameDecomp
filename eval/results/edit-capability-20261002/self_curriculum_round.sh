#!/bin/bash
# One self-curriculum round (self_curriculum.py): propose (Ollama proposer, anonymized functions) -> free the GPU ->
# serve the solver adapter -> learnability filter -> examiner data. Fail-closed: OUT/FAILED names the stage.
# Usage: self_curriculum_round.sh OUT SOLVER_ADAPTER_PATH [PROPOSER] [PER_CLASS]
set -uo pipefail
OUT=$1
ADAPTER=$2
PROPOSER=${3:-ollama:gpt-oss:20b}
PER_CLASS=${4:-40}
HERE=/mnt/c/Code/gameDecomp/eval/results/edit-capability-20261002
PY=$HOME/decomp/sbk1/.venv/bin/python
log() { echo "[$(date '+%F %T')] $*" | tee -a "$OUT/round.log"; }
fail() { log "FAILED: $*"; echo "$*" > "$OUT/FAILED"; pkill -f 'tools.lora_serve.server'; exit 1; }
rm -f "$OUT/FAILED" "$OUT/round.done"
cd "$HERE" || fail "cd"

if [ ! -f "$OUT/propose.json" ]; then
  log "propose with $PROPOSER, $PER_CLASS per class, anonymized"
  $PY self_curriculum.py propose --out "$OUT" --proposer "$PROPOSER" --per-class "$PER_CLASS" --anonymize \
      > "$OUT/propose.log" 2>&1 || fail "propose (see propose.log)"
fi
# Free the GPU from the proposer before the solver server starts (one GPU job at a time).
GW=$(ip route show default | awk '{print $3}')
curl -s "http://$GW:11434/api/generate" -d '{"model":"gpt-oss:20b","keep_alive":0}' > /dev/null || true
sleep 10

if [ ! -f "$OUT/filter.json" ]; then
  log "serve solver $ADAPTER"
  (cd /mnt/c/Code/gameDecomp && SOLVER_VLLM_GPU_UTILIZATION=0.70 SOLVER_VLLM_MAX_SEQS=16 SOLVER_VLLM_CONTINUOUS=1 \
     SOLVER_VLLM_EAGER=0 SOLVER_VLLM_LORA_RANK=8 SOLVER_VLLM_BATCHED_TOKENS=4096 SOLVER_VLLM_KV_GIB=2.5 \
     setsid nohup bash .cache/recon/serve_lowfootprint.sh --adapter "solver=$ADAPTER" > "$OUT/serve.log" 2>&1 < /dev/null &)
  for _ in $(seq 120); do curl -sf http://127.0.0.1:8101/v1/models > /dev/null && break; sleep 5; done
  curl -sf http://127.0.0.1:8101/v1/models > /dev/null || fail "solver server did not start (see serve.log)"
  log "filter (k=4)"
  $PY self_curriculum.py filter --out "$OUT" --solver solver --k 4 > "$OUT/filter.log" 2>&1 || fail "filter"
  pkill -f 'tools.lora_serve.server'; sleep 10
fi
$PY self_curriculum.py examiner --out "$OUT" > "$OUT/examiner.log" 2>&1 || fail "examiner"
log "done"
echo done > "$OUT/round.done"
