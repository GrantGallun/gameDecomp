"""Run the permuter on every near-miss candidate, regardless of verdict.

The pipeline permutes only when route_for() says "permute", and on the hard set
that never fired: 39 of 39 functions routed to reshape because the workbench
returned a structural verdict. I recorded that as correct-by-design, on the
strength of one function permuted for 400s.

That was over-generalised. The bank's refutation is narrow and still holds --
a >=95% score does NOT imply a register-allocation residual -- but "the
permuter cannot help THIS function" is not "the permuter should never run".
The one directly comparable published benchmark (Macabeus, 60 functions,
Sonic Advance 3 + Animal Forest) reports decomp-permuter matching 7 of 60
functions by refining model-generated code, and it runs on every candidate
rather than on a score band.

So: permute everything above a floor and let the oracle answer. CPU-bound, no
model, no GPU.

    python3 -m eval.permute_sweep --db ~/decomp/kb-sbk1.sqlite \\
        --repo ~/decomp/sbk1 --floor 80 --seconds 180
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from eval import matched as matched_mod
from solver import pipeline, workspace


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=80.0)
    ap.add_argument(
        "--only", action="append", default=[],
        help="restrict work to this function (repeatable)")
    ap.add_argument("--seconds", type=int, default=180,
                    help="permuter budget per function")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    done = matched_mod.already_matched(conn)

    rows = [r for r in conn.execute(
        "select f.name, max(a.score) as best from functions f"
        " join attempts a on a.func_addr = f.addr"
        " group by f.addr having best >= ?"
        " order by best desc", (args.floor,)).fetchall()
        if r[0] not in done]
    if args.only:
        wanted = set(args.only)
        rows = [row for row in rows if row[0] in wanted]
        missing = sorted(wanted - {row[0] for row in rows})
        if missing:
            ap.error("--only target is matched, unknown, or below --floor: "
                     + ", ".join(missing))

    print(f"permuting {len(rows)} near misses at {args.seconds}s each "
          f"(~{len(rows) * args.seconds / 60:.0f} min)\n", flush=True)

    wins, improved, flat = [], [], []
    for i, (name, best) in enumerate(rows, 1):
        row = conn.execute(
            "select a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.score = ? and a.source_code is not null"
            " limit 1", (name, best)).fetchone()
        if not row:
            continue

        ws = workspace.bootstrap(repo, name)
        src = ws / f"{name}.c"
        src.write_text(row[0])
        t0 = time.time()
        try:
            score, exact, code = pipeline.run_permuter(
                repo, name, src.relative_to(repo), args.seconds,
                ws=ws, conn=conn)
        except Exception as exc:
            print(f"  [{i:2}/{len(rows)}] {name[:42]:42} ERROR "
                  f"{type(exc).__name__}", flush=True)
            continue

        # run_permuter re-verifies through the oracle; exact comes from there,
        # never from the permuter's own directory names, which are dist.py COST
        # values and have already fabricated a match once in this project.
        if exact:
            wins.append((name, best, score))
            tag = f"{best:7.3f} -> EXACT"
        elif score > best + 0.0005:
            improved.append((name, best, score))
            tag = f"{best:7.3f} -> {score:7.3f}"
        else:
            flat.append(name)
            tag = f"{best:7.3f} -> no improvement"
        print(f"  [{i:2}/{len(rows)}] {name[:42]:42} {tag}"
              f"   ({time.time() - t0:.0f}s)", flush=True)

    print(f"\n{'=' * 66}")
    print(f"NEW BYTE-EXACT MATCHES: {len(wins)}")
    for n, b, s in wins:
        print(f"   {n}   {b:.3f} -> EXACT")
    print(f"improved, not matched: {len(improved)}")
    for n, b, s in improved:
        print(f"   {n}   {b:.3f} -> {s:.3f}")
    print(f"unchanged: {len(flat)}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"seconds": args.seconds,
             "matches": [{"function": n, "was": b} for n, b, _s in wins],
             "improved": [{"function": n, "was": b, "now": s}
                          for n, b, s in improved],
             "unchanged": flat}, indent=1))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
