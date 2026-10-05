#!/bin/bash
# List running census probe processes and log tails.
pgrep -af "probe_stack|diff_pass|ninety-census" | grep -v pgrep
cd /mnt/c/Code/gameDecomp/eval/results/ninety-census-20260914 || exit 1
for log in *.log; do echo "== $log"; tail -3 "$log" | cut -c1-240; done
ls -d stack-probe 2>/dev/null && ls stack-probe | wc -l
