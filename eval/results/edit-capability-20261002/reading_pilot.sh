#!/bin/bash
# Reading pilot (2026-10-04): does practice on compiler-labelled reading tasks fix missing-statement repairs?
# Arms (equal example counts, same recipe as the logic pilot's mixed arm, trained from the base model):
#   read = mixed + reading tasks (reading_tasks.py)      more = mixed + the same number of further logic-v3 tasks
# plus the existing mixed adapter. Exams: the frozen logic-v3 exam (unchanged) and a frozen reading exam.
set -uo pipefail
P=$HOME/decomp/experiments/edit-capability-20261002
L=$P/logic-pilot-v3
R=$P/public/reading-v1
O=$P/reading-v1
REPO=/mnt/c/Code/gameDecomp
HERE=$REPO/eval/results/edit-capability-20261002
PY=$HOME/decomp/sbk1/.venv/bin/python
TPY=$HOME/decomp/train-venv/bin/python
BASE=$HOME/decomp/models/qwen2.5-coder-7b
mkdir -p "$O"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$O/pilot.log"; }
fail() { log "FAILED: $*"; echo "$*" > "$O/FAILED"; for pid in $(pgrep -f "tools.lora_serve.server"); do kill "$pid"; done; exit 1; }
rm -f "$O/FAILED"

cd "$HERE"
if [ ! -f "$O/selfcheck.ok" ]; then
  $PY logic_grade.py "$R/tasks.jsonl" --context "$P/public/reading-v1.jsonl" --self-check --jobs 6 \
      > "$O/selfcheck.log" 2>&1 || fail "self-check (see selfcheck.log)"
  touch "$O/selfcheck.ok"; log "self-check passed"
fi
cd "$REPO"
[ -f "$O/reading_exam.json" ] || $PY -m eval.logic_exam freeze "$R/tasks.jsonl" --out "$O/reading_exam.json" \
    --splits exam,check --check-sample 600 > "$O/freeze.log" 2>&1 || fail "freeze"
[ -f "$O/arms/arms.json" ] || $TPY "$HERE/reading_arms.py" --mixed "$L/arms/mixed.jsonl" \
    --logic "$L/logic-v3/tasks.jsonl" --reading "$R/tasks.jsonl" --out "$O/arms" --base "$BASE" \
    > "$O/arms.log" 2>&1 || fail "arms"
log "arms built"

for arm in read more; do
  [ -f "$O/adapter_$arm/training_receipt.json" ] && continue
  n=$(wc -l < "$O/arms/$arm.jsonl")
  SOLVER_GPU_MEMORY_FRACTION=0.70 $TPY -m eval.train_source_repair --base "$BASE" --tasks "$O/arms/$arm.jsonl" \
      --out "$O/adapter_$arm" --max-steps $((n / 4)) --max-examples "$n" --max-seconds 16000 --max-seq-len 3072 \
      --completion-logits > "$O/train_$arm.log" 2>&1 || fail "train $arm"
  [ -f "$O/adapter_$arm/training_receipt.json" ] || fail "train $arm wrote no receipt"
  log "trained $arm ($n examples)"
done

for pid in $(pgrep -f "tools.lora_serve.server"); do kill "$pid"; done; sleep 10
(cd "$REPO" && SOLVER_VLLM_GPU_UTILIZATION=0.70 SOLVER_VLLM_MAX_SEQS=4 setsid nohup bash .cache/recon/serve_lowfootprint.sh \
   --adapter "mixed=$L/adapter_mixed" --adapter "read=$O/adapter_read" --adapter "more=$O/adapter_more" \
   > "$O/serve.log" 2>&1 < /dev/null &)
for _ in $(seq 120); do curl -sf http://127.0.0.1:8101/v1/models > /dev/null && break; sleep 10; done
curl -sf http://127.0.0.1:8101/v1/models > /dev/null || fail "lora server did not start"
log "server up"

for arm in base mixed read more; do
  $PY -m eval.logic_exam run "$O/reading_exam.json" --arm $arm --out "$O/answers_reading_$arm.jsonl" --jobs 4 \
      > "$O/exam_reading_$arm.log" 2>&1 || fail "reading exam $arm"
  log "reading exam $arm done"
done
for arm in mixed read more; do
  $PY -m eval.logic_exam run "$L/exam.json" --arm $arm --out "$O/answers_logic_$arm.jsonl" --jobs 4 \
      > "$O/exam_logic_$arm.log" 2>&1 || fail "logic exam $arm"
  log "logic exam $arm done"
done
for pid in $(pgrep -f "tools.lora_serve.server"); do kill "$pid"; done

cd "$HERE"
for arm in base mixed read more; do
  $PY logic_grade.py "$R/tasks.jsonl" --context "$P/public/reading-v1.jsonl" --exam "$O/reading_exam.json" \
      --answers "$O/answers_reading_$arm.jsonl" --out "$O/grades_reading_$arm.jsonl" --jobs 6 \
      > "$O/grade_reading_$arm.log" 2>&1 || fail "grade reading $arm"
done
for arm in mixed read more; do
  $PY logic_grade.py "$L/logic-v3/tasks.jsonl" --context "$P/public/context-v3.jsonl" --exam "$L/exam.json" \
      --answers "$O/answers_logic_$arm.jsonl" --out "$O/grades_logic_$arm.jsonl" --jobs 6 \
      > "$O/grade_logic_$arm.log" 2>&1 || fail "grade logic $arm"
done
log "done"; echo done > "$O/done"
