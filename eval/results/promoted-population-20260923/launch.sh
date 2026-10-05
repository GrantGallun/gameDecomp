#!/bin/bash
set -eu
N="$HOME/decomp/experiments/promoted-population-20260923"
rm -rf "$N/rows" "$N/worlds" "$N/ws" "$N/confirm" "$N"/attempts.sqlite*
rm -f /mnt/c/Code/gameDecomp/eval/results/promoted-population-20260923/report-partial.json
cd /mnt/c/Code/gameDecomp/eval/results/promoted-population-20260923
python3 run.py --jobs 4 > "$N/run.log" 2>&1
tail -3 "$N/run.log"
