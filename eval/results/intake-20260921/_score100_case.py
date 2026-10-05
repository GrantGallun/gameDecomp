"""Why does `rmonPrintf` compile at score 100.0 and fail certification?

CLASSIFIED as none of the three outcomes this file was written to separate. The answer is that it does NOT
fail certification: the certificate says it is byte-exact, and `attempt.exact` says otherwise.

    attempt.exact            False
    certificate status       object_sections_differ
    normalized dumps         identical (114 vs 114), diff empty
    boundary.function_exact  TRUE, status `function_exact_pending_integration`, no error
    boundary size            28  (a fresh call refuses a KB-derived 56: `KB metadata ... size=28`)
    integration_ready        True

`rmonPrintf` and `osSyncPrintf` are the SAME code -- both empty variadic stubs, normalized dumps
byte-identical, first 16 text bytes identical -- and they get opposite verdicts for a mechanical reason:

    osSyncPrintf   .text 32 bytes for a 32-byte function, no trailing padding
                   -> object_sections_exact, attempt.exact True
    rmonPrintf     .text 32 bytes for a 28-byte function, 4 bytes of assembler alignment
                   -> object_sections_differ, and the function-boundary path that DOES certify it sets
                      `verification["function_boundary"]`, which `Attempt.exact` never reads
                   (its target lives in a 48-byte TU .text: 28 function + 20 trailing)

So the ROM-backed extent certificate is doing its job and the promotion path is looking somewhere else.
`eval/completion_campaign.py:210` reads the boundary correctly and sets
`function_exact_pending_integration`; `solver/workspace.py` sets `exact = verification["exact"]`, which the
boundary path does not touch. Every harness keying on `attempt.exact` -- this session's probes among them --
scores a certified-correct function as a failure.

NOT A DEFECT TO PATCH HERE. Changing what `Attempt.exact` means would move the ratchet, and the boundary
certificate says `requires_isolated_integration: True` and excludes `whole ROM` on purpose. The finding is
recorded, and the check below is left as the receipt for it.

The check is read-only: it recompiles through the oracle, reads the artifacts, and promotes nothing.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_control import build_frame_context                    # noqa: E402
from eval.tool_agent_run import build_context                          # noqa: E402

sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
from _placeholder_rules import union                                   # noqa: E402

KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

# The two states that scored 100.0 in class-repair-stage.json: one certified, one not.
CASES = ("osSyncPrintf", "rmonPrintf")
frame = {r["function"]: r for r in
         json.loads((BASE / "class-frame.json").read_text(encoding="utf-8"))["rows"]}

conn = sqlite3.connect(str(KB))
try:
    for name in CASES:
        entry = frame.get(name)
        if entry is None:
            print(f"{name}: not in the frame")
            continue
        context, initial, why = build_frame_context(REPO, entry, conn)
        if context is None:
            print(f"{name}: {why}")
            continue
        source, tokens = union(context.candidate or "")
        verdict = context.compile_fn(source)
        ws = Path(context.workspace) if context.workspace else REPO / "nonmatchings" / name
        target_dump = ws / "target_object_dump_normalized.s"
        cand_dump = ws / f"{name}_object_dump_normalized.s"

        print("=" * 78)
        print(f"{name}   placeholders={tokens}")
        print("=" * 78)
        print(f"  verdict: compiled={verdict.get('compiled')} exact={verdict.get('exact')} "
              f"score={verdict.get('score')}")
        print(f"  certificate status: {(verdict.get('verification') or {}).get('status')}")
        verification = verdict.get("verification") or {}
        for key in sorted(verification):
            if key in ("exact", "status", "normalized_assembly_exact", "build_manifest_scope"):
                continue
            value = verification[key]
            print(f"    {key}: {str(value)[:150]}")
        print(f"  diff: {len(verdict.get('diff') or '')} chars")
        diff = (verdict.get("diff") or "").strip()
        if diff:
            for line in diff.splitlines()[:24]:
                print(f"    {line[:150]}")
        print(f"  target dump exists: {target_dump.is_file()}   candidate dump exists: "
              f"{cand_dump.is_file()}")
        if target_dump.is_file() and cand_dump.is_file():
            a = target_dump.read_text(errors="replace")
            b = cand_dump.read_text(errors="replace")
            print(f"  normalized dumps identical: {a == b}  ({len(a)} vs {len(b)} chars)")
            if a != b:
                import difflib
                for line in list(difflib.unified_diff(a.splitlines(), b.splitlines(),
                                                      "target", "candidate", lineterm="", n=1))[:20]:
                    print(f"    {line[:150]}")
finally:
    conn.close()
