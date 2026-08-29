"""Run the trace checks across EVERY function and rank what is broken.

tools/trace.py inspects one function, so a failure mode affecting thirty
functions has to be noticed thirty times -- and every bug found on 2026-08-28
was found by staring at one case and being lucky about which case. This is the
same checks, applied to the whole corpus, ranked by how many functions each
one hits.

Reads stored attempts rather than recompiling, so it is fast and touches
neither the GPU nor build.sh's lock.

    python3 -m tools.audit                 # ranked failure modes
    python3 -m tools.audit --targets       # also list the best next targets
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from solver import context as kb_context
from solver import pipeline, structgen, workspace

# stderr signatures -> what the failure actually is, and whose fault
STDERR_MODES = [
    (re.compile(r"Unknown character `"), "extraction: fence leaked (ours)"),
    (re.compile(r"Unknown character \$"), "extraction: assembly as C (ours)"),
    (re.compile(r"Unterminated (string|comment)"), "truncation (ours)"),
    (re.compile(r"contains a do-while"), "do-while token (prompt, fixed)"),
    (re.compile(r"redeclaration of"), "redeclared a type"),
    (re.compile(r"ultratypes\.h.*Syntax Error"), "redefined a scalar type"),
    (re.compile(r"undefined; reoccurrences"), "undeclared symbol"),
    (re.compile(r"member of structure or union required"), "wrong struct shape"),
    (re.compile(r"Empty declaration specifiers"), "unknown type name"),
    (re.compile(r"no text symbols"), "type conflict / empty object"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", action="store_true")
    ap.add_argument("--db", type=Path,
                    default=Path.home() / "decomp/kb-sbk1.sqlite")
    args = ap.parse_args()

    repo = Path.home() / "decomp/sbk1"
    conn = sqlite3.connect(str(args.db))

    funcs = [r[0] for r in conn.execute(
        "select distinct f.name from attempts a"
        " join functions f on f.addr = a.func_addr")]
    print(f"auditing {len(funcs)} functions with attempts\n")

    flags: Counter[str] = Counter()
    affected: dict[str, list[str]] = defaultdict(list)
    near: list[tuple[str, float]] = []
    exact_n = 0

    for fn in funcs:
        rows = conn.execute(
            "select score, compiled, compiler_stderr, source_code from attempts"
            " where func_addr=(select addr from functions where name=?)",
            (fn,)).fetchall()
        scores = [r[0] for r in rows if r[1]]
        best = max(scores) if scores else 0.0
        if best >= 100:
            # 100 by the scorer is not necessarily byte-exact
            exact_n += 1

        # --- stage: candidates -------------------------------------------
        if not scores:
            flags["no compiling candidate at all"] += 1
            affected["no compiling candidate at all"].append(fn)

        # --- stage: compile failure modes --------------------------------
        seen_modes = set()
        for _, compiled, err, _src in rows:
            if compiled or not err:
                continue
            for pat, label in STDERR_MODES:
                if pat.search(err):
                    seen_modes.add(label)
        for label in seen_modes:
            flags[label] += 1
            affected[label].append(fn)

        # --- stage: context ----------------------------------------------
        kb = kb_context.for_function(conn, fn) or ""
        if not kb.strip():
            flags["KB context empty"] += 1
            affected["KB context empty"].append(fn)
        elif not re.search(r"param\d", kb):
            flags["KB has no param-based evidence"] += 1
            affected["KB has no param-based evidence"].append(fn)

        # --- stage: prompt -----------------------------------------------
        try:
            ws = workspace.bootstrap(repo, fn)
            asm = workspace.target_asm(ws, fn)
            pipeline.build_prompt(repo, conn, fn, asm,
                                  workspace.m2c_draft(ws), "reshape",
                                  use_siblings=False)
        except Exception as exc:
            label = f"prompt build FAILS ({type(exc).__name__})"
            flags[label] += 1
            affected[label].append(fn)

        # --- stage: the rounding trap ------------------------------------
        if 99.5 <= best < 100:
            near.append((fn, best))
            if f"{best:.2f}" == "100.00":
                flags["score displays 100.00 but is not exact"] += 1
                affected["score displays 100.00 but is not exact"].append(fn)

        # --- stage: struct repairability ---------------------------------
        if 90 <= best < 100:
            src = max((r for r in rows if r[1]), key=lambda r: r[0])[3] or ""
            lay = structgen.layout(conn, fn)
            if not structgen.struct_names(src):
                flags["near-miss candidate declares no struct"] += 1
                affected["near-miss candidate declares no struct"].append(fn)
            elif not lay:
                flags["near-miss has struct but no param evidence"] += 1
                affected["near-miss has struct but no param evidence"].append(fn)

    print(f"{'functions':>9}  failure mode")
    print("-" * 74)
    for label, n in flags.most_common():
        print(f"{n:9}  {label}")
        for fn in affected[label][:3]:
            print(f"{'':11}  - {fn[:60]}")
        if len(affected[label]) > 3:
            print(f"{'':11}    ... and {len(affected[label]) - 3} more")

    # Group into families: a ranked list of individual modes hides that most
    # of them are the same underlying problem wearing different error text.
    FAMILY = {
        "types / declarations": ["unknown type name", "undeclared symbol",
                                 "redefined a scalar type", "redeclared a type",
                                 "wrong struct shape",
                                 "type conflict / empty object"],
        "our harness (now fixed)": ["extraction: fence leaked (ours)",
                                    "extraction: assembly as C (ours)",
                                    "truncation (ours)",
                                    "do-while token (prompt, fixed)"],
        "no candidate / no evidence": ["no compiling candidate at all",
                                       "KB context empty",
                                       "KB has no param-based evidence"],
    }
    print(f"\n{'-' * 74}")
    print("BY FAMILY (distinct functions affected by at least one mode):")
    for fam, labels in FAMILY.items():
        hit = set()
        for l in labels:
            hit.update(affected.get(l, []))
        print(f"   {len(hit):3} / {len(funcs)}  {fam}")

    print(f"\n{'-' * 74}")
    print(f"functions reaching score >= 100 : {exact_n}")
    print(f"functions in 99.5-100 (near)    : {len(near)}")

    if args.targets and near:
        print("\nHIGHEST-VALUE TARGETS -- closest non-exact functions:")
        for fn, sc in sorted(near, key=lambda x: -x[1])[:12]:
            print(f"   {sc:8.4f}  {fn}")
        print("\n(these are ranked by TRUE score, not the 2-decimal display "
              "that once hid a 99.999 as '100.00')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
