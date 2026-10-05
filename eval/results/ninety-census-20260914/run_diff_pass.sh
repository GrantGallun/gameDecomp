#!/bin/bash
# Launch the 90+ diff pass detached; log to diff_pass.log.
cd /mnt/c/Code/gameDecomp || exit 1
setsid nohup /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/diff_pass.py --workers 2 \
    > eval/results/ninety-census-20260914/diff_pass.log 2>&1 < /dev/null &
echo "pid $!"
