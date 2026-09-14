"""Simulate bottom-up wavefront solving before spending GPU on it.

THE PROPOSED ALGORITHM
    Solve the leaves. Promote a function to the frontier once EVERY function it
    calls is solved. When the frontier stops advancing, sweep the lowest
    unsolved level again with more effort, and repeat.

    That is a topological wavefront, and it is how a human decompiler works. It
    is also cheap to evaluate before running it, because the call graph is
    already known and per-tier solve rates are already measured -- so the yield
    can be projected instead of discovered after a long run.

WHAT THIS DOES AND DOES NOT SETTLE
    It answers "how far does the wave travel", which is a property of the GRAPH
    and the solve rates, and it locates where the frontier stalls. It cannot
    say whether solving a callee makes its caller EASIER -- that is a causal
    claim, and the one measurement so far says the effect is nil for the
    signatures currently in hand (68 of 83 return void, which is codegen
    neutral). So the wave is justified here as an ordering, and its yield is
    what this projects.

    Rates are quoted from what has actually been attempted, which was sampled
    for DIFFICULTY, so they understate a random function of the same tier. The
    sensitivity sweep is there because of that, not for decoration.

    python3 -m eval.wavefront
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from eval import callgraph, matched as matched_mod


def tier_of(size, insn) -> str:
    n = insn or (size or 0) // 4
    return ("tiny" if n < 20 else "small" if n < 60 else
            "medium" if n < 150 else "large" if n < 300 else "huge")


def recovered_set(conn) -> set:
    """Functions whose exact source was RECOVERED from the target repository.

    They must not count toward a solve rate. 55 of the 138 matches came from
    tools/score_repo_function.py, which reads the reference decomp; counting
    them would project the system's future yield from answers it was handed.
    """
    return {n for (n,) in conn.execute(
        "select distinct f.name from attempts a"
        " join functions f on f.addr = a.func_addr"
        " where a.exact = 1 and ("
        "   a.strategy like '%history-recovery%'"
        "   or a.strategy like '%historical-provenance%'"
        "   or a.strategy like '%symbol-restoration%')")}


def measured_rates(conn, meta: dict, done: set) -> dict:
    """Solved / attempted per tier, from history."""
    attempted = {n for (n,) in conn.execute(
        "select distinct f.name from attempts a"
        " join functions f on f.addr = a.func_addr")}
    att = Counter()
    win = Counter()
    for n in attempted:
        if n not in meta:
            continue
        t = tier_of(*meta[n])
        att[t] += 1
        if n in done:
            win[t] += 1
    return {t: (win[t] / att[t] if att[t] else 0.0) for t in
            ("tiny", "small", "medium", "large", "huge")}, att, win


def simulate(callees: dict, meta: dict, done: set, rates: dict,
             passes: int = 3, seed: int = 0) -> tuple[list, set]:
    """Run the wavefront. Returns (per-round log, final solved set).

    SOLVABILITY IS DRAWN ONCE PER FUNCTION, not once per attempt. The first
    version of this re-drew every round, so a medium function at p=0.64 got
    roughly twenty-four independent chances and the wave reached 99.9% of the
    binary. That was an artefact of modelling unlimited retries at constant
    probability. Whether a function yields is a property OF THE FUNCTION: a
    tier rate is the fraction of functions solvable at all, not the chance
    that one more go will work. Every plateau measured this session says
    retrying a function that has resisted is close to free and close to
    useless.

    `passes` still models sweeping a stalled level with more effort, but as a
    modest widening of the solvable fraction rather than a fresh draw.
    """
    rng = random.Random(seed)
    # one fixed draw per function, compared against the tier rate below
    draw = {n: rng.random() for n in meta}
    solved = set(done)
    nodes = set(meta)
    log = []
    for sweep in range(1, passes + 1):
        boost = 1.0 + 0.25 * (sweep - 1)
        while True:
            ready = [n for n in nodes
                     if n not in solved
                     and all(c in solved or c not in nodes
                             for c in callees.get(n, ()))]
            if not ready:
                break
            gained = 0
            for n in ready:
                p = min(1.0, rates.get(tier_of(*meta[n]), 0.0) * boost)
                if draw[n] < p:
                    solved.add(n)
                    gained += 1
            log.append((sweep, len(ready), gained, len(solved)))
            if gained == 0:
                break                      # frontier cannot advance this sweep
    return log, solved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--trials", type=int, default=20)
    args = ap.parse_args()

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db, timeout=120)
    done = set(matched_mod.already_matched(conn, repo))
    callees, _callers = callgraph.edges(conn)
    meta = {n: (s, i) for n, s, i in conn.execute(
        "select name, size, insn_count from functions")}
    done &= set(meta)

    recovered = recovered_set(conn) & set(meta)
    solved_only = done - recovered
    rates, att, win = measured_rates(conn, meta, solved_only)
    print(f"excluding {len(recovered)} functions RECOVERED from "
          f"target source; rates are from what was SOLVED")
    print("measured solve rate by tier (attempted set was sampled for "
          "difficulty, so these are pessimistic):")
    for t in ("tiny", "small", "medium", "large", "huge"):
        print(f"   {t:<7} {win[t]:>4}/{att[t]:<4} = {100*rates[t]:5.1f}%")

    total = len(meta)
    print("")
    print(f"starting from the SOLVED set: {len(solved_only)} of {total}")

    print(f"\n{'scenario':<26}{'final solved':>14}{'of total':>10}"
          f"{'rounds':>8}")
    print("-" * 60)
    for label, scale in (("measured rates", 1.0),
                         ("rates x1.5", 1.5),
                         ("rates x2", 2.0),
                         ("tiny/small perfect", None)):
        finals = []
        rounds = []
        for trial in range(args.trials):
            r = dict(rates)
            if scale is None:
                r["tiny"] = r["small"] = 1.0
            else:
                r = {k: min(1.0, v * scale) for k, v in r.items()}
            log, sv = simulate(callees, meta, solved_only, r,
                               passes=args.passes, seed=trial)
            finals.append(len(sv))
            rounds.append(len(log))
        avg = sum(finals) / len(finals)
        print(f"{label:<26}{avg:>14.0f}{100*avg/total:>9.1f}%"
              f"{sum(rounds)/len(rounds):>8.0f}")

    # Where does it stall under measured rates?
    log, sv = simulate(callees, meta, solved_only, rates,
                       passes=args.passes, seed=0)
    stuck = [n for n in meta if n not in sv]
    by_tier = Counter(tier_of(*meta[n]) for n in stuck)
    blocked_by = Counter()
    for n in stuck:
        for c in callees.get(n, ()):
            if c in meta and c not in sv:
                blocked_by[c] += 1
    print(f"\nunder measured rates the wave stalls with {len(stuck)} unsolved")
    print("   by tier: " + ", ".join(f"{t}={by_tier[t]}" for t in
                                     ("tiny", "small", "medium", "large",
                                      "huge") if by_tier[t]))
    print("   functions blocking the most others at the stall point:")
    for name, n in blocked_by.most_common(8):
        print(f"      {name:<44} blocks {n}  ({tier_of(*meta[name])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
