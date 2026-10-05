"""Is the frontend's `undeclared identifier` reading about the final candidate, or about a mid-sequence one?

The SEQUENCE is

    resolve_placeholders
    frontend_diagnostics      <-- runs HERE, before any header is added
    negative_offset
    header_variant            <-- adds gRenderMatricesDirty, gIdentityFixedTransform, ...
    globals_variant
    opaque_variant
    rewrite_do_while

and `header_variant` demonstrably adds headers for names the frontend reported undeclared. So the chain
analysis -- "undeclared-identifier is the whole distance for 22 states" -- is measured on a candidate that
no longer exists by the end of the sequence. That is the same class of error as reading cfe's first error:
a true statement about an intermediate state, quoted as a statement about the outcome.

This runs a real state end to end and compares the diagnostics at BOTH points, plus the final verdict.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import frontend_diagnostics as frontend                        # noqa: E402
from solver import workspace                                               # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

split = json.loads((BASE / "undeclared-class-split.json").read_text(encoding="utf-8"))
TARGETS = ["renderRacePickupRespawn", "guMtxF2L", "alSavePull"]

conn = sqlite3.connect(str(KB))
try:
    for name in TARGETS:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"{name}: {why}")
            continue
        print("=" * 78)
        print(f"{name}")
        print("=" * 78)
        initial = dict(context.initial_verdict or {})
        current, best, stderr = context.candidate, initial, initial.get("stderr") or ""
        target = str(context.target or "")
        snapshots = {}
        for label in SEQUENCE:
            if not gated(label, stderr):
                continue
            ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                  "initial_verdict": {**initial, "stderr": stderr}}
            try:
                result = RUNNERS[label](ns, {})
            except Exception as exc:                                       # noqa: BLE001
                print(f"  {label.split('.')[-1]:22} raised {type(exc).__name__}")
                continue
            short = label.split(".")[-1]
            if result.get("changed"):
                verdict = context.compile_fn(result["source"])
                if rank(verdict) >= rank(best):
                    current, best = result["source"], verdict
                    stderr = verdict.get("stderr") or ""
                print(f"  {short:22} changed -> score {verdict.get('score')}")
            else:
                print(f"  {short:22} no-change ({str(result.get('reason'))[:52]})")
            # Diagnostics at THIS point in the sequence.
            report = frontend.analyse(current, repo=REPO, target=target)
            undeclared = sum(1 for e in (report.get("errors") or [])
                             if "undeclared identifier" in (e.get("what") or ""))
            snapshots[short] = {"status": report.get("status"),
                                "errors": report.get("error_count", 0),
                                "undeclared": undeclared}
        print(f"\n  diagnostics along the sequence:")
        for short, snap in snapshots.items():
            print(f"    after {short:22} clang={snap['status']:10} errors={snap['errors']:3} "
                  f"undeclared={snap['undeclared']}")
        print(f"\n  final: compiled={bool(best.get('compiled'))} exact={bool(best.get('exact'))} "
              f"score={best.get('score')}")
        first = next((ln.strip() for ln in (best.get("stderr") or "").splitlines() if ln.strip()), "")
        print(f"  final cfe error: {first[:100] or '(none)'}")
        headers = [ln for ln in current.splitlines() if ln.startswith("#include")]
        print(f"  headers in the final candidate: {len(headers)}")
        print()
finally:
    conn.close()
