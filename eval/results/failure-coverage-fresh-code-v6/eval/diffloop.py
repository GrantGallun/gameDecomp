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
import time
from pathlib import Path

from eval import matched as matched_mod
from solver import diffrepair, signals, workspace


def run_one(repo: Path, name: str, src: str, ws: Path, rounds: int = 8,
            verbose: bool = True, conn=None, run_id: str = ""):
    """Iterate until exact, stable, or worse, with an arm-health receipt."""
    att = workspace.score(
        ws, repo, f"{name}_diffloop_base", src, conn=conn, func=name,
        strategy="diffrepair-baseline", run_id=run_id)
    health = {"activated": 0, "compiled_candidates": 0,
              "broken_candidates": 0, "terminal": ""}
    if not att.compiled:
        health["terminal"] = "baseline_build_failure"
        return att, src, ["baseline did not compile"], health
    best_att, best_src = att, src
    log = []

    for i in range(rounds):
        if best_att.exact:
            break
        new, changed, info = diffrepair.repair(best_src, best_att.diff)
        if not changed:
            log.append(f"round {i+1}: no constraints to apply")
            health["terminal"] = ("not_applicable" if not health["activated"]
                                  else "stable")
            break
        health["activated"] += 1
        cand = workspace.score(
            ws, repo, f"{name}_diffloop_{i + 1}", new, conn=conn, func=name,
            strategy="diffrepair", run_id=run_id,
            extra={"round": i + 1, "constraints": info["constraints"],
                   "dropped": info["dropped"]})
        if cand.compiled:
            health["compiled_candidates"] += 1
        else:
            health["broken_candidates"] += 1
        s = signals.analyse(cand.diff, cand.score, cand.exact, cand.compiled)
        log.append(f"round {i+1}: {info['constraints']} constraints"
                   f" ({info['dropped']} dropped) -> "
                   f"{'EXACT' if cand.exact else f'{cand.score:.3f}'}"
                   f" [struct {s.structural} off {s.offset} wid {s.width}]"
                   if cand.compiled else
                   f"round {i+1}: repair broke the build")
        if verbose:
            print(f"      {log[-1]}", flush=True)
        if not cand.compiled:
            health["terminal"] = "repair_build_failure"
            break
        if cand.score < best_att.score - 0.0005:
            health["terminal"] = "regressed"
            break                       # keep the better previous candidate
        best_att, best_src = cand, new
        if cand.exact:
            health["terminal"] = "exact"
            break
    else:
        health["terminal"] = "round_limit"
    if not health["terminal"]:
        health["terminal"] = "exact" if best_att.exact else "stable"
    return best_att, best_src, log, health


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
            " group by f.addr having best >= ?"
            " order by best desc", (args.floor,)).fetchall()
            if r[0] not in done]

    print(f"{len(rows)} functions\n")
    wins, improved, flat, not_applicable, invalid = [], [], [], [], []
    run_rows = []
    run_id = f"diffloop-{int(time.time())}"
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
        att, best_src, log, health = run_one(
            repo, name, src, ws, args.rounds, conn=conn, run_id=run_id)

        if att.exact:
            wins.append((name, start, att.score))
            print(f"      -> BYTE-EXACT (oracle verified)", flush=True)
            out = Path("matched_recovered") / f"{name}.c"
            out.parent.mkdir(exist_ok=True)
            out.write_text(best_src)
        elif att.score > start + 0.0005:
            improved.append((name, start, att.score))
        elif health["terminal"] == "not_applicable":
            not_applicable.append(name)
        elif health["terminal"] == "baseline_build_failure":
            invalid.append(name)
        else:
            flat.append(name)
        run_rows.append({"function": name, "was": start, "now": att.score,
                         "exact": att.exact, **health, "log": log})

    print(f"\n{'=' * 68}")
    print(f"NEW BYTE-EXACT MATCHES: {len(wins)}")
    for n, a, b in wins:
        print(f"   {n}   {a:.3f} -> EXACT")
    print(f"improved, not matched:  {len(improved)}")
    for n, a, b in improved:
        print(f"   {n}   {a:.3f} -> {b:.3f}")
    print(f"unchanged:              {len(flat)}")
    print(f"arm not applicable:     {len(not_applicable)}")
    print(f"invalid baseline:       {len(invalid)}")

    activations = sum(r["activated"] for r in run_rows)
    compiled_candidates = sum(r["compiled_candidates"] for r in run_rows)
    print(f"\narm health: {activations} activation(s), "
          f"{compiled_candidates} compiling candidate(s)")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"matches": [{"function": n, "was": a} for n, a, _b in wins],
             "improved": [{"function": n, "was": a, "now": b}
                          for n, a, b in improved],
             "unchanged": flat, "not_applicable": not_applicable,
             "invalid": invalid,
             "health": {"activations": activations,
                        "compiled_candidates": compiled_candidates},
             "runs": run_rows}, indent=1))

    if not run_rows or activations == 0:
        print("\nEXPERIMENT INVALID: the repair arm never activated; this run "
              "cannot support a claim about repair effectiveness.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
