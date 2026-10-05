#!/bin/bash
set -euo pipefail
cd /mnt/c/Code/gameDecomp/eval/results/edit-capability-20261002
PY=/home/grant/decomp/sbk1/.venv/bin/python
O=/home/grant/decomp/experiments/edit-capability-20261002/public
P=/home/grant/decomp/experiments/edit-capability-20261002/logic-pilot-smoke-resume
mkdir -p "$P"
$PY logic_grade.py "$O/logic-smoke/tasks.jsonl" --self-check --context "$O/ctx_smoke.jsonl" --out "$P/selfcheck.jsonl" --jobs 4
for arm in base base_fs; do
  answers=/tmp/smoke_answers.jsonl
  if [ "$arm" = base_fs ]; then answers=/tmp/smoke_answers_fs.jsonl; fi
  $PY logic_grade.py "$O/logic-smoke/tasks.jsonl" --answers "$answers" --exam /tmp/smoke_exam.json --context "$O/ctx_smoke.jsonl" --out "$P/grades_$arm.jsonl" --jobs 4 > "$P/grade_$arm.log"
  cat "$P/grade_$arm.log"
done
tail -4 "$O/context.log"
cat "$O/run_context.sh" "$O/run_export.sh"
