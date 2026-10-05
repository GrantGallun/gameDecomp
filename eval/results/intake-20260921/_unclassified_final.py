"""The `unclassified` class, read on the final candidate: what are the 14 states actually failing on?

A class named `unclassified` is a statement about the classifier. The traced payload stores per-step CLASS
names but the MESSAGES only where the action's detail was recorded, which was the old mid-sequence read --
so the messages have to be taken again on the final candidate. That is the same point-in-the-pipeline
lesson this round is about, applied to the diagnostic text rather than to its class.

Read-only apart from the attempts the oracle logs.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import frontend_diagnostics as frontend                        # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

traced = json.loads((BASE / "wide-intake-traced.json").read_text(encoding="utf-8"))
targets = [row["function"] for row in traced["rows"]
           if not row["sequence"]["compiled"]
           and (row.get("diagnostic_trace") or [{}])[-1].get("classes") == ["unclassified"]]

conn = sqlite3.connect(str(KB))
messages: Counter = Counter()
try:
    for name in targets:
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
            except Exception:                                          # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        report = frontend.analyse(current, repo=REPO, target=str(context.target or ""))
        errors = report.get("errors") or []
        print(f"\n{name}  ({len(current)} chars, clang={report.get('status')}, "
              f"{len(errors)} error(s))")
        for error in errors[:4]:
            print(f"   {error['line']}:{error['column']} {error['what'][:96]}")
            messages[error["what"][:70]] += 1
        # The offending line, since a message alone has misled this session twice.
        lines = current.splitlines()
        for error in errors[:2]:
            index = error["line"] - 1
            if 0 <= index < len(lines):
                print(f"      line {error['line']}: {lines[index].strip()[:96]}")
        cfe_first = next((ln.strip() for ln in stderr.splitlines() if ln.strip()), "")
        print(f"   cfe says: {cfe_first[:96] or '(none)'}")
finally:
    conn.close()

print(f"\nthe messages across all {len(targets)} states:")
for text, count in messages.most_common(16):
    print(f"  {count:4}  {text}")
