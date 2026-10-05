#!/bin/bash
# Both arms over the same 22 starts, budget 32 + regalloc_search 300, code-v15. Experiment DB only; no ledger.
set -eu
H=/mnt/c/Code/gameDecomp/eval/results/restored-holes-20260925
E=$HOME/decomp/experiments
cd "$H"
for ARM in control restored; do
  ROUND_FREEZE=$H/freeze.json ROUND_ARM=$ARM RESTART_STARTS=$H/starts.json RESTART_NATIVE=$E/restored-holes-$ARM \
  RESTART_REPORT=$H/report-$ARM.json RESTART_BUDGET=32 python3 run.py --jobs 4 > "$E/restored-holes-$ARM.log" 2>&1
done
echo done
