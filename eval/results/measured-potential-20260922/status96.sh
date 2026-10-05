#!/bin/bash
L="$HOME/decomp/experiments/measured-potential-20260922"
echo "rows: $(ls "$L/rows" 2>/dev/null | wc -l) / 216   C: free: $(df -h /mnt/c | awk 'NR==2 {print $4}')"
grep -E '"exact": true|raised|Traceback|Error' "$L/run96.log" | cut -c1-170
tail -1 "$L/run96.log" | cut -c1-170
