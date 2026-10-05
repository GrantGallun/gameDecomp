"""How much of the frame's `Syntax Error` wall is the `?` token, and how much of it the current
rewriter reaches?

WHY THIS IS THE DECIDING MEASUREMENT. Step 1 says the four intake actions convert nothing on this frame.
Step 2 says the original actions DO move the certificate (7 of 40; `resolve-placeholders` converts 2).
The census says 36 raw `?` tokens across the frame but the module's own `placeholders()` finds only 18,
and 12 tokens survive a rewrite -- all of them in PARAMETER lists, which `PARAM`'s lookahead
(`(?=\\s*[,)*])`) cannot match because the token is followed by a NAME (`? a0_unk4`), not punctuation.

So the question this answers is not "does a mechanism exist" but "is the blocker the token the mechanism
names". Two independently built SUBSTITUTIONS are compiled per draft:

  module    `m2c_placeholders.rewrite` exactly as it stands (the current capability)
  widened   the same substitution, with `? name` handled wherever it appears -- parameter lists
            included, because a parameter list is the same token in the same grammatical position

and a third arm adds the campaign's intake sequence on top of `widened`. Compiles and exactness are the
certificate's verdict on the OUTPUT. No model is called and nothing is promoted: this measures an upper
bound for a candidate fix, it does not apply one.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, rank                            # noqa: E402
from eval.intake_runners import RUNNERS                                 # noqa: E402
from eval.tool_agent_run import build_context                           # noqa: E402
from solver import m2c_placeholders                                     # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

sys.path.insert(0, str(BASE))
from _placeholder_rules import widened                                 # noqa: E402


def measure() -> list[dict]:
    frame = json.loads((BASE / "class-frame.json").read_text(encoding="utf-8"))["rows"]
    conn = sqlite3.connect(str(KB))
    rows = []
    try:
        for entry in frame:
            name = entry["function"]
            context, why = build_context(REPO, name, conn=conn)
            if context is None:
                continue
            draft = context.candidate or ""
            module_src, module_names = m2c_placeholders.rewrite(draft)
            wide_src, wide_hits = widened(draft)

            module_verdict = context.compile_fn(module_src)
            wide_verdict = context.compile_fn(wide_src)

            # third arm: the campaign's intake order on top of the widened draft
            current, best = wide_src, wide_verdict
            stderr = best.get("stderr") or ""
            for label in SEQUENCE:
                stage_ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                            "initial_verdict": {**(context.initial_verdict or {}), "stderr": stderr}}
                try:
                    result = RUNNERS[label](stage_ns, {})
                except Exception:                                      # noqa: BLE001
                    continue
                if not result.get("changed"):
                    continue
                verdict = context.compile_fn(result["source"])
                if rank(verdict) >= rank(best):
                    current, best = result["source"], verdict
                    stderr = verdict.get("stderr") or ""

            rows.append({"function": name, "tier": entry.get("tier"),
                         "module_compiled": bool(module_verdict.get("compiled")),
                         "module_exact": bool(module_verdict.get("exact")),
                         "module_score": module_verdict.get("score"),
                         "module_tokens": len(module_names),
                         "wide_compiled": bool(wide_verdict.get("compiled")),
                         "wide_exact": bool(wide_verdict.get("exact")),
                         "wide_score": wide_verdict.get("score"),
                         "wide_tokens": wide_hits,
                         "wide_stderr": (wide_verdict.get("stderr") or "")[:100],
                         "composed_compiled": bool(best.get("compiled")),
                         "composed_exact": bool(best.get("exact")),
                         "composed_score": best.get("score")})
    finally:
        conn.close()
    return rows


def main() -> int:
    rows = measure()
    arms = {"module": ("module_compiled", "module_exact"),
            "widened": ("wide_compiled", "wide_exact"),
            "widened+intake": ("composed_compiled", "composed_exact")}
    print(f"{'function':40} {'module':>10} {'widened':>10} {'+intake':>10}")
    for row in rows:
        cells = []
        for key, (compiled, exact) in arms.items():
            cells.append(f"{'exact' if row[exact] else 'compiles' if row[compiled] else '-':>10}")
        print(f"{row['function']:40} " + " ".join(cells))
    print()
    n = len(rows)
    for label, (compiled, exact) in arms.items():
        converted = sum(1 for r in rows if r[compiled])
        print(f"{label:16} converted {converted:2} of {n} ({100 * converted / n:.1f}%)   "
              f"exact {sum(1 for r in rows if r[exact])}")

    (BASE / "class-placeholder-arms.json").write_text(json.dumps(
        {"rows": rows, "arms": {k: {"converted": sum(1 for r in rows if r[v[0]]),
                                    "exact": sum(1 for r in rows if r[v[1]])}
                                for k, v in arms.items()},
         "note": ("module = solver.m2c_placeholders.rewrite as it stands; widened = the same "
                  "substitution extended to `? name` in parameter lists; +intake adds the campaign's "
                  "intake sequence. Measured, not applied; nothing promoted.")}, indent=2) + "\n",
        encoding="utf-8")
    print(f"\nwrote {BASE / 'class-placeholder-arms.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
