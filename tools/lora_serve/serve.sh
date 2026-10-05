#!/usr/bin/env bash
# Launcher for the local inference service.
#
#   tools/lora_serve/serve.sh run   [server args...]   # foreground (Ctrl-C stops)
#   tools/lora_serve/serve.sh start [server args...]   # detached: pidfile + log
#   tools/lora_serve/serve.sh stop
#   tools/lora_serve/serve.sh status
#
# BASE-ONLY by default: no adapter is registered, so every request serves the
# bare checkpoint. Set ADAPTER='m1=/path/to/adapter' to register one; it is then
# reachable only by its model id (default --default-adapter is 'none').
#
# Examples
#   PORT=8101 tools/lora_serve/serve.sh run                     # M0: base
#   PORT=8102 ADAPTER='m1=/path/adapters/x' tools/lora_serve/serve.sh run
#   PORT=8102 ADAPTER='m1=/path/x' DEFAULT_ADAPTER=m1 ...       # adapter arm
#
# Only ONE such server can hold the GPU at a time (5.4 GB of 4-bit weights each,
# 16 GB shared with another GPU user). Serve arms sequentially, or serve both
# arms from ONE process: the base id serves M0 and '<base>+m1' serves M1.
#
# NOTE on this WSL2 host: a process detached from a `wsl.exe ... bash -lc`
# session can be torn down when that session ends. `run` under a harness
# background job is the reliable launch here; `start` is for interactive shells.
set -euo pipefail

REPO=${REPO:-/mnt/c/Code/gameDecomp}
PYTHON=${PYTHON:-/home/grant/decomp/train-venv/bin/python}
MODEL=${MODEL:-/home/grant/decomp/models/qwen2.5-coder-7b}
ADAPTER=${ADAPTER:-}
DEFAULT_ADAPTER=${DEFAULT_ADAPTER:-none}
PORT=${PORT:-8101}
LOG=${LOG:-$HOME/lora-serve-$PORT.log}
PIDFILE=${PIDFILE:-$HOME/lora-serve-$PORT.pid}
export HF_HOME=${HF_HOME:-$HOME/decomp/hf-home}
# Fragment-free allocator: this card is shared (Windows-side ollama holds ~3.5 GB)
# and a burst of concurrent prefill otherwise fails to find a contiguous block.
export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}

DEFAULT_ARGS=(
  --model "$MODEL"
  --port "$PORT"
  --default-adapter "$DEFAULT_ADAPTER"
  --max-batch-size 6
  --max-batch-tokens 40000
  --batch-wait-ms 40
  --max-model-len 12288
  --receipt-log "$HOME/lora_serve_receipts.jsonl"
)
if [ -n "$ADAPTER" ]; then
  DEFAULT_ARGS+=(--adapter "$ADAPTER")
fi

cmd=${1:-run}
shift || true

case "$cmd" in
  run)
    cd "$REPO"
    exec "$PYTHON" -m tools.lora_serve.server "${DEFAULT_ARGS[@]}" "$@"
    ;;
  start)
    cd "$REPO"
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "already running: pid $(cat "$PIDFILE")" >&2
      exit 1
    fi
    setsid nohup "$PYTHON" -m tools.lora_serve.server "${DEFAULT_ARGS[@]}" "$@" \
      < /dev/null >> "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "started pid $(cat "$PIDFILE"), log $LOG"
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      pid=$(cat "$PIDFILE")
      kill "$pid" 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "stopped pid $pid"
    else
      echo "no pidfile at $PIDFILE" >&2
    fi
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "running pid $(cat "$PIDFILE")"
    else
      echo "not running"
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 {run|start|stop|status} [server args...]" >&2
    exit 2
    ;;
esac
