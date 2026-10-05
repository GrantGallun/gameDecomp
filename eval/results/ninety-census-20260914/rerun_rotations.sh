#!/bin/bash
# Re-probe the makeFixedRotation* family with the array-decay fix, then certify every stack exact.
cd /mnt/c/Code/gameDecomp/eval/results/ninety-census-20260914 || exit 1
mkdir -p stack-probe-predecay
for f in stack-probe/makeFixedRotation*; do mv "$f" stack-probe-predecay/; done
PY=/home/grant/decomp/sbk1/.venv/bin/python
cd /mnt/c/Code/gameDecomp && nice "$PY" eval/results/ninety-census-20260914/probe_stack.py --workers 2 > eval/results/ninety-census-20260914/probe_stack3.log 2>&1
nice "$PY" eval/results/ninety-census-20260914/certify_stack.py > eval/results/ninety-census-20260914/certify_stack.log 2>&1
tail -1 eval/results/ninety-census-20260914/certify_stack.log
grep -v '"repair_complete": true' eval/results/ninety-census-20260914/certify_stack.log | cut -c1-240
