"""How close is every function, not just how many matched.

Match count is a step function and it has not moved all session, which makes it
a bad instrument for steering: a function at 99.999 and one at 0.0 both count
as "not matched". This reports the BEST score ever achieved per function, by
tier, so the shape of the remaining work is visible -- how many are one
instruction away versus how many have never compiled at all.

Best-ever, not last-run: scores are best-of-N over a sampling process, so the
question "how close has this function ever come" is the one that says whether a
deterministic repair pass could finish it.

    python3 -m eval.distance --db ~/decomp/kb-sbk1.sqlite
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

from eval import matched as matched_mod

# Bands chosen around what the tooling can actually do, not round numbers:
#   >=99.9  one or two instructions out; a deterministic pass can plausibly close it
#   >=99    a handful of instructions; repad/tracefix territory
#   >=95    the permuter's old band -- refuted as a rule, but still near
#   >=80    right shape, wrong details
#   >0      compiles, structurally wrong
#   ==0     never produced a compiling candidate
BANDS = [
    (100.0, "MATCHED"),
    (99.9, "99.9+  (1-2 instructions)"),
    (99.0, "99.0+  (a few instructions)"),
    (95.0, "95.0+"),
    (80.0, "80.0+"),
    (0.0001, ">0     (compiles, wrong)"),
    (-1.0, "0      (never compiled)"),
]
TIERS = ["tiny", "small", "medium", "large", "huge"]


def band(score: float) -> str:
    for floor, label in BANDS:
        if score >= floor:
            return label
    return BANDS[-1][1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--tier", default="", help="only this tier")
    ap.add_argument("--list-near", type=float, default=0.0,
                    help="list every function at or above this score")
    args = ap.parse_args()

    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    cols = {r[1] for r in conn.execute("pragma table_info(functions)")}
    tier_col = "tier" if "tier" in cols else ""

    rows = conn.execute(
        "select f.name, " + (f"f.{tier_col}, " if tier_col else "'?', ") +
        " f.size, max(a.score), sum(a.compiled), count(*)"
        " from functions f join attempts a on a.func_addr = f.addr"
        " group by f.addr").fetchall()

    def tier_of(t, size):
        if t and t in TIERS:
            return t
        n = (size or 0) // 4                       # instructions
        return ("tiny" if n < 20 else "small" if n < 60 else
                "medium" if n < 150 else "large" if n < 300 else "huge")

    done = matched_mod.already_matched(conn)
    per: dict = defaultdict(lambda: defaultdict(list))
    for name, t, size, best, ncomp, natt in rows:
        tr = tier_of(t, size)
        if args.tier and tr != args.tier:
            continue
        # A match recorded only as a file in matched_recovered/ has logged
        # attempts that top out below 100, so banding on max(score) alone puts
        # a SOLVED function in the "99.9+" row and invites re-solving it.
        b = "MATCHED" if name in done else band(best or 0.0)
        per[tr][b].append((name, best or 0.0, ncomp or 0))

    labels = [l for _, l in BANDS]
    width = max(len(l) for l in labels) + 2
    tiers = [t for t in TIERS if t in per] + \
            [t for t in per if t not in TIERS]

    print(f"\n{'band':<{width}}" + "".join(f"{t:>9}" for t in tiers) + "    total")
    print("-" * (width + 9 * len(tiers) + 9))
    for label in labels:
        cells = [len(per[t].get(label, [])) for t in tiers]
        if not sum(cells):
            continue
        print(f"{label:<{width}}" + "".join(f"{c:>9}" for c in cells)
              + f"{sum(cells):>9}")
    print("-" * (width + 9 * len(tiers) + 9))
    totals = [sum(len(v) for v in per[t].values()) for t in tiers]
    print(f"{'attempted':<{width}}" + "".join(f"{c:>9}" for c in totals)
          + f"{sum(totals):>9}")

    matched = [sum(len(per[t].get('MATCHED', [])) for t in tiers)]
    near = sum(len(per[t].get(l, [])) for t in tiers
               for l in ("99.9+  (1-2 instructions)",
                         "99.0+  (a few instructions)"))
    print(f"\nmatched: {matched[0]}   within a few instructions: {near}")

    if args.list_near:
        print(f"\nfunctions at or above {args.list_near}:")
        near_rows = sorted(
            ((n, s, c, t) for t in tiers for lst in per[t].values()
             for n, s, c in lst if s >= args.list_near and s < 100.0),
            key=lambda r: -r[1])
        for n, s, c, t in near_rows:
            print(f"  {s:8.3f}  {t:7} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
