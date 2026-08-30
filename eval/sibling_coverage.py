"""How many unmatched functions have a close, already-matched twin?

The bank's CONFIRMED medium-failures-are-structural ends by naming the only
remaining levers: "sibling mirroring, decomposition, or model capability".
solver/siblings.py implements the first one and every run this session set
siblings=False, so it has never been measured.

Chris Lewis, working the same problem by hand with agents, reports function
similarity scoring (ethteck's coddog, exact bounded Levenshtein rather than
embeddings) as a technique that WORKED, while reporting the same three failure
modes we measured independently -- large functions refused outright, struct
field offsets, and C89 declaration rules.

Before spending a GPU run on it, count how often a usable twin actually exists.
A lever that applies to two functions is not a lever.

CONTAMINATION NOTE: siblings.historical_filter restricts to functions matched
BEFORE the target in git history, which is the honest setting -- the reference
repo is 100% complete and a solver mid-project would not have all of it. This
tool reports both, because the gap between them IS the optimism.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import siblings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=0.0,
                    help="only functions whose best score is at least this")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ours", action="store_true",
                    help="restrict the sibling pool to functions WE have "
                         "matched. This is the honest cold-start number: a "
                         "real solver has its own matches, not the reference "
                         "project's finished repo. The --historical filter "
                         "cannot answer this -- it derives order from git "
                         "subjects that cover only 777 of ~2113 functions, "
                         "and none of the current targets.")
    ap.add_argument("--historical", action="store_true",
                    help="restrict to functions matched BEFORE the target in "
                         "the reference project's own history -- the honest "
                         "setting, since the reference repo is 100%% complete "
                         "and a mid-project solver would not have all of it")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    done = matched_mod.already_matched(conn)

    rows = [r for r in conn.execute(
        "select f.name, max(a.score) as best from functions f"
        " join attempts a on a.func_addr = f.addr"
        " group by f.addr having best >= ? and best < 100.0"
        " order by best desc", (args.floor,)).fetchall()
        if r[0] not in done]
    if args.limit:
        rows = rows[:args.limit]

    print(f"{len(rows)} unmatched functions with a logged attempt\n")
    print(f"{'function':44} {'best':>7} {'top sib':>8}  name")
    bands = {"0.90+": 0, "0.75-0.90": 0, "0.45-0.75": 0, "none": 0}
    for name, best in rows:
        try:
            got = siblings.find(repo, name, top=8,
                                historical=args.historical)
            if args.ours:
                got = [g for g in got if g[0] in done]
            got = got[:1]
        except Exception as exc:
            print(f"{name[:44]:44} {best:7.3f}  ERROR {type(exc).__name__}")
            continue
        if not got:
            bands["none"] += 1
            print(f"{name[:44]:44} {best:7.3f} {'--':>8}")
            continue
        sname, score, _p = got[0]
        band = ("0.90+" if score >= 0.90 else
                "0.75-0.90" if score >= 0.75 else "0.45-0.75")
        bands[band] += 1
        print(f"{name[:44]:44} {best:7.3f} {score:8.2f}  {sname[:34]}")

    tot = sum(bands.values()) or 1
    print("\ntop-sibling similarity, over unmatched functions:")
    for k in ("0.90+", "0.75-0.90", "0.45-0.75", "none"):
        print(f"  {bands[k]:4}  ({100*bands[k]/tot:4.1f}%)  {k}")
    usable = bands["0.90+"] + bands["0.75-0.90"]
    print(f"\nfunctions with a twin at 0.75 or better: {usable} "
          f"({100*usable/tot:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
