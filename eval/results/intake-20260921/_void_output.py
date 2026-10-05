"""What does the candidate look like after the `void` fix, and what is at the new error line?"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import compile_obligations, workspace                          # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"

for NAME in ("osEPiRawReadIo", "osEPiRawWriteIo"):
    conn = sqlite3.connect(str(KB))
    try:
        context, why = build_context(REPO, NAME, conn=conn)
        if context is None:
            print(f"{NAME}: {why}")
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
        asm = workspace.target_asm(workspace.bootstrap(REPO, NAME), NAME)
        source, report = compile_obligations.opaque_variant(REPO, NAME, current, asm)
        verdict = context.compile_fn(source)
        print("=" * 78)
        print(f"{NAME}   compiled={bool(verdict.get('compiled'))} score={verdict.get('score')}")
        print("=" * 78)
        for line in (verdict.get("stderr") or "").splitlines()[:6]:
            print(f"  cfe: {line.strip()[:104]}")
        print()
        for index, line in enumerate(source.splitlines(), 1):
            marker = "  <<<" if index in {int(m.group(1)) for m in
                                          __import__("re").finditer(r"line (\d+):",
                                                                    verdict.get("stderr") or "")} else ""
            print(f"  {index:>4} | {line[:104]}{marker}")
        print()
    finally:
        conn.close()
