"""Does the project's clang frontend produce the diagnostic `negative_field_repair` gates on, and does
the repair fire when it does?

The chain already exists in the repository:

    solver/frontend_check.check        runs `clang -fsyntax-only` under the project's own Makefile
                                       policy and writes `<name>.frontend.json` with diagnostics
    solver/negative_field_repair       gates on `member reference base type` -- a CLANG diagnostic,
                                       not a cfe one, which is why it has never fired on this route
    solver/modelrepair.py:759          the only caller, reached after intake, not during it

cfe reports `Syntax Error` for `temp_v0->unk-4`; clang parses it and then says what it did (`->unk` is a
member, `- 4` is a subtraction). That difference is the whole reason the repair is silent here.

This runs the chain by hand on the states where the residual is a negative field offset. Read-only apart
from what the oracle logs; nothing is wired by this script.
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
from solver import frontend_check, negative_field_repair, workspace      # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

classes = json.loads((BASE / "post-fix-residual-classes.json").read_text(encoding="utf-8"))
TARGETS = classes["classes"]["negative field offset (`->unk-N`, not an identifier)"][:6]

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
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""

        ws = workspace.bootstrap(REPO, name)
        target = (initial.get("compiler_recipe") or {}).get("target")
        print("=" * 78)
        print(f"{name}   target={target}")
        print("=" * 78)
        if not target:
            print("  no compiler recipe -> the frontend cannot run")
            continue

        source_path = ws / "probe_unk.c"
        source_path.write_text(current, encoding="utf-8")
        front = frontend_check.check(REPO, source_path, target)
        diagnostics = front.get("diagnostics") or ""
        print(f"  frontend status : {front.get('status')}")
        hits = [line.strip() for line in diagnostics.splitlines()
                if "member reference base type" in line]
        print(f"  member-reference diagnostics: {len(hits)}")
        for line in hits[:3]:
            print(f"     {line[:110]}")
        report = negative_field_repair.propose(current, name,
                                               workspace.target_asm(ws, name), diagnostics)
        print(f"  negative_field_repair changes: {len(report['changes'])}")
        for change in report["changes"][:3]:
            print(f"     {change['before'][:44]:44} -> {change['after'][:52]}")
        if report["changes"]:
            verdict = context.compile_fn(report["source"])
            before_first = next((ln for ln in stderr.splitlines() if ln.strip()), "")
            after_first = next((ln for ln in (verdict.get("stderr") or "").splitlines()
                                if ln.strip()), "")
            print(f"  compiled before={bool(best.get('compiled'))} after={bool(verdict.get('compiled'))} "
                  f"exact={bool(verdict.get('exact'))} score={verdict.get('score')}")
            print(f"     error before: {before_first[:80]}")
            print(f"     error after : {after_first[:80] or '(none)'}")
        source_path.unlink(missing_ok=True)
        print()
finally:
    conn.close()
