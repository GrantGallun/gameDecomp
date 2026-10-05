#!/bin/bash
# Run 1 (loss check, 224 frozen sources, code-v10), then run 2 (round 4 from round-3 bests, control and treatment).
set -eu
H=/mnt/c/Code/gameDecomp/eval/results/branch-shape-population-20260924
E=$HOME/decomp/experiments
cd "$H"
python3 run.py --jobs 4 > "$E/branch-shape-population-20260924.log" 2>&1
python3 ../restart-20260923/starts.py "$E/restart-round3-20260923/rows" "$H/starts-round4.json"
ROUND_FREEZE=/mnt/c/Code/gameDecomp/eval/results/locality-population-20260923/freeze.json ROUND_ARM=control \
RESTART_STARTS=$H/starts-round4.json RESTART_NATIVE=$E/branch-shape-round4-control RESTART_REPORT=$H/report-round4-control.json \
  RESTART_BUDGET=32 python3 round4.py --jobs 4 > "$E/branch-shape-round4-control.log" 2>&1
ROUND_FREEZE=$H/freeze.json ROUND_ARM=treatment \
RESTART_STARTS=$H/starts-round4.json RESTART_NATIVE=$E/branch-shape-round4-treatment RESTART_REPORT=$H/report-round4-treatment.json \
  RESTART_BUDGET=32 python3 round4.py --jobs 4 > "$E/branch-shape-round4-treatment.log" 2>&1
echo done
