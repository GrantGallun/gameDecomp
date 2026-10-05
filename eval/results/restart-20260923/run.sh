#!/bin/bash
# long arm (96 from the frozen sources), then restart rounds 2 and 3 (32 each from the previous best).
set -eu
H=/mnt/c/Code/gameDecomp/eval/results/restart-20260923
E=$HOME/decomp/experiments
cd "$H"
RESTART_STARTS=/mnt/c/Code/gameDecomp/eval/results/population-transfer-20260922/population.json \
RESTART_NATIVE=$E/restart-long-20260923 RESTART_REPORT=$H/report-long.json RESTART_BUDGET=96 \
  python3 round.py --jobs 4 > $E/restart-long.log 2>&1
python3 starts.py $E/locality-population-20260923/rows $H/starts-round2.json
RESTART_STARTS=$H/starts-round2.json RESTART_NATIVE=$E/restart-round2-20260923 RESTART_REPORT=$H/report-round2.json \
  RESTART_BUDGET=32 python3 round.py --jobs 4 > $E/restart-round2.log 2>&1
python3 starts.py $E/restart-round2-20260923/rows $H/starts-round3.json
RESTART_STARTS=$H/starts-round3.json RESTART_NATIVE=$E/restart-round3-20260923 RESTART_REPORT=$H/report-round3.json \
  RESTART_BUDGET=32 python3 round.py --jobs 4 > $E/restart-round3.log 2>&1
echo done
