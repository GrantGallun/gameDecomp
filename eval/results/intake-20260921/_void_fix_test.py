"""Does `opaque_variant` now reach the class it documents?

TWO MISTAKES THIS AVOIDS, both made earlier in this session:

  1. the first in-process `void` test fed `opaque_variant` the RAW DRAFT, which fails at line 10 with
     `Syntax Error`. The mechanism never had a parsing candidate. Here it gets the FINAL candidate from the
     sequence, the state it is actually handed in production.
  2. the first test compared `plans` counts and stopped there. `plan` returning a plan is not the mechanism
     firing: `opaque_variant` also has to find a tag for it, and the struct has to make the member access
     resolve. The verdict below is the ORACLE's, on the compiled object.

Read-only apart from the attempts the oracle logs.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import compile_obligations, workspace                          # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

traced = json.loads((BASE / "wide-intake-undeclared.json").read_text(encoding="utf-8"))
blocked = [row for row in traced["rows"] if not row["sequence"]["compiled"]]
targets = []
for row in blocked:
    classes = (row.get("diagnostic_trace") or [{}])[-1].get("classes") or []
    if "member-on-typed-pointer" in classes:
        targets.append((row["function"], classes))
print(f"states whose final chain contains member-on-typed-pointer: {len(targets)}")
whole = [name for name, classes in targets if classes == ["member-on-typed-pointer"]]
print(f"  of which it is the WHOLE distance: {len(whole)}\n")

conn = sqlite3.connect(str(KB))
fired = compiled = exact = 0
shapes: Counter = Counter()
results = []
try:
    for name, _classes in targets:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
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
        if best.get("compiled"):
            continue

        asm = workspace.target_asm(workspace.bootstrap(REPO, name), name)
        source, report = compile_obligations.opaque_variant(REPO, name, current, asm)
        derived = report.get("derived_void_parameters") or []
        if source == current:
            shapes["declined, nothing to emit"] += 1
            continue
        fired += 1
        verdict = context.compile_fn(source)
        ok = bool(verdict.get("compiled"))
        compiled += int(ok)
        exact += int(bool(verdict.get("exact")))
        first = next((ln.strip() for ln in (verdict.get("stderr") or "").splitlines() if ln.strip()), "")
        results.append((name, len(derived), ok, verdict.get("score"), first[:70]))
        if not ok:
            shapes["still fails after the struct"] += 1
        else:
            shapes["COMPILED"] += 1
finally:
    conn.close()

print(f"fired on {fired} of {len(targets)}; compiled {compiled}, exact {exact}")
print(f"\noutcomes: {dict(shapes)}\n")
for name, count, ok, score, first in results:
    mark = "COMPILED" if ok else "         "
    print(f"  {mark} {name:44} void-params={count} score={score} {first}")
