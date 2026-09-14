"""Fault-ranked CEGIS beam search over every rewrite generator.

Two findings from this session make this the engine rather than another pass:

  1. THE OBJECTIVE WAS INVERTED. On residuals the allocator or the struct
     layout owns, the dist.py byte score moves OPPOSITE to the number of wrong
     instructions -- padding a struct or permuting two stores displaces every
     later byte and is charged for it. Measured: five statement swaps took
     renderRaceUiSingleTrailEffect from 41 faults to 34 while the score fell
     91.974 -> 85.987. A score-ranked search rejects every one of those steps.
     So this ranks by faults and lets the score fall.

  2. FIXES COMPOSE, AND THE SECOND ONE IS ONLY VISIBLE AFTER THE FIRST. Both
     hand-closed matches needed a PAIR of rewrites, and the second constraint
     could not be derived from the original residual -- with the arguments
     still swapped, two loads normalise alike and yield a bogus offset. So the
     residual is RE-DERIVED after every application and proposals come from the
     new one, never from a precomputed list.

`exact` comes from the oracle and nothing else. The fault count decides only
WHERE TO LOOK; it has no authority over whether a candidate matches, and a
candidate with zero faults that does not verify is simply wrong.

    python3 -m eval.faultsearch --tier medium --budget 150
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from eval import matched as matched_mod
from solver import (allocdiff, layoutstore, protostore, rewrites,
                    signals, workspace)


@dataclass
class State:
    code: str
    diff: str
    score: float
    faults: int
    trail: tuple[str, ...]


def faults_of(sig: signals.Signals, diff: str = "") -> int:
    """Wrong instructions, counting CAUSES rather than consequences.

    Structural faults are doubled so a repair cannot buy two offset fixes by
    introducing a branch-shape difference, which no generator can take back.

    Register faults get counted differently, and this is the point. One web
    given the wrong colour renames that value at every single reference:
    renderRaceUiSingleTrailEffect reports 31 register faults arising from 13
    mis-coloured webs, and its anchor alone accounts for four of them. Scoring
    the instructions makes a rewrite that fixes one web look like noise beside
    one that shuffles many, so the search was ranking by blast radius instead
    of by progress -- the same error as counting four branch-target faults
    that are one instruction-count difference seen four times.

    allocdiff derives the web count by comparing the two streams the oracle
    already produced. It is not the refuted `save` model; no prediction is
    involved, so it is safe to steer with.
    """
    base = 2 * sig.structural + sig.layout + sig.immediate + sig.reloc
    if diff and allocdiff.applicable(diff):
        return base + len(allocdiff.mismatches(diff))
    return base + sig.regalloc


def tier_of(size, insn) -> str:
    n = insn or (size or 0) // 4
    return ("tiny" if n < 20 else "small" if n < 60 else
            "medium" if n < 150 else "large" if n < 300 else "huge")


def search(ws: Path, repo: Path, name: str, code: str, *, beam: int = 3,
           depth: int = 5, budget: int = 150, verbose: bool = True,
           conn: sqlite3.Connection | None = None):
    """Returns (exact_source or None, best State, compiles_used)."""
    att = workspace.score(
        ws, repo, name, code, conn=conn, func=name,
        strategy="faultsearch-baseline",
        extra={"beam": beam, "depth": depth, "budget": budget},
    )
    used = 1
    if not att.compiled:
        return None, None, used
    if att.exact:
        return code, None, used

    sig = signals.analyse(att.diff, att.score, att.exact)
    start = State(code, att.diff, att.score,
                  faults_of(sig, att.diff), ())
    frontier, best, seen = [start], start, {code}
    if verbose:
        print(f"  start faults={start.faults} score={start.score:.3f}")

    for d in range(depth):
        nxt: list[State] = []
        for st in frontier:
            if used >= budget:
                break
            for rw in rewrites.propose(st.code, st.diff):
                if used >= budget:
                    break
                new_code = rw(st.code)
                if new_code == st.code or new_code in seen:
                    continue
                seen.add(new_code)
                trail = st.trail + (rw.label,)
                a = workspace.score(
                    ws, repo, name, new_code, conn=conn, func=name,
                    strategy=f"faultsearch-d{d + 1}",
                    extra={"trail": trail, "rewrite_kind": rw.kind,
                           "beam": beam, "depth": depth,
                           "budget": budget},
                )
                used += 1
                if not a.compiled:
                    continue
                if a.exact:
                    if verbose:
                        print("  EXACT: "
                              + " -> ".join(st.trail + (rw.label,)))
                    return new_code, best, used
                s = signals.analyse(a.diff, a.score, a.exact)
                cand = State(new_code, a.diff, a.score,
                             faults_of(s, a.diff), trail)
                nxt.append(cand)
                if cand.faults < best.faults:
                    best = cand
        if not nxt:
            break
        nxt.sort(key=lambda s: (s.faults, -s.score))
        frontier = nxt[:beam]
        if verbose:
            print(f"  depth {d+1}: {len(nxt):3} compiled, "
                  f"best faults={frontier[0].faults} "
                  f"score={frontier[0].score:.3f}")
        if used >= budget:
            break
    return None, best, used


def _explain(st: State, show: int = 10) -> None:
    """What is LEFT when the search runs out, by kind and by line.

    A plateau is only useful if it says which generator is missing. The fault
    total does not: 28 faults that are all structural need control-flow work,
    28 that are all registers need an allocation lever, and those are entirely
    different projects.
    """
    sig = signals.analyse(st.diff, st.score)
    print(f"     RESIDUAL: structural={sig.structural} regalloc={sig.regalloc}"
          f" offset={sig.offset} width={sig.width} immediate={sig.immediate}"
          f" reloc={sig.reloc}")
    pairs, n_minus, n_plus = signals._pairs(st.diff)
    print(f"     lines: {n_minus} expected / {n_plus} produced,"
          f" {len(pairs)} paired")
    for a, b in pairs[:show]:
        print(f"       -{a:<36} +{b}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--tier", default="")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--beam", type=int, default=3)
    ap.add_argument("--depth", type=int, default=5)
    ap.add_argument("--budget", type=int, default=150)
    ap.add_argument("--max-functions", type=int, default=0)
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--prototypes", action="store_true",
                    help="give callers their callees' verified signatures")
    ap.add_argument("--include-recovered", action="store_true",
                    help="admit signatures from functions RECOVERED from "
                         "target source, not just solved ones")
    ap.add_argument("--shared-layout", action="store_true",
                    help="pool struct offsets across functions "
                         "from logged residuals")
    args = ap.parse_args()

    repo = Path(args.repo)
    # Every attempt is logged, and other harnesses log to the same file, so a
    # writer WILL be found holding the lock. Without a busy timeout sqlite
    # raises immediately: a 400-compile sweep died two functions from the end
    # of the tier with "database is locked", losing the whole run.
    conn = sqlite3.connect(args.db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    done = matched_mod.already_matched(conn)

    rows = conn.execute(
        "select f.name, f.size, f.insn_count, max(a.score) from functions f"
        " join attempts a on a.func_addr = f.addr"
        " where a.compiled = 1 and a.source_code is not null"
        " group by f.addr").fetchall()

    targets = []
    for name, size, insn, best in rows:
        if name in done:
            continue
        if args.tier and tier_of(size, insn) != args.tier:
            continue
        if args.only and name not in args.only:
            continue
        targets.append((name, best))
    if args.prototypes:
        st = protostore.load_from_db(
            conn, include_recovered=args.include_recovered)
        src = "solved+recovered" if args.include_recovered else "solved only"
        print(f"prototype store: {st['signatures']} verified signatures "
              f"({src})")
    if args.shared_layout:
        stats = layoutstore.load_from_db(conn)
        print(f"shared layout store: {stats['structs']} structs, "
              f"{stats['offsets']} offsets, "
              f"{stats['multi_function']} pinned by 2+ functions")
    targets.sort(key=lambda r: -r[1])
    if args.max_functions:
        targets = targets[:args.max_functions]
    print(f"{len(targets)} functions, budget {args.budget} compiles each\n")

    hits, improved = [], []
    t0 = time.time()
    for name, best in targets:
        src = conn.execute(
            "select a.source_code from attempts a join functions f"
            " on f.addr = a.func_addr where f.name = ? and a.compiled = 1"
            " and a.source_code is not null order by a.score desc limit 1",
            (name,)).fetchone()[0]
        try:
            ws = workspace.bootstrap(repo, name)
        except Exception as exc:                   # noqa: BLE001
            print(f"{name:<46} bootstrap failed: {exc}")
            continue
        # A function must never be repaired from its OWN logged residuals,
        # or the transfer being measured is circular.
        layoutstore.set_exclusion(name if args.shared_layout else "")
        print(f"{name:<46} start {best:7.3f}")
        hit, st, used = search(ws, repo, name, src, beam=args.beam,
                               depth=args.depth, budget=args.budget,
                               conn=conn)
        if hit:
            hits.append((name, hit))
            print(f"  *** BYTE-EXACT *** ({used} compiles)")
            out = Path("matched_recovered") / f"{name}.c"
            out.parent.mkdir(exist_ok=True)
            out.write_text(hit, encoding="utf-8")
            print(f"  written to {out}")
        elif st is not None:
            if st.trail:
                improved.append((name, st))
                print(f"  best faults={st.faults} score={st.score:.3f} "
                      f"({used} compiles)")
                for step in st.trail:
                    print(f"     {step}")
            if args.explain:
                _explain(st)

    print("\n" + "=" * 70)
    print(f"NEW BYTE-EXACT MATCHES: {len(hits)}   "
          f"improved-not-matched: {len(improved)}   "
          f"elapsed {time.time()-t0:.0f}s")
    for name, _ in hits:
        print(f"   {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
