#!/bin/bash
# Third arm (local_webs, code-v17) over the same starts; waits for the pure_inline arm to finish first.
set -eu
H=/mnt/c/Code/gameDecomp/eval/results/restored-holes-20260925
E=$HOME/decomp/experiments
cd "$H"
until grep -q '"done": true' "$E/restored-holes-pop-pure_inline.log" 2>/dev/null; do sleep 30; done
ROUND_FREEZE=$H/freeze-v17.json ROUND_ARM=local_webs RESTART_STARTS=$H/starts-all.json \
RESTART_NATIVE=$E/restored-holes-pop-local_webs RESTART_REPORT=$H/report-pop-local_webs.json RESTART_BUDGET=32 \
REGALLOC_BUDGET=0 python3 run.py --jobs 4 > "$E/restored-holes-pop-local_webs.log" 2>&1
echo done
