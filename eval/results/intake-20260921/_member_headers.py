"""Do the project's headers already have these field names, and does supplying a struct convert the state?

The `member-on-typed-pointer` class splits by what the draft's text actually says, and the two halves have
different owners:

    `arg0->unk10`      m2c named the field from its offset. The assembly fixes the offset and the width, so
                       a padded struct is mechanically derivable -- `opaque_variant`'s job.
    `arg0->matrixDirty` a REAL field name. Nothing in the assembly says offset 0x14 is called that; the name
                       came from the reference decomp and m2c kept it. No deterministic pass can produce it.

Measured across the 12 single-class states: 0 m2c-named members, 10 real field names. So the question stops
being "can tooling reach it" and becomes "does the project already know these names", which is checkable:

    in project headers  -> `header_variant` should supply the type, and the declaration already carries the
                           names. If the state still fails, the header search has a gap.
    nowhere             -> the type does not exist in this project's headers at all. That is a semantics
                           gap, and the honest next move is to record it rather than to invent fields.

Read-only: it reads headers, and compiles through the oracle only to test a supplied struct.
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

REPO = Path.home() / "decomp/sbk1"
INCLUDE = REPO / "include"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

residual = json.loads((BASE / "post-sequence-residual.json").read_text(encoding="utf-8"))
targets = [row["function"] for row in residual["single_class"]
           if row["class"] == "member-on-typed-pointer"]

# Every field name that appears inside a struct/union body anywhere in the project's headers.
declared_members: dict[str, list[str]] = {}
for header in INCLUDE.rglob("*.h"):
    text = header.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*[\[;]", text):
        declared_members.setdefault(match.group(1), []).append(
            header.relative_to(INCLUDE).as_posix())

conn = sqlite3.connect(str(KB))
try:
    for name in targets:
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
        members = sorted({m.group(2) for m in
                          re.finditer(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)", current)})
        print(f"{name}")
        if not members:
            print(f"   no `->member` access in the final candidate at all -- the class label came from a "
                  f"diagnostic about something else")
        for member in members:
            where = declared_members.get(member) or []
            if where:
                print(f"   {member:22} DECLARED in {where[0]}"
                      f"{f' (+{len(where) - 1} more)' if len(where) > 1 else ''}")
            else:
                print(f"   {member:22} not a member of any struct/union in include/**")
        first = next((ln.strip() for ln in stderr.splitlines() if ln.strip()), "")
        print(f"   cfe: {first[:96]}")
        print()
finally:
    conn.close()
