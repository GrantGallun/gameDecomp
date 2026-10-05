"""The 11 unclassified states: what is actually wrong, in the compiler's full text and the draft's.

`Syntax Error` reported on `s16 temp_v0;` or `Vec3s *temp_t1;` cannot be about those lines -- both are
valid C, and no open construct precedes them in any of the drafts. So the question is whether the real
fault is somewhere the FIRST error does not name, which is exactly the case the module's own docs describe
("cfe STOPS at that line and truncates its error list there").

This prints, per state: every `cfe:` line in the verdict, and the whole list of lines carrying `?` or
`M2C_` tokens, since those are what the intake route can currently act on. Read-only.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                      # noqa: E402
from eval.intake_runners import RUNNERS                                  # noqa: E402
from eval.tool_agent_run import build_context                            # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

classes = json.loads((BASE / "post-fix-residual-classes.json").read_text(encoding="utf-8"))
SAMPLES = classes["classes"]["unclassified"][:4]

conn = sqlite3.connect(str(KB))
try:
    for name in SAMPLES:
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
        print("=" * 78)
        print(f"{name}   {len(current)} chars, {len(stderr.splitlines())} stderr lines")
        print("=" * 78)
        print("  every compiler line:")
        for line in (stderr or "").splitlines()[:14]:
            print(f"    {line[:120]}")
        if not stderr:
            print("    (compiler produced no diagnostics)")
        suspicious = [(i, ln) for i, ln in enumerate(current.splitlines(), 1)
                      if re.search(r"\?|M2C_|\bunk-|\bbitwise\b|\bunaligned\b", ln)]
        print(f"  lines carrying a token the intake route owns ({len(suspicious)}):")
        for index, line in suspicious[:10]:
            print(f"    {index:>5}: {line.strip()[:110]}")
        print(f"  first 6 lines of the candidate:")
        for line in current.splitlines()[:6]:
            print(f"    {line[:110]}")
        print()
finally:
    conn.close()
