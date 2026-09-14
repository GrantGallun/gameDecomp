"""Beam search over statement order, ranked by FAULT COUNT rather than score.

WHY NOT THE BYTE SCORE
    On renderRaceUiSingleTrailEffect both proposed statement swaps compiled,
    and both moved the two measures in OPPOSITE directions:

        swap 0   score 91.974 -> 91.779 (-0.195)   regalloc 39 -> 38 (-1)
        swap 1   score 91.974 -> 89.494 (-2.480)   regalloc 39 -> 35 (-4)

    dist.py charges for displaced bytes, so permuting two stores costs score
    even when the permutation is right. Every search in this project ranks by
    score, which means on an allocation-shaped residual it has been steering
    AWAY from the answer -- the best move looks like the worst one. That is a
    plausible reason register-shaped functions have never moved.

    So this ranks by how many INSTRUCTIONS are wrong. Registers are compared as
    registers; nothing is charged for byte displacement.

    `exact` still comes from the oracle alone and is the only thing that ends
    the search. The objective is a heuristic for WHERE TO LOOK; it decides
    nothing about whether a candidate matches.

    python3 -m eval.allocsearch --only renderRaceUiSingleTrailEffect
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from eval import matched
from solver import rewrites, signals, workspace


@dataclass
class State:
    code: str
    diff: str
    score: float
    faults: int
    trail: tuple[str, ...]
    receipt_id: int | None = None


def faults_of(sig: signals.Signals) -> int:
    """Wrong instructions, weighted by how hard the kind is to undo.

    A structural fault means an instruction has no counterpart at all or the
    control flow differs, which no statement swap should be creating; it is
    weighted so a swap cannot buy four register fixes with a new one for free.
    """
    # `ordering` is included: it was split out of layout/regalloc, and a sum
    # that enumerates kinds by name stops counting one the moment it is
    # named separately.
    return (2 * sig.structural + sig.regalloc + sig.layout + sig.immediate
            + sig.ordering)


def search(ws: Path, repo: Path, name: str, code: str, *, beam: int = 4,
           depth: int = 6, budget: int = 120, verbose: bool = True,
           conn=None, parent_attempt_id: int | None = None,
           run_id: str = "alloc-order"):
    """Returns (exact_source or None, best State, compiles_used)."""
    att = workspace.score(ws, repo, name, code)
    if not att.compiled:
        return None, None, 1
    if att.exact:
        return code, None, 1
    sig = signals.analyse(att.diff, att.score)
    start = State(code, att.diff, att.score, faults_of(sig), (),
                  parent_attempt_id)
    if not rewrites._allocation_shaped(att.diff or ""):
        return None, start, 1
    if verbose:
        print(f"  start faults={start.faults} score={start.score:.3f}")

    frontier = [start]
    best = start
    seen = {code}
    used = 1

    for d in range(depth):
        nxt: list[State] = []
        for st in frontier:
            for rw in rewrites.statement_order_rewrites(
                    st.code, st.diff, gate=False):
                if used >= budget:
                    break
                new_code = rw(st.code)
                if new_code == st.code or new_code in seen:
                    continue
                seen.add(new_code)
                a = workspace.score(
                    ws, repo, name, new_code, conn=conn, func=name,
                    strategy="alloc-order", run_id=run_id,
                    run_kind="deterministic-allocation-search",
                    iteration=d + 1, parent_attempt_id=st.receipt_id,
                    relation="statement-order", action=rw.label,
                    feedback=st.diff)
                used += 1
                if not a.compiled:
                    continue
                if a.exact:
                    if verbose:
                        print(f"  EXACT at depth {d+1}: "
                              f"{' -> '.join(st.trail + (rw.label,))}")
                    return new_code, best, used
                s = signals.analyse(a.diff, a.score)
                cand = State(new_code, a.diff, a.score, faults_of(s),
                             st.trail + (rw.label,), a.receipt_id)
                nxt.append(cand)
                if (cand.faults, -cand.score) < (best.faults, -best.score):
                    best = cand
        if not nxt:
            break
        nxt.sort(key=lambda s: (s.faults, -s.score))
        frontier = nxt[:beam]
        if verbose:
            print(f"  depth {d+1}: {len(nxt):3} compiled, best faults="
                  f"{frontier[0].faults} score={frontier[0].score:.3f}")
        if used >= budget:
            break
    return None, best, used


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--beam", type=int, default=4)
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--budget", type=int, default=120)
    ap.add_argument(
        "--attempt-id", type=int,
        help="start from this exact durable attempt receipt (requires --only)")
    args = ap.parse_args()

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db)
    done = matched.already_matched(conn)

    if args.attempt_id is not None:
        if len(args.only) != 1:
            ap.error("--attempt-id requires exactly one --only function")
        rows = conn.execute(
            "select f.name, a.score, a.source_code, a.id from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where a.id=? and f.name=? and a.compiled=1"
            " and a.source_code is not null",
            (args.attempt_id, args.only[0])).fetchall()
        if not rows:
            ap.error("--attempt-id is not a compiling receipt for --only")
    else:
        # A bare column beside max(score) relies on SQLite's special-case row
        # selection and leaves score ties arbitrary.  Select the exact newest
        # best receipt so every search has a reproducible root.
        rows = conn.execute(
            "select f.name, a.score, a.source_code, a.id from functions f"
            " join attempts a on a.id=(select a2.id from attempts a2"
            " where a2.func_addr=f.addr and a2.compiled=1"
            " and a2.source_code is not null"
            " order by a2.score desc, a2.id desc limit 1)").fetchall()

    targets = []
    for nm, sc, src, attempt_id in rows:
        if nm in done:
            continue
        if args.only and nm not in args.only:
            continue
        targets.append((nm, sc, src, attempt_id))
    targets.sort(key=lambda r: -r[1])
    print(f"{len(targets)} functions\n")

    exact_found = []
    for nm, sc, src, attempt_id in targets:
        ws = workspace.bootstrap(repo, nm)
        base = workspace.score(ws, repo, nm, src)
        if not base.compiled:
            continue
        if not rewrites._allocation_shaped(base.diff or ""):
            continue                      # not this generator's residual
        print(f"{nm:48} start {base.score:7.3f}")
        run_id = f"alloc-order-{nm}-{time.time_ns()}"
        hit, best, used = search(
            ws, repo, nm, src, beam=args.beam, depth=args.depth,
            budget=args.budget, conn=conn,
            parent_attempt_id=attempt_id, run_id=run_id)
        if hit:
            exact_found.append(nm)
            out = Path("matched_recovered") / f"{nm}.c"
            out.parent.mkdir(exist_ok=True)
            out.write_text(hit)
            print(f"  *** BYTE-EXACT *** ({used} compiles)")
        elif best is not None:
            print(f"  best faults={best.faults} score={best.score:.3f} "
                  f"({used} compiles)  {' -> '.join(best.trail) or '(start)'}")

    print("\n" + "=" * 66)
    print(f"NEW BYTE-EXACT MATCHES: {len(exact_found)}")
    for nm in exact_found:
        print(f"   {nm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
