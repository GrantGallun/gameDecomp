#!/usr/bin/env bash
# Operator commands for gameDecomp; run inside WSL. `./gd --help` lists them, docs/RUNNING.md explains them.
# Uses the first Python found: $GAMEDECOMP_PYTHON, the target decomp's .venv (where the pipeline's
# requirements are installed, docs/RUNNING.md step 3), or this repository's .venv.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
home="${GAMEDECOMP_HOME:-$HOME/decomp}"
target="${GAMEDECOMP_TARGET:-$home/sbk1}"
for candidate in "${GAMEDECOMP_PYTHON:-}" "$target/.venv/bin/python" "$root/.venv/bin/python"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then python="$candidate"; break; fi
done
python="${python:-python3}"
cd "$root"
exec "$python" -m eval.control "$@"
