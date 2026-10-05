"""What does the clang frontend cost per state, and what does it add over cfe?

TWO QUESTIONS, because the answer to the second only matters if the first is affordable:

  cost   `frontend_diagnostics.analyse` runs the project's checker recipe, which shells out to `make`
         once per process and then `clang -fsyntax-only` per state. On 200 states the per-state figure
         decides whether this can be an intake step or has to be a sampling tool.

  value  cfe stops at the first error and truncates its list there; clang reports every independent
         blocker. If clang's diagnostics carry gate strings for the repair modules that cfe never
         produces, then an intake route that only reads cfe is blind to mechanisms it already owns.

This runs on a handful of real frame members and prints both, so the wiring decision is made on numbers.
Read-only: it compiles nothing the oracle would not, and promotes nothing.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                       # noqa: E402
from eval.intake_runners import RUNNERS                                   # noqa: E402
from eval.tool_agent_run import build_context                             # noqa: E402
from solver import frontend_diagnostics as frontend                       # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"
SAMPLE = 8

frame = json.loads((BASE / "wide-frame.json").read_text(encoding="utf-8"))["rows"]
# Take the states the intake sequence could NOT convert: those are the ones a new step would have to help.
blocked = [r for r in frame if not r["sequence"]["compiled"]]
sample = blocked[:SAMPLE]
print(f"frame {len(frame)} states, {len(blocked)} still blocked; piloting {len(sample)}\n")

conn = sqlite3.connect(str(KB))
totals = {"clang_seconds": 0.0, "cfe_errors": 0, "clang_errors": 0, "gate_states": 0}
gate_hits: dict[str, int] = {}
try:
    for entry in sample:
        context, why = build_context(REPO, entry["function"], conn=conn)
        if context is None:
            print(f"{entry['function']}: {why}")
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
            except Exception:                                             # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""

        cfe_errors = sum(1 for line in stderr.splitlines() if line.startswith("cfe:"))
        started = time.time()
        report = frontend.analyse(current, repo=REPO, target=str(context.target or ""))
        elapsed = time.time() - started
        totals["clang_seconds"] += elapsed
        totals["cfe_errors"] += cfe_errors
        gates = report.get("gates") or {}
        if gates:
            totals["gate_states"] += 1
            for name in gates:
                gate_hits[name] = gate_hits.get(name, 0) + 1
        if report.get("status") != "unavailable":
            totals["clang_errors"] += report.get("error_count", 0)
        print(f"{entry['function'][:36]:36} {entry['tier']:7} cfe_errors={cfe_errors:2} "
              f"clang={report.get('status'):10} clang_errors={report.get('error_count', 0):3} "
              f"{elapsed:5.1f}s  gates={sorted(gates)}")
        if report.get("status") == "unavailable":
            print(f"      unavailable: {report.get('reason')}")
finally:
    conn.close()

n = len(sample)
print(f"\ncost   : {totals['clang_seconds']:.1f}s for {n} states = "
      f"{totals['clang_seconds'] / n:.2f}s per state; 200 states = "
      f"{totals['clang_seconds'] / n * 200 / 60:.1f} minutes")
print(f"value  : cfe reported {totals['cfe_errors']} errors over {n} states; clang reported "
      f"{totals['clang_errors']}")
print(f"         {totals['gate_states']} of {n} states produced at least one repair-gate string")
for name, count in sorted(gate_hits.items(), key=lambda kv: -kv[1]):
    print(f"           {count:3}  {name}")
