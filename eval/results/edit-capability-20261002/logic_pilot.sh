#!/bin/bash
# Compatibility entry point; process ownership and stage gates live in Python.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
exec "$HOME/decomp/sbk1/.venv/bin/python" "$HERE/logic_pilot.py" "$@"
