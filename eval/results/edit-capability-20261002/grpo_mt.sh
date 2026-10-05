#!/bin/bash
# Multi-turn GRPO, queued after grpo_pilot.sh: same start (mixed SFT adapter), same steps/twins/lr, but explain
# episodes get up to 3 attempts with compiler feedback. Then a 3-turn exam (explain tasks get feedback; other kinds
# answer once) for mixed, grpo (single-turn RL) and grpo_mt, plus grpo_mt's single-turn exam.
set -euo pipefail
P=$HOME/decomp/experiments/edit-capability-20261002
L=$P/logic-pilot-v3
G=$P/grpo-v1
M=$P/grpo-mt-v1
CTX=$P/public/context-v3.jsonl
REPO=/mnt/c/Code/gameDecomp
HERE=$REPO/eval/results/edit-capability-20261002
PY=$HOME/decomp/sbk1/.venv/bin/python
TPY=$HOME/decomp/train-venv/bin/python
mkdir -p "$M"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$M/pilot.log"; }
fail() { log "FAILED: $*"; echo "$*" > "$M/FAILED"; exit 1; }
trap 'fail "line $LINENO exited $?"' ERR

while [ ! -f "$G/done" ]; do
  pgrep -f grpo_pilot.sh > /dev/null || fail "single-turn pilot ended without done"
  sleep 60
done

train() {   # $1 = output dir, then extra flags
  local out=$1; shift
  (cd "$REPO" && SOLVER_GPU_MEMORY_FRACTION=0.70 $TPY -m eval.train_grpo --base "$HOME/decomp/models/qwen2.5-coder-7b" \
     --init-adapter "$L/adapter_mixed" --tasks "$L/logic-v3/tasks.jsonl" --context "$CTX" \
     --out "$out" --steps 150 --group 4 --prompts 2 --twin-share 0.5 --turns 3 --max-seconds 10800 "$@" \
     > "$out.log" 2>&1)
}
log "train (3 turns, batched generation)"
ADAPTER=$M/adapter
if ! train "$ADAPTER"; then
  log "batched training failed: $(tail -c 600 "$ADAPTER.log" | tr '\n' ' ')"
  log "retrying once with --no-batch-gen"
  ADAPTER=$M/adapter_nobatch
  train "$ADAPTER" --no-batch-gen || fail "training failed twice; see $ADAPTER.log"
fi
log "trained: $(grep -o '"stop_reason": "[a-z]*"' "$ADAPTER/grpo_receipt.json"); fallbacks: $(grep -c gen_fallback "$ADAPTER/steps.jsonl" || true)"

(cd "$REPO" && SOLVER_VLLM_GPU_UTILIZATION=0.70 SOLVER_VLLM_MAX_SEQS=4 setsid nohup bash .cache/recon/serve_lowfootprint.sh \
   --adapter "mixed=$L/adapter_mixed" --adapter "grpo=$G/adapter" --adapter "grpo_mt=$ADAPTER" \
   > "$M/serve.log" 2>&1 < /dev/null &)
for _ in $(seq 120); do curl -sf http://127.0.0.1:8101/v1/models > /dev/null && break; sleep 10; done
for arm in mixed grpo grpo_mt; do
  (cd "$REPO" && $PY -m eval.logic_exam run "$L/exam.json" --arm "$arm" --out "$M/answers_${arm}_t3.jsonl" \
     --turns 3 --context "$CTX" --jobs 4 > "$M/exam_${arm}_t3.log" 2>&1)
  log "exam $arm (3 turns): $(tail -1 "$M/exam_${arm}_t3.log")"
done
(cd "$REPO" && $PY -m eval.logic_exam run "$L/exam.json" --arm grpo_mt --out "$M/answers_grpo_mt_t1.jsonl" \
   --jobs 4 > "$M/exam_grpo_mt_t1.log" 2>&1)
for pid in $(pgrep -f "tools.lora_serve.server"); do kill "$pid"; done

cd "$HERE"
for name in mixed_t3 grpo_t3 grpo_mt_t3 grpo_mt_t1; do
  $PY logic_grade.py "$L/logic-v3/tasks.jsonl" --context "$CTX" --answers "$M/answers_$name.jsonl" \
    --exam "$L/exam.json" --out "$M/grades_$name.jsonl" --jobs 6 > "$M/grade_$name.log" 2>&1
done
$PY logic_compare.py "$L/grades_mixed.jsonl" "$M/grades_mixed_t3.jsonl" "$M/grades_grpo_t3.jsonl" \
  "$M/grades_grpo_mt_t3.jsonl" "$M/grades_grpo_mt_t1.jsonl" > "$M/compare_vs_mixed_t1.txt"
$PY logic_compare.py "$M/grades_mixed_t3.jsonl" "$M/grades_grpo_t3.jsonl" "$M/grades_grpo_mt_t3.jsonl" \
  > "$M/compare_vs_mixed_t3.txt"
$PY - "$M" <<'PYEOF' > "$M/turns.txt"
import json, sys, collections
M = sys.argv[1]
for arm in ("mixed", "grpo", "grpo_mt"):
    rows = [json.loads(l) for l in open(f"{M}/answers_{arm}_t3.jsonl")]
    grades = {json.loads(l)["id"]: json.loads(l) for l in open(f"{M}/grades_{arm}_t3.jsonl")}
    explain = [r for r in rows if r["id"].startswith("logic-explain")]
    solved_at = collections.Counter(r.get("turns", 1) for r in explain if grades[r["id"]]["rows"])
    print(arm, "explain n", len(explain), "solved by turn:", dict(sorted(solved_at.items())),
          "mean turns", round(sum(r.get("turns", 1) for r in explain) / max(1, len(explain)), 2))
PYEOF
log "done"
echo done > "$M/done"
