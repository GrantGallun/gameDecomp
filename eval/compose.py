"""Batch harness for deterministic repair across many functions.

The search itself is solver/repair.py -- it belongs to the solver so that
pipeline.solve can run it, which was the point of an external review's central
finding: the machinery producing every recent match lived in eval scripts and
never ran in production.

eval/diffloop.py applies one rewrite per round and stops when a step fails to
improve, so a fix needing two simultaneous changes is unreachable by
construction. Both matches recovered after an external review were exactly
that shape:

    loop bound 4 -> 5           99.962      neither alone
    8 bytes before menuState    99.962      reaches exact
    BOTH                        EXACT

Each rewrite moves the score by four hundredths of a point. Greedy hill
climbing cannot see either as progress, and this project recorded that finding
months of work ago -- "a function at 99.6% has SEVERAL small interacting
errors" -- and kept building single-step repairs anyway.

METHOD
    score every proposed rewrite alone, then try PAIRS. A rewrite is kept for
    composition if it does not make things worse: the two matches needed
    rewrites worth +0.04 each, which is indistinguishable from noise, so
    filtering on "improves" would have discarded them.

    The oracle verifies every combination. Nothing here decides a match.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import repair, workspace


# The search itself lives in solver/repair.py so the SOLVER owns it and
# the pipeline can call it. This module is the batch harness around it;
# an external review's central finding was that the machinery producing
# matches sat in eval scripts and never ran in production.
search = repair.search


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=60.0)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    done = matched_mod.already_matched(conn)

    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip()]
    else:
        names = [r[0] for r in conn.execute(
            "select f.name, max(a.score) as best from functions f"
            " join attempts a on a.func_addr = f.addr"
            " where a.compiled = 1 group by f.addr having best >= ?"
            " order by best desc", (args.floor,)).fetchall()
            if r[0] not in done]

    print(f"{len(names)} functions\n")
    wins, improved = [], []
    for name in names:
        # The best NON-exact candidate. Selecting the best outright picked a
        # source that was already byte-exact once receipts existed, so the
        # search "found" matches it had been handed -- a vacuous test. If a
        # function already has an exact candidate there is nothing to compose.
        row = conn.execute(
            "select a.score, a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.compiled = 1 and a.source_code is not null"
            "   and a.score < 100 order by a.score desc limit 1",
            (name,)).fetchone()
        if not row:
            continue
        start, src = row
        ws = workspace.bootstrap(repo, name)
        print(f"{name[:48]:48} start {start:.3f}", flush=True)
        att, best_src, log = search(repo, name, src, ws, conn=conn)
        for line in log:
            if "EXACT" in line or "improved" in line:
                print(f"      {line}", flush=True)
        if att.exact:
            wins.append((name, start))
            out = Path("matched_recovered") / f"{name}.c"
            out.parent.mkdir(exist_ok=True)
            out.write_text(best_src)
            print(f"      -> BYTE-EXACT, wrote {out}", flush=True)
        elif att.score > start + 0.0005:
            improved.append((name, start, att.score))

    print(f"\n{'=' * 66}")
    print(f"NEW BYTE-EXACT MATCHES: {len(wins)}")
    for n, s in wins:
        print(f"   {n}   {s:.3f} -> EXACT")
    print(f"improved, not matched: {len(improved)}")
    for n, a, b in improved:
        print(f"   {n}   {a:.3f} -> {b:.3f}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"matches": [{"function": n, "was": s} for n, s in wins],
             "improved": [{"function": n, "was": a, "now": b}
                          for n, a, b in improved]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
