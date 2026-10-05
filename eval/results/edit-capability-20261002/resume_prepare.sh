#!/bin/bash
set -euo pipefail
cd /mnt/c/Code/gameDecomp
PY=/home/grant/decomp/sbk1/.venv/bin/python
TPY=/home/grant/decomp/train-venv/bin/python
BASE=/home/grant/decomp/models/qwen2.5-coder-7b
HERE=eval/results/edit-capability-20261002
O=/home/grant/decomp/experiments/edit-capability-20261002/public
P=/home/grant/decomp/experiments/edit-capability-20261002/logic-pilot-v3-smoke
mkdir -p "$P"
$PY -m eval.logic_tasks "$O/ctx_smoke.jsonl" --out "$P/data" > "$P/export.log"
$PY "$HERE/logic_grade.py" "$P/data/tasks.jsonl" --self-check --context "$O/ctx_smoke.jsonl" --out "$P/selfcheck.jsonl" --jobs 4
$TPY "$HERE/logic_arms.py" "$P/data/tasks.jsonl" --base "$BASE" --out "$P/arms" --cap 8 > "$P/arms.log"
$TPY -m eval.train_source_repair --base "$BASE" --tasks "$P/arms/mixed.jsonl" --out "$P/adapter_mixed" --dry-run
$TPY -m pytest tests/test_logic_pilot.py tests/test_logic_tasks.py tests/test_context_closure.py tests/test_repair_training_path.py tests/test_posttraining_safety.py -q --basetemp=/tmp/logic-pilot-v3-tests
