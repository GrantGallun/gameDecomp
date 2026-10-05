"""Where did `struct osEPiRawReadIo_arg0` go? The respell survived but the definition did not.

`_void_trace.py` shows the final candidate's signature already reading
`struct osEPiRawReadIo_arg0 *arg0` -- so SOMETHING emitted both halves -- and no struct definition in the
candidate, so a later step removed it or the two halves came from different steps.

`header_variant` runs after `opaque_variant` in the sequence and calls `project_headers.reconcile_declarations`,
which DELETES top-level declarations a header provides. A struct the candidate defines itself could be
collateral. This traces the candidate's struct through every step.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
NAME = "osEPiRawReadIo"
TAG = f"{NAME}_arg0"

conn = sqlite3.connect(str(KB))
try:
    context, why = build_context(REPO, NAME, conn=conn)
    if context is None:
        raise SystemExit(why)
    initial = dict(context.initial_verdict or {})
    current, best, stderr = context.candidate, initial, initial.get("stderr") or ""

    def state(label: str, source: str) -> None:
        has_struct = bool(re.search(r"\bstruct\s+" + re.escape(TAG) + r"\s*\{", source))
        has_respell = bool(re.search(r"struct\s+" + re.escape(TAG) + r"\s*\*", source))
        has_void = bool(re.search(r"\bvoid\s*\*\s*arg0\b", source))
        print(f"  after {label:26} struct_defined={has_struct!s:5} respelled={has_respell!s:5} "
              f"void_star={has_void!s:5} len={len(source)}")

    print(f"{NAME}: tracing `{TAG}`")
    state("start", current)

    for label in SEQUENCE:
        if not gated(label, stderr):
            continue
        ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
              "initial_verdict": {**initial, "stderr": stderr}}
        try:
            result = RUNNERS[label](ns, {})
        except Exception as exc:                                           # noqa: BLE001
            print(f"  {label.split('.')[-1]:26} raised {type(exc).__name__}")
            continue
        short = label.split(".")[-1]
        if not result.get("changed"):
            print(f"  {short:26} no-change")
            continue
        verdict = context.compile_fn(result["source"])
        state(short + " (proposed)", result["source"])
        if rank(verdict) >= rank(best):
            current, best = result["source"], verdict
            stderr = verdict.get("stderr") or ""
        else:
            print(f"  {short:26} proposed but ranked lower -- NOT kept")
    print()
    state("FINAL", current)
    print(f"\n  final verdict: compiled={bool(best.get('compiled'))} score={best.get('score')}")
    first = next((ln.strip() for ln in stderr.splitlines() if ln.strip()), "")
    print(f"  cfe: {first[:100] or '(none)'}")
    print(f"\n  the struct's lines in the final candidate:")
    for index, line in enumerate(current.splitlines(), 1):
        if TAG in line:
            print(f"     {index:>4}: {line.strip()[:100]}")
finally:
    conn.close()
