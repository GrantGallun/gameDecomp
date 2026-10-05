#!/bin/bash
# GRPO pilot: start from the logic pilot's mixed SFT adapter, RL with compiler rewards on TRAIN tasks (half twins),
# then the SAME frozen exam, graded and compared against base and against the mixed adapter it started from.
set -euo pipefail
P=$HOME/decomp/experiments/edit-capability-20261002
L=$P/logic-pilot-v3
G=$P/grpo-v1
REPO=/mnt/c/Code/gameDecomp
HERE=$REPO/eval/results/edit-capability-20261002
PY=$HOME/decomp/sbk1/.venv/bin/python
TPY=$HOME/decomp/train-venv/bin/python
mkdir -p "$G"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$G/pilot.log"; }

log "train"
(cd "$REPO" && SOLVER_GPU_MEMORY_FRACTION=0.70 $TPY -m eval.train_grpo --base "$HOME/decomp/models/qwen2.5-coder-7b" \
   --init-adapter "$L/adapter_mixed" --tasks "$L/logic-v3/tasks.jsonl" --context "$P/public/context-v3.jsonl" \
   --out "$G/adapter" --steps 150 --group 4 --prompts 2 --twin-share 0.5 --max-seconds 7200 > "$G/train.log" 2>&1)
log "trained: $(grep -o '"stop_reason": "[a-z]*"' "$G/adapter/grpo_receipt.json")"

log "serve"
(cd "$REPO" && SOLVER_VLLM_GPU_UTILIZATION=0.70 SOLVER_VLLM_MAX_SEQS=4 setsid nohup bash .cache/recon/serve_lowfootprint.sh \
   --adapter "grpo=$G/adapter" > "$G/serve.log" 2>&1 < /dev/null &)
for _ in $(seq 120); do curl -sf http://127.0.0.1:8101/v1/models > /dev/null && break; sleep 10; done
(cd "$REPO" && $PY -m eval.logic_exam run "$L/exam.json" --arm grpo --out "$G/answers_grpo.jsonl" --jobs 4 > "$G/exam.log" 2>&1)
for pid in $(pgrep -f "tools.lora_serve.server"); do kill "$pid"; done
log "exam: $(tail -1 "$G/exam.log")"

cd "$HERE"
$PY logic_grade.py "$L/logic-v3/tasks.jsonl" --context "$P/public/context-v3.jsonl" --answers "$G/answers_grpo.jsonl" \
  --exam "$L/exam.json" --out "$G/grades_grpo.jsonl" --jobs 6 > "$G/grade.log" 2>&1
$PY logic_compare.py "$L/grades_base.jsonl" "$G/grades_grpo.jsonl" > "$G/compare_vs_base.txt"
$PY logic_compare.py "$L/grades_mixed.jsonl" "$G/grades_grpo.jsonl" > "$G/compare_vs_mixed.txt"
$PY logic_compare.py "$L/grades_mixed.jsonl" "$G/grades_grpo.jsonl" --metric label > "$G/compare_vs_mixed_label.txt"
log "done"
echo done > "$G/done"
