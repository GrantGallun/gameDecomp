#!/bin/bash
# Compare main-tree files with the frozen live campaign copies (line endings normalized).
cd /mnt/c/Code/gameDecomp || exit 1
for f in "$@"; do
    live="eval/results/resume-pipeline-20260908/code/$f"
    if [ ! -e "$live" ]; then echo "MISSING-LIVE $f"; continue; fi
    if diff -q <(tr -d '\r' < "$f") <(tr -d '\r' < "$live") > /dev/null; then echo "same $f"; else echo "DIFF $f"; fi
done
