"""Does portfolio selection beat scalar-best? Measured on stored candidates.

The pipeline anchors on the highest-scoring candidate and every repair pass
runs against that one. But score is a scalar over a residual containing very
different errors, and they are not equally recoverable: a wrong offset is
repairable by repad, a wrong branch shape is not.

So the claim to test is narrow and checkable:

    the scalar-best candidate is sometimes NOT the most repairable one,
    and repairing the Pareto set finds matches that repairing the best does not

Both arms use the SAME stored candidates, so there is no sampling variance --
the only difference is which candidate the repair passes are pointed at. No
model, no GPU.

    python3 -m eval.pareto_eval --db ~/decomp/kb-sbk1.sqlite \\
        --repo ~/decomp/sbk1 --floor 60
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from eval.repair import passes
from miner import globals_layout
from solver import signals, workspace


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=60.0)
    ap.add_argument("--beam", type=int, default=6)
    ap.add_argument("--repair", action="store_true",
                    help="also run the deterministic repair passes on each "
                         "arm's anchor and compare what they reach")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    done = matched_mod.already_matched(conn)
    objs = globals_layout.objects(conn)

    funcs = [r for r in conn.execute(
        "select f.name, max(a.score) as best from functions f"
        " join attempts a on a.func_addr = f.addr"
        " group by f.addr having best >= ?"
        " order by best desc", (args.floor,)).fetchall()
        if r[0] not in done]

    print(f"{len(funcs)} unmatched functions above {args.floor}\n")
    differs = same = 0
    rows_out = []
    wins_best, wins_pareto = [], []

    for name, best in funcs:
        cands = conn.execute(
            "select a.id, a.score, a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.compiled = 1 and a.source_code is not null"
            " order by a.score desc limit 12", (name,)).fetchall()
        if len(cands) < 2:
            continue

        ws = workspace.bootstrap(repo, name)
        scored = []
        for aid, sc, src in cands:
            att = workspace.score(ws, repo, name, src)
            if not att.compiled:
                continue
            sig = signals.analyse(att.diff, att.score, att.exact, True)
            scored.append(((aid, src), sig))
        if len(scored) < 2:
            continue

        scalar = max(scored, key=lambda ks: ks[1].score)
        front = signals.pareto(scored)[:args.beam]
        top = front[0] if front else scalar

        picked_differently = top[0][0] != scalar[0][0]
        differs += picked_differently
        same += not picked_differently

        print(f"{name[:44]:44} best={scalar[1].score:6.2f}"
              f" (struct {scalar[1].structural:3}, layout {scalar[1].layout:3})"
              f"   pareto-top={top[1].score:6.2f}"
              f" (struct {top[1].structural:3}, layout {top[1].layout:3})"
              f"{'   DIFFERENT' if picked_differently else ''}")

        rows_out.append({
            "function": name,
            "scalar": {"score": scalar[1].score,
                       "structural": scalar[1].structural,
                       "layout": scalar[1].layout},
            "pareto_top": {"score": top[1].score,
                           "structural": top[1].structural,
                           "layout": top[1].layout},
            "front": len(front), "differs": picked_differently})

        if not args.repair:
            continue

        # Point the SAME repair passes at each arm's anchor and compare.
        def best_reachable(src):
            hit = (False, 0.0)
            for label, code in passes(src, conn=conn, func=name, repo=repo,
                                      ws=ws, objs=objs):
                if label == "baseline":
                    continue
                a = workspace.score(ws, repo, name, code)
                if a.exact:
                    return True, a.score
                if a.compiled and a.score > hit[1]:
                    hit = (False, a.score)
            return hit

        ex_b, sc_b = best_reachable(scalar[0][1])
        ex_p, sc_p = best_reachable(top[0][1])
        if ex_b:
            wins_best.append(name)
        if ex_p:
            wins_pareto.append(name)
        if ex_b or ex_p:
            print(f"    repair: scalar {'EXACT' if ex_b else f'{sc_b:.2f}'}"
                  f"   pareto {'EXACT' if ex_p else f'{sc_p:.2f}'}")

    print(f"\n{'=' * 68}")
    print(f"functions where Pareto picked a DIFFERENT anchor: {differs}")
    print(f"functions where both picked the same:             {same}")
    if args.repair:
        print(f"\nmatches from repairing the scalar-best anchor : {len(wins_best)}")
        print(f"matches from repairing the Pareto-top anchor  : {len(wins_pareto)}")
        only_p = set(wins_pareto) - set(wins_best)
        print(f"found ONLY via Pareto: {len(only_p)} {sorted(only_p)}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"differs": differs, "same": same, "rows": rows_out,
             "wins_best": wins_best, "wins_pareto": wins_pareto}, indent=1))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
