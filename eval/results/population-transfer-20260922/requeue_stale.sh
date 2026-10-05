#!/bin/bash
# Quarantine the 7 functions whose resumed baseline hit a stale workspace, so run.py reruns them.
# A script file on purpose: `wsl.exe ... bash -c '...'` re-expands $vars before bash sees them.
set -eu
EXP="$HOME/decomp/experiments/population-transfer-20260922"
cd "$EXP"
echo "state before: rows=$(ls rows | wc -l) worlds=$(ls worlds | wc -l) ws=$(ls ws 2>/dev/null | wc -l) db=$(stat -c %s attempts.sqlite)"
mkdir -p rows-quarantine
for f in osCreateViManager osEPiRawReadIo osEPiRawWriteIo osGetThreadPri osPfsIsPlug osPfsRepairId osPiRawStartDma; do
  for arm in control expanded; do
    [ -f "rows/$f--$arm.json" ] && mv "rows/$f--$arm.json" "rows-quarantine/$f--$arm.stale-ws.json"
  done
  [ -n "$f" ] && rm -rf "ws/${f:?}"
done
echo "state after: rows=$(ls rows | wc -l) quarantined=$(ls rows-quarantine | wc -l)"
