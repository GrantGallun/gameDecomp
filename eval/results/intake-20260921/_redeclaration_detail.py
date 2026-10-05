"""Why do four states still redeclare after `header_variant` reconciled?

`compile_recovery.header_variant` ends with `project_headers.reconcile_declarations(repo, changed)` and
reports what it removed, so the mechanism exists. Either it removes nothing on these states, or it removes
something and the redeclaration is of a different kind (a function definition vs a prototype, a typedef vs
a struct tag). This prints the reconciliation receipt and the offending pair for each of the four.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                      # noqa: E402
from eval.intake_runners import RUNNERS                                  # noqa: E402
from eval.tool_agent_run import build_context                            # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

classes = json.loads((BASE / "post-fix-residual-classes.json").read_text(encoding="utf-8"))
TARGETS = classes["classes"]["redeclaration against an injected header"]

conn = sqlite3.connect(str(KB))
try:
    for name in TARGETS:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"{name}: {why}")
            continue
        initial = dict(context.initial_verdict or {})
        current, best, stderr = context.candidate, initial, initial.get("stderr") or ""
        for label in SEQUENCE:
            if not gated(label, stderr):
                continue
            ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                  "initial_verdict": {**initial, "stderr": stderr}}
            try:
                result = RUNNERS[label](ns, {})
            except Exception:                                        # noqa: BLE001
                continue
            if result.get("changed") and label.endswith("header_variant"):
                print("=" * 78)
                print(f"{name}")
                print("=" * 78)
                print(f"  header_variant report:")
                print(f"    added      : {result.get('detail', {}).get('added')}")
                print(f"    reconciled : {result.get('detail', {}).get('reconciled')}")
                print(f"    removed    : {[i for i in (result.get('detail', {}).get('removed_headers') or [])]}")
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        print(f"  final first error: "
              f"{next((ln.strip() for ln in stderr.splitlines() if ln.strip()), '(none)')[:100]}")
        print(f"  full error list:")
        for line in stderr.splitlines()[:6]:
            print(f"     {line[:110]}")
        # the offending redeclaration, in the candidate
        print(f"  candidate's own declarations of the function name:")
        for index, line in enumerate(current.splitlines(), 1):
            if (name in line and "(" in line) or f"struct {name}" in line:
                print(f"     {index:>4}: {line.strip()[:100]}")
        print()
finally:
    conn.close()
