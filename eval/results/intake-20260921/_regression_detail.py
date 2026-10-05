"""Resolve the one contradiction in the arm measurement, with the text in front of us.

The claim under test: `copyGfxCommandBlockToScratch` compiles under the module's rewrite and does not
under the widened one. Before that is written down, the two rewrites are applied to the CURRENT draft in
one process and both outputs are printed in full, together with the candidate the compiler sees and the
verdict for each. Two different explanations were available and only the diff can separate them:

  (a) the widened substitution rewrites something the module's rewrite leaves alone -- then it is a
      looser rule, the regression is real, and the widened arm's 20% is inflated by a candidate that
      was already at 5%;
  (b) the draft in the module's rewrite still carries a bare `?` from a shape NEITHER rule handles --
      then the module's 42.037 was produced on a candidate that cannot be what is written down, and the
      defect is in the measurement, not in the widening.
"""
from __future__ import annotations

import difflib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                          # noqa: E402
from solver import m2c_placeholders                                    # noqa: E402

sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
from _placeholder_arms import widened                                  # noqa: E402

KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"
NAME = "copyGfxCommandBlockToScratch"
BARS = "=" * 78

conn = sqlite3.connect(str(KB))
try:
    context, _ = build_context(REPO, NAME, conn=conn)
    draft = context.candidate or ""
    module_src, names = m2c_placeholders.rewrite(draft)
    wide_src, hits = widened(draft)

    print(BARS)
    print(f"THE DRAFT AS THE CONTEXT HANDS IT OVER  ({len(draft)} chars)")
    print(BARS)
    for index, line in enumerate(draft.splitlines(), 1):
        print(f"  {index:3} {line}")

    print()
    print(BARS)
    print(f"module rewrite: names={names}  changed={module_src != draft}")
    print(BARS)
    for index, line in enumerate(module_src.splitlines(), 1):
        marker = "  <<<" if "?" in line else ""
        print(f"  {index:3} {line}{marker}")

    print()
    print(BARS)
    print(f"widened rewrite: hits={hits}  changed={wide_src != draft}")
    print(BARS)
    for index, line in enumerate(wide_src.splitlines(), 1):
        marker = "  <<<" if "?" in line else ""
        print(f"  {index:3} {line}{marker}")

    print()
    print("diff module -> widened:")
    for line in difflib.unified_diff(module_src.splitlines(), wide_src.splitlines(),
                                     "module", "widened", lineterm="", n=2):
        print(f"   {line[:150]}")

    print()
    print(BARS)
    print("VERDICTS, each on the text printed above")
    print(BARS)
    for label, source in (("draft (baseline)", draft), ("module", module_src), ("widened", wide_src)):
        verdict = context.compile_fn(source)
        print(f"  {label:18} compiled={bool(verdict.get('compiled'))} "
              f"exact={bool(verdict.get('exact'))} score={verdict.get('score')}")
        print(f"     stderr: {(verdict.get('stderr') or '(none)')[:150]!r}")

    print()
    print("does the module's output still contain a declaration-position `?`?")
    for index, line in enumerate(module_src.splitlines(), 1):
        if "?" in m2c_placeholders._masked(line):
            print(f"   line {index}: {line.strip()[:120]}")
finally:
    conn.close()
