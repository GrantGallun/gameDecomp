#!/bin/bash
# Run repro_park.py for each function given; prints the tail of each run.
cd /mnt/c/Code/gameDecomp || exit 1
for function in "$@"; do
    echo "== $function"
    nice -n 5 /home/grant/decomp/sbk1/.venv/bin/python eval/results/gap-census-20260914/repro_park.py "$function" 2>&1 | tail -14
done
