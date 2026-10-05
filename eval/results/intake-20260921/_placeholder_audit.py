"""Two things the arm measurement left open, both of which decide whether it can be quoted.

1. THE REGRESSION. `copyGfxCommandBlockToScratch` compiles under the module's rewrite (score 42.037) and
   does NOT under the widened one. An extension that hands back a case it was already handling is not an
   extension, it is a different bug, and the 8-of-40 figure is not quotable until it is explained.

2. THE UNTOUCHED CONTROL. 31 of 40 drafts carry no `?` at all, so both arms must leave them byte-identical
   and produce identical verdicts. If they do not, the widened substitution is matching text it should
   not be matching and the comparison is not between two placeholder policies.

The exact drafts involved are printed rather than summarised, because the only way to see which of these
it is, is to look at the text.
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
from _placeholder_rules import widened                                 # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

arms = json.loads((BASE / "class-placeholder-arms.json").read_text(encoding="utf-8"))["rows"]
conn = sqlite3.connect(str(KB))
try:
    print("=" * 78)
    print("1. THE REGRESSION: copyGfxCommandBlockToScratch")
    print("=" * 78)
    context, _ = build_context(REPO, "copyGfxCommandBlockToScratch", conn=conn)
    module_src, names = m2c_placeholders.rewrite(context.candidate)
    wide_src, hits = widened(context.candidate)
    print(f"  module tokens={names}   widened hits={hits}")
    for label, src in (("module", module_src), ("widened", wide_src)):
        verdict = context.compile_fn(src)
        print(f"  {label:8} compiled={bool(verdict.get('compiled'))} exact={bool(verdict.get('exact'))} "
              f"score={verdict.get('score')} stderr={(verdict.get('stderr') or '')[:90]!r}")
    diff = list(difflib.unified_diff(module_src.splitlines(), wide_src.splitlines(),
                                     "module", "widened", lineterm="", n=1))
    print(f"  module -> widened diff ({len(diff)} lines):")
    for line in diff[:24]:
        print(f"     {line[:140]}")

    print()
    print("=" * 78)
    print("2. THE UNTOUCHED CONTROL: drafts with no `?`")
    print("=" * 78)
    untouched = [r["function"] for r in arms if r["module_tokens"] == 0 and r["wide_tokens"] == 0]
    print(f"  {len(untouched)} drafts carry no `?` in either arm")
    same_source = diffs = 0
    for name in untouched:
        context, _ = build_context(REPO, name, conn=conn)
        if context is None:
            continue
        module_src, _ = m2c_placeholders.rewrite(context.candidate)
        wide_src, hits = widened(context.candidate)
        if module_src == wide_src == context.candidate:
            same_source += 1
        else:
            diffs += 1
            print(f"  ! {name} changed with no `?` present (hits={hits})")
    print(f"  byte-identical in both arms: {same_source}   unexpectedly changed: {diffs}")

    print()
    print("=" * 78)
    print("3. WHERE THE WIDENED ARM LOSES A CASE THE MODULE WON")
    print("=" * 78)
    won = [r for r in arms if r["module_compiled"] and not r["wide_compiled"]]
    print(f"  module converts, widened does not: {[r['function'] for r in won]}")
    print(f"  widened converts, module does not: "
          f"{[r['function'] for r in arms if r['wide_compiled'] and not r['module_compiled']]}")

    print()
    print("=" * 78)
    print("4. WHAT THE WIDENED ARM STILL CANNOT COMPILE, AND ITS FIRST ERROR")
    print("=" * 78)
    for row in arms:
        if row["wide_compiled"]:
            continue
        first = next((ln for ln in row["wide_stderr"].splitlines() if ln.strip()), "")
        print(f"  {row['function']:40} {first[:96]}")
finally:
    conn.close()
