#!/bin/bash
# Move non-compiling draft-lowering results aside and rerun the probe with the current module.
cd /mnt/c/Code/gameDecomp/eval/results/ninety-census-20260914 || exit 1
mkdir -p draft-lowering-pass1
for f in draft-lowering/*.json; do
    if ! grep -q '"outcome": "compiles"' "$f"; then mv "$f" draft-lowering-pass1/; fi
done
cd /mnt/c/Code/gameDecomp && nice /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/probe_draft_lowering.py --workers 2 \
    > eval/results/ninety-census-20260914/draft_lowering2.log 2>&1
tail -28 eval/results/ninety-census-20260914/draft_lowering2.log
