#!/usr/bin/env bash
# Run a solver script with the GPU deliberately under-driven, so the machine
# stays usable (video playback, desktop) while work continues in the background.
#
#   ./run-throttled.sh entryT1.py [args...]
#
# SOLVER_NUM_GPU=24  keeps 24 of ~33 layers on the GPU; the rest run on CPU.
#                    Roughly 2-3x slower, but the GPU is no longer pinned.
# SOLVER_GAP_MS=1500 idles 1.5s after each call so the compositor gets a
#                    regular window instead of one solid block of work.
#
# Raise SOLVER_NUM_GPU (or unset both) to go fast again.
export SOLVER_NUM_GPU="${SOLVER_NUM_GPU:-24}"
export SOLVER_GAP_MS="${SOLVER_GAP_MS:-1500}"
echo "throttled: NUM_GPU=$SOLVER_NUM_GPU GAP_MS=$SOLVER_GAP_MS"
exec python3 "$@"
