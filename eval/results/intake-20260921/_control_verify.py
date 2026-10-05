"""Three checks the control's first run demands before any of it is quoted.

1. IS `redraft` A REAL TRANSFORM? It fired on 40 of 40 states. `redraft` re-runs m2c on the assembly
   with no `--context`, and `build_context` also starts from m2c's draft. If those two invocations agree,
   `changed` must be false and an action that "fired on everything" is actually an action that changed
   nothing; if they disagree, m2c is not deterministic here and EVERY measured delta in this repository
   that starts from an m2c draft is affected. Both readings are findings, so it is measured, not assumed.

2. DO THE TWO CONVERSIONS HOLD UP? Re-derive the draft with `build_context`, run `resolve-placeholders`,
   compile the result through `solver.workspace.score` again, and compare the verdict to the stored one.
   A conversion that does not reproduce is not a conversion.

3. WHERE DID THE ERRORS GO? For each state the control called "improved", print the baseline stderr next
   to the winner's stderr. Improvement is currently counted as "fewer compiler errors", which is a proxy;
   the stderr pair is the receipt that decides whether the proxy told the truth.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_control import error_classes                      # noqa: E402
from eval.tool_agent_run import build_context                      # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"
M2C = str(Path.home() / "decomp/sbk1/.venv/bin/m2c")

payload = json.loads((BASE / "class-control.json").read_text(encoding="utf-8"))
rows = {r["function"]: r for r in payload["rows"]}

conn = sqlite3.connect(str(KB))
try:
    print("=" * 78)
    print("1. IS m2c DETERMINISTIC, AND IS `redraft` A REAL TRANSFORM?")
    print("=" * 78)
    for name in ("__osPopThread", "copyGfxCommandBlockToScratch", "updateRaceResultsFlow"):
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"  {name}: build_context failed: {why}")
            continue
        asm = Path(context.target_asm_path)
        runs = [subprocess.run([M2C, "--target", "mips-ido-c", str(asm)],
                               capture_output=True, text=True, timeout=300).stdout for _ in range(2)]
        draft = context.candidate
        print(f"  {name}")
        print(f"     two fresh m2c runs identical : {runs[0] == runs[1]} "
              f"({len(runs[0])} vs {len(runs[1])} chars)")
        print(f"     m2c output == build_context draft: {runs[0].strip() == draft.strip()}")
        print(f"     draft {len(draft)} chars, m2c {len(runs[0])} chars")
        if runs[0].strip() != draft.strip():
            a, b = draft.splitlines(), runs[0].splitlines()
            first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            print(f"     first differing line {first + 1}:")
            print(f"        draft: {a[first] if first < len(a) else '<eof>'}")
            print(f"        m2c  : {b[first] if first < len(b) else '<eof>'}")

    print()
    print("=" * 78)
    print("2. DO THE TWO CONVERSIONS REPRODUCE?")
    print("=" * 78)
    from eval.tool_runners import resolve_placeholders

    for name in [r["function"] for r in payload["rows"] if r.get("compiling_anywhere")]:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"  {name}: build_context failed: {why}")
            continue
        base = dict(context.initial_verdict or {})
        result = resolve_placeholders({**context.__dict__, "kb_conn": conn}, {})
        verdict = context.compile_fn(result["source"]) if result.get("changed") else {}
        print(f"  {name}")
        print(f"     baseline   compiled={bool(base.get('compiled'))} exact={bool(base.get('exact'))} "
              f"score={base.get('score')}")
        print(f"     re-derived compiled={bool(verdict.get('compiled'))} "
              f"exact={bool(verdict.get('exact'))} score={verdict.get('score')}")
        print(f"     changed={result.get('changed')} placeholders={result.get('placeholders')}")

    print()
    print("=" * 78)
    print("3. THE ERROR PAIR FOR EVERY STATE THE CONTROL CALLED IMPROVED")
    print("=" * 78)
    for row in payload["rows"]:
        if not row.get("improved"):
            continue
        print(f"\n  {row['function']} ({row['tier']}, {row['baseline_errors']} -> "
              f"{row['errors_remaining_at_best']})")
        print(f"     baseline stderr: {row['baseline_stderr'][:200]!r}")
        for action, item in row["actions"].items():
            for verdict in (item.get("verdicts") or []):
                if verdict.get("errors_remaining") == row["errors_remaining_at_best"] and \
                        (verdict.get("compiled") or
                         verdict.get("errors_remaining") < row["baseline_errors"]):
                    print(f"     winner  ({action}): {json.dumps(verdict)[:220]}")
finally:
    conn.close()
