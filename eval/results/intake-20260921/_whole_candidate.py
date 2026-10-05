"""Print the whole candidate for one unclassified state. It is 755 characters; there is no excuse for
reasoning about a syntax error from a line number.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                      # noqa: E402
from eval.intake_runners import RUNNERS                                  # noqa: E402
from eval.tool_agent_run import build_context                            # noqa: E402

KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

conn = sqlite3.connect(str(KB))
try:
    for name in ("clearRaceReplayCourseGrid", "initRaceCourseSceneryObjects"):
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
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        print("=" * 78)
        print(f"{name} — the candidate the sequence ends on, in full")
        print("=" * 78)
        for index, line in enumerate(current.splitlines(), 1):
            print(f"  {index:>4} | {line}")
        print("\n  compiler said:")
        for line in stderr.splitlines():
            print(f"    {line}")
        print()
finally:
    conn.close()
