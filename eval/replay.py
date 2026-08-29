"""Re-compile stored candidates under a source transform. No model, no GPU.

Every text-level fix so far was measured by generating a fresh run and
comparing means -- 45 minutes of GPU, and the answer arrived buried in sampling
noise. The include-fix run is the cautionary case: it produced +4 compiles and
a mean that moved -7.3 the wrong way, and disentangling the two took longer
than the run did.

But a source transform is deterministic, and 2,834 candidates are already in
the database. Applying the transform to stored text and recompiling is a PAIRED
test on identical inputs: the only difference between arms is the transform, so
there is no sampling variance to see through at all. It costs compiles, not
generations, and it answers the question the eval run cannot.

What it does NOT measure: whether a repaired candidate scores well, only
whether it compiles and what it scores if it does. A transform that fixes
syntax cannot make a structurally wrong candidate right, and this harness will
say so plainly rather than letting a compile-rate gain read as progress.

    python3 -m eval.replay --db ~/decomp/kb-sbk1.sqlite --repo ~/decomp/sbk1 \\
        --since-run eval/results/inc_hard_v1.json --transform c89
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

from solver import c89, llm, workspace

TRANSFORMS = {
    "c89": c89.to_c89,
    "includes": llm.strip_unresolvable_includes,
    "none": lambda s: s,
}


def window_of(results: str) -> tuple[float, float]:
    """The wall-clock span of a finished run, from its own files."""
    return (os.path.getmtime(results + ".fingerprint.json"),
            os.path.getmtime(results) + 120)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--since-run", required=True,
                    help="results .json whose attempts to replay")
    ap.add_argument("--transform", default="c89", choices=sorted(TRANSFORMS))
    ap.add_argument("--only-failing", action="store_true", default=True,
                    help="replay only attempts that did not compile")
    ap.add_argument("--include-compiling", dest="only_failing",
                    action="store_false",
                    help="also replay attempts that DID compile, to prove the "
                         "transform does not break working candidates")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    lo, hi = window_of(args.since_run)
    fn = TRANSFORMS[args.transform]

    q = ("select f.name, a.compiled, a.score, a.source_code"
         " from attempts a join functions f on f.addr = a.func_addr"
         " where a.created_at between ? and ? and a.source_code is not null")
    if args.only_failing:
        q += " and a.compiled = 0"
    q += " order by f.name, a.id"
    rows = conn.execute(q, (lo, hi)).fetchall()
    if args.limit:
        rows = rows[:args.limit]

    # Only rows the transform actually changes can move the number; the rest
    # are a control group that must come out identical.
    changed = [(n, ok, sc, s) for n, ok, sc, s in rows if fn(s) != s]
    print(f"attempts in window: {len(rows)}   transform changes: "
          f"{len(changed)} ({100*len(changed)/max(1,len(rows)):.1f}%)")
    print(f"transform: {args.transform}\n")

    fixed, still, broke, kept = [], [], [], []
    t0 = time.time()
    for i, (name, was_ok, was_score, src) in enumerate(changed, 1):
        new = fn(src)
        ws = workspace.bootstrap(repo, name)
        att = workspace.score(ws, repo, name, new)
        if was_ok and not att.compiled:
            broke.append((name, was_score))
            tag = "BROKE"
        elif was_ok:
            kept.append((name, was_score, att.score))
            tag = "kept"
        elif att.compiled:
            fixed.append((name, att.score, att.exact))
            tag = f"FIXED -> {att.score:.2f}" + ("  EXACT" if att.exact else "")
        else:
            still.append(name)
            tag = "still fails"
        print(f"  [{i:3}/{len(changed)}] {name[:44]:44} {tag}")

    n = len(changed)
    print(f"\n{'=' * 66}")
    print(f"replayed {n} changed candidates in {time.time()-t0:.0f}s")
    if not args.only_failing:
        print(f"  previously compiling, still compiling : {len(kept)}")
        print(f"  previously compiling, NOW BROKEN      : {len(broke)}")
    print(f"  previously failing, now COMPILES      : {len(fixed)}")
    print(f"  previously failing, still fails       : {len(still)}")
    if n:
        base = len(fixed) + len(still)
        if base:
            print(f"\n  repair rate on failing candidates: "
                  f"{100*len(fixed)/base:.1f}%")
    if fixed:
        sc = sorted((s for _, s, _ in fixed), reverse=True)
        print(f"  scores of repaired candidates: max {sc[0]:.2f}  "
              f"median {sc[len(sc)//2]:.2f}  "
              f"exact {sum(1 for _, _, e in fixed if e)}")
        print("\n  A repaired candidate that scores near zero is a syntax win")
        print("  and nothing more -- the structural gap is untouched.")
    if broke:
        print(f"\n  REGRESSIONS -- the transform must not do this:")
        for nm, s in broke[:10]:
            print(f"    {nm} was {s:.2f}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"transform": args.transform, "changed": n,
             "fixed": [{"function": f, "score": s, "exact": e}
                       for f, s, e in fixed],
             "still_failing": still, "broke": broke}, indent=1))
        print(f"\nwrote {args.out}")
    return 1 if broke else 0


if __name__ == "__main__":
    raise SystemExit(main())
