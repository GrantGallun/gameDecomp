"""What does `workspace.score` itself report about rmonPrintf? The receipt, not a summary.

`eval/intake_control.py` builds its verdict from `solver.workspace.Attempt`, whose `verification` is the
in-memory certificate. The stored `rmonPrintf.verification.json` on disk says
`function_boundary.function_exact: true` with `status: function_exact_pending_integration`, which is a
ROM-backed byte certificate for the function's own extent. If the fresh verdict agrees, then a compiled
function with certified-correct bytes is being counted as a failure by every harness that keys on
`attempt.exact` -- and `Attempt.exact` is `verification["exact"]`, which the function-boundary path does
not set.

This prints the fields that decide it, with no truncation.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                          # noqa: E402

sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
from _placeholder_rules import union                                   # noqa: E402

KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

conn = sqlite3.connect(str(KB))
try:
    for name in ("rmonPrintf", "osSyncPrintf"):
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"{name}: {why}")
            continue
        source, tokens = union(context.candidate or "")
        verdict = context.compile_fn(source)
        verification = verdict.get("verification") or {}
        boundary = verification.get("function_boundary") or {}
        print("=" * 78)
        print(f"{name}   placeholders={tokens}")
        print("=" * 78)
        print(f"  attempt.exact      : {verdict.get('exact')}")
        print(f"  score              : {verdict.get('score')}")
        print(f"  certificate status : {verification.get('status')}")
        print(f"  normalized_exact   : {verification.get('normalized_assembly_exact')}")
        print(f"  boundary.function_exact : {boundary.get('function_exact')}")
        print(f"  boundary.status         : {boundary.get('status')}")
        print(f"  boundary.error          : {boundary.get('error')}")
        print(f"  boundary keys           : {sorted(boundary)}")
        print(f"  boundary.size           : {boundary.get('size')}")
        print(f"  diff length        : {len(verdict.get('diff') or '')}")
        print(f"  stderr length      : {len(verdict.get('stderr') or '')}")

        # The campaign's own reading of the same receipt, for comparison.
        from eval.completion_campaign import integration_ready
        print(f"  campaign would set  : "
              f"{'function_exact_pending_integration' if boundary.get('function_exact') else 'not-exact'}")
        try:
            print(f"  integration_ready   : {integration_ready(source, name)}")
        except Exception as exc:                                    # noqa: BLE001
            print(f"  integration_ready   : raised {type(exc).__name__}: {exc}")
        print()
finally:
    conn.close()
