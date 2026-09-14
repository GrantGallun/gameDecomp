"""Rank unmatched functions by how close they are to falling, not by score.

Score is a bad work queue. A function at 70% whose entire residual is two
offset faults is closer to done than one at 96% whose residual is thirty branch
faults, because we own a deterministic fix for the first and nothing for the
second.

So rank by TRACTABILITY:

    generally repairable   offset, width, unambiguous immediate
    conditionally repaired relocation addends and named data symbols
    no implemented pass    register allocation
    structural             branch shape and missing instructions (some narrow
                           signatures have generators, not the whole class)

and put the smallest all-repairable residuals first. Clearing those is worth
more than the score delta suggests: each one that lands adds a verified source
to the sibling pool, which is the flywheel that is currently empty.

    python3 -m eval.triage --db ~/decomp/kb-sbk1.sqlite --repo ~/decomp/sbk1
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import signals, workspace


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=0.0)
    ap.add_argument("--show", type=int, default=6,
                    help="residual lines to print for the top entries")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    done = matched_mod.already_matched(conn)

    rows = [r for r in conn.execute(
        "select f.name, max(a.score) as best from functions f"
        " join attempts a on a.func_addr = f.addr"
        " where a.compiled = 1 group by f.addr having best >= ?"
        " order by best desc", (args.floor,)).fetchall()
        if r[0] not in done]

    scored = []
    for name, best in rows:
        src = conn.execute(
            "select a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.score = ? and a.source_code is not null"
            " limit 1", (name, best)).fetchone()
        if not src:
            continue
        ws = workspace.bootstrap(repo, name)
        att = workspace.score(ws, repo, name, src[0])
        if not att.compiled:
            continue
        s = signals.analyse(att.diff, att.score, att.exact, True)
        scored.append((name, s, att))

    # A zero-structural candidate with forty register faults is not a two-fault
    # candidate. The old key omitted every unimplemented fault and repeatedly
    # promoted renderRaceUiSingleTrailEffect as the easiest target.
    scored.sort(key=lambda t: (t[1].structural > 0, t[1].structural,
                               t[1].no_repair_implemented > 0,
                               t[1].no_repair_implemented,
                               t[1].conditional_repair,
                               t[1].repairable, -t[1].score))

    print(f"{len(scored)} unmatched functions with a compiling candidate\n")
    print(f"{'function':44} {'score':>7} {'faults':>6} "
          f"{'off':>4} {'wid':>4} {'imm':>4} {'rel':>4} {'reg':>4} "
          f"{'STRUCT':>7}")
    print("-" * 93)
    for name, s, _att in scored:
        total = (s.layout + s.immediate + s.reloc + s.regalloc
                 + s.structural)
        print(f"{name[:44]:44} {s.score:7.3f} {total:6} "
              f"{s.offset:4} {s.width:4} {s.immediate:4} {s.reloc:4} "
              f"{s.regalloc:4} {s.structural:7}")

    clean = [t for t in scored if t[1].structural == 0]
    print(f"\nZERO structural faults (may still contain unimplemented "
          f"register faults): {len(clean)}")
    for name, s, att in clean[:args.show]:
        print(f"\n  {name}   {s.score:.3f}   "
              f"offset {s.offset}  width {s.width}  reloc {s.reloc}  "
              f"immediate {s.immediate}  regalloc {s.regalloc}")
        shown = 0
        for line in (att.diff or "").splitlines():
            if line[:1] in "+-" and not line.startswith(("+++", "---")):
                print(f"      {line[:84]}")
                shown += 1
            if shown >= 8:
                break

    tiny = [t for t in scored
            if t[1].structural == 0
            and t[1].no_repair_implemented == 0
            and t[1].repairable + t[1].conditional_repair <= 4]
    print(f"\nSMALLEST TRACTABLE (zero structural, <=4 faults): {len(tiny)}")
    for name, s, _a in tiny:
        print(f"   {name}   {s.score:.3f}   "
              f"{s.repairable + s.conditional_repair} faults")

    if args.out:
        Path(args.out).write_text(json.dumps(
            [{"function": n, "score": s.score, "structural": s.structural,
              "offset": s.offset, "width": s.width,
              "immediate": s.immediate, "reloc": s.reloc,
              "regalloc": s.regalloc} for n, s, _a in scored], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
