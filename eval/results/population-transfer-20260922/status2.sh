#!/bin/bash
# Read-only progress for stage 2.
L="$HOME/decomp/experiments/population-transfer-20260922/stage2"
echo "rows: $(ls "$L/rows" 2>/dev/null | wc -l) / 448   C: free: $(df -h /mnt/c | awk 'NR==2 {print $4}')"
grep -E '"exact": true|raised|stopped|Error|Traceback' "$L/run2.log" | cut -c1-160
tail -1 "$L/run2.log" | cut -c1-160
