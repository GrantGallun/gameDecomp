#!/bin/bash
# Launch the stack-layout probe detached; log to probe_stack.log.
cd /mnt/c/Code/gameDecomp || exit 1
setsid nohup nice /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/probe_stack.py --workers 2 \
    > eval/results/ninety-census-20260914/probe_stack.log 2>&1 < /dev/null &
echo "pid $!"
