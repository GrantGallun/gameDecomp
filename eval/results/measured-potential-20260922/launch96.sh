#!/bin/bash
# Clear the two-function smoke run, then run the pre-registered budget-96 arm over all 216 functions.
set -eu
N="$HOME/decomp/experiments/measured-potential-20260922"
rm -rf "$N/rows" "$N/worlds" "$N/ws" "$N/confirm" "$N/attempts.sqlite" "$N/attempts.sqlite-wal" "$N/attempts.sqlite-shm"
rm -f /mnt/c/Code/gameDecomp/eval/results/measured-potential-20260922/report96-partial.json
cd /mnt/c/Code/gameDecomp/eval/results/measured-potential-20260922
python3 run96.py --jobs 4 > "$N/run96.log" 2>&1
tail -3 "$N/run96.log"
