#!/bin/bash
set -eu
H=/mnt/c/Code/gameDecomp/eval/results/at-inline-20260924
P=/mnt/c/Code/gameDecomp/eval/results/branch-shape-population-20260924
E=$HOME/decomp/experiments
cd "$H"
python3 run.py --jobs 4 > "$E/at-inline-20260924.log" 2>&1
ROUND_FREEZE=$H/freeze.json ROUND_ARM=at_inline \
RESTART_STARTS=$P/starts-round4.json RESTART_NATIVE=$E/at-inline-round4 RESTART_REPORT=$H/report-round4.json \
  RESTART_BUDGET=32 python3 round4.py --jobs 4 > "$E/at-inline-round4.log" 2>&1
echo done
