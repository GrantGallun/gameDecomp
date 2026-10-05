#!/bin/bash
# After the web A/B: sleep/wake smoke test (must PASS), then sequential examiner RL. One GPU job at a time.
set -uo pipefail
P=$HOME/decomp/experiments/edit-capability-20261002
O=$P/examiner-rl-v1
mkdir -p "$O"
until [ -f "$P/web-exam-v1/done" ] || [ -f "$P/web-exam-v1/FAILED" ]; do sleep 60; done
sleep 30
PY=$HOME/decomp/sbk1/.venv/bin/python
HERE=/mnt/c/Code/gameDecomp/eval/results/edit-capability-20261002
# Round 1's filter failed on an over-long prompt (fixed); rerun the round (propose is kept, filter + examiner run).
if [ ! -f "$P/self-curriculum-v1/round.done" ]; then
  bash "$HERE/self_curriculum_round.sh" "$P/self-curriculum-v1" "$P/reading-v1/adapter_read" \
      > "$P/self-curriculum-v1/round2.out" 2>&1 || { echo "round rerun failed" > "$O/FAILED"; exit 1; }
fi
if ! $PY /mnt/c/Code/gameDecomp/.cache/recon/sleep_wake_smoke.py > "$O/smoke.log" 2>&1; then
  echo "sleep/wake smoke test failed (smoke.log)" > "$O/FAILED"; exit 1
fi
cd /mnt/c/Code/gameDecomp/eval/results/edit-capability-20261002 || exit 1
$PY examiner_rl.py --out "$O" --solver "$P/reading-v1/adapter_read" --weak "$P/self-curriculum-v1/weak.json" \
    --cycles 10 --prompts 16 --group 4 > "$O/run.log" 2>&1
