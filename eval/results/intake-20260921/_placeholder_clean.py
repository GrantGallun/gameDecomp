"""The clean two-rule comparison, twice, in one process, with the judge being the compiler.

WHAT WENT WRONG THE FIRST TIME, recorded because it decides how the result is read. The arms run recorded
`copyGfxCommandBlockToScratch` as compiling under the module's rewrite (42.037) and not under the widened
one. The draft was then printed in full: the module DOES rewrite `allocMenuRenderScratch(?)` -- a
parameter list with no parameter name -- through `PARAM`, and the widened rule I wrote does not, because it
requires a NAME after the `?`. So the two rules are not nested; each covers a shape the other misses:

    module   `? name;` / `extern ? name;`   (DECL_LINE)   +   `(?` / `,?`  (PARAM, nameless)
    widened  `? name` anywhere, parameter LISTS included, nameless NOT included

A comparison of two partially-overlapping rules is still a comparison, but the union is what a fix would
actually ship, so three arms are measured here and the union is the one that matters:

    module    what the action does today
    widened   the `? name` shapes the module misses, on top of module
    union     both rules, i.e. what a fix would be

Run TWICE in the same process, module and union in opposite orders on the second pass, because the first
measurement showed one state whose verdict changed between runs and a mechanism whose output is not
stable cannot be quoted from a single pass. `solver.workspace.score` writes into the function's workspace
and that workspace is shared with a live campaign, so order and repetition are the controls here.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                          # noqa: E402
from solver import m2c_placeholders                                    # noqa: E402

sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
from _placeholder_rules import union, widened                          # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"


def measure(order: tuple[str, ...]) -> list[dict]:
    frame = json.loads((BASE / "class-frame.json").read_text(encoding="utf-8"))["rows"]
    conn = sqlite3.connect(str(KB))
    out = []
    try:
        for entry in frame:
            name = entry["function"]
            context, why = build_context(REPO, name, conn=conn)
            if context is None:
                print(f"  {name}: {why}")
                continue
            draft = context.candidate or ""
            variants = {
                "module": m2c_placeholders.rewrite(draft),
                "widened": widened(draft),
                "union": union(draft),
            }
            row = {"function": name, "tier": entry.get("tier")}
            for label in order:
                source, tokens = variants[label]
                verdict = context.compile_fn(source)
                row[label] = {"compiled": bool(verdict.get("compiled")),
                              "exact": bool(verdict.get("exact")),
                              "score": verdict.get("score"),
                              "tokens": len(tokens) if isinstance(tokens, list) else tokens,
                              "changed": source != draft}
            out.append(row)
    finally:
        conn.close()
    return out


first = measure(("module", "widened", "union"))
second = measure(("union", "widened", "module"))

print(f"{'function':40} {'module':>9} {'widened':>9} {'union':>9}   stability")
flaky = []
for a, b in zip(first, second):
    cells = []
    for label in ("module", "widened", "union"):
        state = "exact" if a[label]["exact"] else "compiles" if a[label]["compiled"] else "-"
        if (a[label]["compiled"], a[label]["exact"]) != (b[label]["compiled"], b[label]["exact"]):
            state += "!" + ("exact" if b[label]["exact"] else
                            "compiles" if b[label]["compiled"] else "-")
            flaky.append((a["function"], label, a[label], b[label]))
        cells.append(f"{state:>9}")
    print(f"{a['function']:40} " + " ".join(cells))

print()
for label in ("module", "widened", "union"):
    n = len(first)
    conv = sum(1 for r in first if r[label]["compiled"])
    exact = sum(1 for r in first if r[label]["exact"])
    conv2 = sum(1 for r in second if r[label]["compiled"])
    exact2 = sum(1 for r in second if r[label]["exact"])
    print(f"{label:9} pass1 {conv:2}/{n} ({100 * conv / n:4.1f}%) exact {exact}   "
          f"pass2 {conv2:2}/{n} ({100 * conv2 / n:4.1f}%) exact {exact2}")

print(f"\nflaky (a state whose verdict differed between the two passes): {len(flaky)}")
for name, label, a, b in flaky:
    print(f"   {name:40} {label:9} pass1={a} pass2={b}")

new = [r["function"] for r in first
       if r["union"]["compiled"] and not r["module"]["compiled"]]
lost = [r["function"] for r in first
        if r["module"]["compiled"] and not r["union"]["compiled"]]
print(f"\ncases the union converts and the module does not ({len(new)}): {new}")
print(f"cases the module converts and the union does not ({len(lost)}): {lost}")
exact_new = [r["function"] for r in first if r["union"]["exact"] and not r["module"]["exact"]]
print(f"newly EXACT under the union ({len(exact_new)}): {exact_new}")

(BASE / "class-placeholder-clean.json").write_text(json.dumps(
    {"pass1": first, "pass2": second, "flaky": [list(map(str, f)) for f in flaky],
     "note": ("module = solver.m2c_placeholders.rewrite; widened = `? name` in parameter lists; "
              "union = module then widened. Two passes in one process, opposite orders. The compiler "
              "judged every output; no model, nothing promoted.")}, indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'class-placeholder-clean.json'}")
