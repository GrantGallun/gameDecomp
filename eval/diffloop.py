"""Iterate diff-directed repair until the oracle stops changing its mind.

One pass is not enough. Inserting padding ahead of an early field shifts every
later field, which produces a NEW diff with new constraints -- so the loop is

    compile -> diff -> constraints -> repair -> compile -> ...

until the candidate matches, stops changing, or stops improving. That is a
counterexample-guided loop with the oracle as the counterexample source, which
is the shape an external review argued this project should have.

Guards, because a loop that rewrites its own input can wander:
  - stop if the score falls; the previous candidate is kept
  - stop if the source stops changing
  - hard iteration cap

No model, no GPU.

    python3 -m eval.diffloop --db ~/decomp/kb-sbk1.sqlite --repo ~/decomp/sbk1
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import diffrepair, signals, workspace


def run_one(repo: Path, name: str, src: str, ws: Path, rounds: int = 8,
            verbose: bool = True):
    """Iterate until exact, stable, or worse. Returns (best_att, best_src, log)."""
    att = workspace.score(ws, repo, name, src)
    if not att.compiled:
        return att, src, ["did not compile"]
    best_att, best_src = att, src
    log = []

    for i in range(rounds):
        if best_att.exact:
            break
        new, changed, info = diffrepair.repair(best_src, best_att.diff)
        if not changed:
            log.append(f"round {i+1}: no constraints to apply")
            break
        cand = workspace.score(ws, repo, name, new)
        s = signals.analyse(cand.diff, cand.score, cand.exact, cand.compiled)
        log.append(f"round {i+1}: {info['constraints']} constraints"
                   f" ({info['dropped']} dropped) -> "
                   f"{'EXACT' if cand.exact else f'{cand.score:.3f}'}"
                   f" [struct {s.structural} off {s.offset} wid {s.width}]"
                   if cand.compiled else
                   f"round {i+1}: repair broke the build")
        if verbose:
            print(f"      {log[-1]}", flush=True)
        if not cand.compiled or cand.score < best_att.score - 0.0005:
            break                       # keep the better previous candidate
        best_att, best_src = cand, new
        if cand.exact:
            break
    return best_att, best_src, log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=60.0)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--only", default="", help="comma-separated function names")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    done = matched_mod.already_matched(conn)

    if args.only:
        wanted = [n.strip() for n in args.only.split(",") if n.strip()]
        rows = [(n, 0.0) for n in wanted]
    else:
        rows = [r for r in conn.execute(
            "select f.name, max(a.score) as best from functions f"
            " join attempts a on a.func_addr = f.addr"
            " group by f.addr having best >= ? and best < 100.0"
            " order by best desc", (args.floor,)).fetchall()
            if r[0] not in done]

    print(f"{len(rows)} functions\n")
    wins, improved, flat = [], [], []
    for name, _b in rows:
        row = conn.execute(
            "select a.score, a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.compiled = 1 and a.source_code is not null"
            " order by a.score desc limit 1", (name,)).fetchone()
        if not row:
            continue
        start, src = row
        ws = workspace.bootstrap(repo, name)
        print(f"{name[:48]:48} start {start:.3f}", flush=True)
        att, best_src, _log = run_one(repo, name, src, ws, args.rounds)

        if att.exact:
            wins.append((name, start, att.score))
            print(f"      -> BYTE-EXACT (oracle verified)", flush=True)
            out = Path("matched_recovered") / f"{name}.c"
            out.parent.mkdir(exist_ok=True)
            out.write_text(best_src)
        elif att.score > start + 0.0005:
            improved.append((name, start, att.score))
        else:
            flat.append(name)

    print(f"\n{'=' * 68}")
    print(f"NEW BYTE-EXACT MATCHES: {len(wins)}")
    for n, a, b in wins:
        print(f"   {n}   {a:.3f} -> EXACT")
    print(f"improved, not matched:  {len(improved)}")
    for n, a, b in improved:
        print(f"   {n}   {a:.3f} -> {b:.3f}")
    print(f"unchanged:              {len(flat)}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"matches": [{"function": n, "was": a} for n, a, _b in wins],
             "improved": [{"function": n, "was": a, "now": b}
                          for n, a, b in improved],
             "unchanged": flat}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
