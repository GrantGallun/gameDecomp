#!/bin/bash
# Paired: restored stream on code-v15 vs the same stream on code-v16 (+pure_inline), all unsolved starts, budget 32,
# no regalloc stage (it moved nothing on the 22-function run). Experiment DBs only.
set -eu
H=/mnt/c/Code/gameDecomp/eval/results/restored-holes-20260925
E=$HOME/decomp/experiments
cd "$H"
for PAIR in "restored:freeze.json" "pure_inline:freeze-v16.json"; do
  ARM=${PAIR%%:*}; FZ=${PAIR#*:}
  ROUND_FREEZE=$H/$FZ ROUND_ARM=$ARM RESTART_STARTS=$H/starts-all.json RESTART_NATIVE=$E/restored-holes-pop-$ARM \
  RESTART_REPORT=$H/report-pop-$ARM.json RESTART_BUDGET=32 REGALLOC_BUDGET=0 python3 run.py --jobs 4 \
    > "$E/restored-holes-pop-$ARM.log" 2>&1
done
echo done
