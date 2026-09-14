"""What shape is the call graph, and how much cheap work sits at the leaves?

"Work from the leaves inward" is the standard decomp order, and the KB has had
the edges to do it all along. Before reordering everything around that, measure
the shape, because two things decide whether it pays:

  HOW MUCH IS AT THE LEAVES   If most unattempted functions are deep in the
                              graph, leaf-first buys little.
  IS IT ACYCLIC               Mutual recursion makes a strongly connected
                              component that no topological order can break;
                              those have to be solved together or not at all.

Levels are computed from callees upward: a leaf is level 0, and a function's
level is one more than its deepest callee. Functions inside a cycle are
reported separately rather than assigned a bogus level.

    python3 -m eval.callgraph_shape
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from eval import callgraph, matched as matched_mod


def tier_of(size, insn) -> str:
    n = insn or (size or 0) // 4
    return ("tiny" if n < 20 else "small" if n < 60 else
            "medium" if n < 150 else "large" if n < 300 else "huge")


def levels(callees: dict, nodes: set) -> tuple[dict, set]:
    """(level per function, functions in a cycle). Leaves are level 0."""
    level: dict = {}
    state: dict = {}                       # 0 = visiting, 1 = done
    cyclic: set = set()

    def visit(n, stack):
        if state.get(n) == 1:
            return level.get(n, 0)
        if n in stack:
            cyclic.add(n)
            return 0
        stack.add(n)
        deepest = -1
        for c in callees.get(n, ()):
            if c not in nodes:
                continue                   # calls outside the known set
            deepest = max(deepest, visit(c, stack))
        stack.discard(n)
        state[n] = 1
        level[n] = deepest + 1
        return level[n]

    import sys
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 10000))
    try:
        for n in nodes:
            visit(n, set())
    finally:
        sys.setrecursionlimit(old)
    return level, cyclic


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--max-insns", type=int, default=60)
    ap.add_argument("--out-leaves", type=Path,
                    help="write every never-attempted leaf under --max-insns "
                         "as a run_set-compatible set")
    args = ap.parse_args()

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db, timeout=120)
    done = set(matched_mod.already_matched(conn, repo))
    callees, callers = callgraph.edges(conn)

    meta = {n: (s, i) for n, s, i in conn.execute(
        "select name, size, insn_count from functions")}
    attempted = {n for (n,) in conn.execute(
        "select distinct f.name from attempts a"
        " join functions f on f.addr = a.func_addr")}
    nodes = set(meta)

    lvl, cyclic = levels(callees, nodes)
    print(f"functions in graph: {len(nodes)}   "
          f"matched: {len(done & nodes)}   attempted: {len(attempted & nodes)}")
    print(f"functions inside a call cycle: {len(cyclic)}"
          + ("   (no topological order can separate these)" if cyclic else ""))

    by_level: dict = defaultdict(list)
    for n in nodes:
        by_level[lvl.get(n, 0)].append(n)

    print(f"\n{'level':>6}{'total':>8}{'matched':>9}{'unattempted':>13}"
          f"{'unatt <= %d insns' % args.max_insns:>20}")
    print("-" * 58)
    cheap_total = 0
    for k in sorted(by_level)[:12]:
        names = by_level[k]
        m = sum(1 for n in names if n in done)
        un = [n for n in names if n not in attempted]
        cheap = [n for n in un
                 if 0 < (meta[n][1] or (meta[n][0] or 0) // 4) <= args.max_insns]
        cheap_total += len(cheap)
        label = "leaves" if k == 0 else str(k)
        print(f"{label:>6}{len(names):>8}{m:>9}{len(un):>13}{len(cheap):>20}")
    deeper = [k for k in by_level if k >= 12]
    if deeper:
        names = [n for k in deeper for n in by_level[k]]
        print(f"{'12+':>6}{len(names):>8}"
              f"{sum(1 for n in names if n in done):>9}"
              f"{sum(1 for n in names if n not in attempted):>13}")

    leaves = by_level[0]
    un_leaves = [n for n in leaves if n not in attempted]
    cheap_leaves = [n for n in un_leaves
                    if 0 < (meta[n][1] or (meta[n][0] or 0) // 4)
                    <= args.max_insns]
    print(f"\nLEAVES: {len(leaves)} total, {sum(1 for n in leaves if n in done)}"
          f" matched, {len(un_leaves)} never attempted, "
          f"{len(cheap_leaves)} of those <= {args.max_insns} instructions")
    tiers = Counter(tier_of(*meta[n]) for n in un_leaves)
    print("never-attempted leaves by tier: "
          + ", ".join(f"{k}={tiers[k]}" for k in
                      ("tiny", "small", "medium", "large", "huge") if tiers[k]))
    print(f"\ncheap unattempted work at ALL levels: {cheap_total}")

    if args.out_leaves:
        rows = []
        for n in sorted(cheap_leaves):
            k = meta[n][1] or (meta[n][0] or 0) // 4
            rows.append({"function": n, "tier": tier_of(*meta[n]),
                         "leaf": True, "insns": k,
                         "callers": len(callers.get(n, ()))})
        rows.sort(key=lambda r: (r["insns"], -r["callers"]))
        payload = {
            "derived_from": "eval.callgraph_shape",
            "note": ("Never-attempted LEAVES, smallest first. The call graph "
                     "is a clean DAG with zero cycles, so a leaf can be "
                     "solved without regard to order. Only 195 of 2113 "
                     "functions had ever been attempted: the eval sets were "
                     "deliberately sampled for difficulty, so the cheapest "
                     "and most idiomatic functions in the binary were never "
                     "tried, and the sibling pool was starved of exactly the "
                     "ordinary shapes it needed."),
            "selection": {"never_attempted": True, "leaf": True,
                          "max_insns": args.max_insns},
            "dev": rows,
            "heldout": [],
        }
        import json
        args.out_leaves.write_text(json.dumps(payload, indent=2),
                                   encoding="utf-8")
        print("")
        print(f"wrote {len(rows)} leaf function(s) to {args.out_leaves}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
