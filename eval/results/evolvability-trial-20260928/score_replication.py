"""Score the replication against PREREGISTRATION-replication.md (R1-R4). Read-only.

    python3 score_replication.py [experiment_dir]
"""
import collections
import json
import sqlite3
import sys
from pathlib import Path

E = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/grant/decomp/experiments/evolvability-replication-20260928")
rows = [json.loads(p.read_text()) for p in sorted(E.glob("run-*/**/result.json"))]
arms = ["production", "mutation_count", "evolvability"]
by = {(r["arm"], r["function"]): r for r in rows}
functions = sorted({r["function"] for r in rows})
complete = [f for f in functions if all((a, f) in by for a in arms)]
print(f"runs {len(rows)}; functions {len(functions)}; complete (all three arms) {len(complete)}")


def gradient(r):
    if r.get("exact"):
        return (0, 0, 0)
    g = r.get("best_compiled_gradient")
    return tuple(g) if g else (float("inf"),)


exact = {a: {f for f in complete if by[(a, f)].get("exact")} for a in arms}
for a in arms:
    print(f"  {a:15s} matched {len(exact[a]):2d}: {sorted(exact[a])}")
print("R1 mutation_count > production:", len(exact["mutation_count"]) > len(exact["production"]),
      "| evolvability > production:", len(exact["evolvability"]) > len(exact["production"]))
lost = {a: sorted(exact["production"] - exact[a]) for a in arms[1:]}
print("R2 production matches the others missed:", lost, "->", not any(lost.values()))
unmatched = [f for f in complete if f not in exact["production"]]
early = [f for f in unmatched if (by[("production", f)].get("budget_spent") or 0) <= by[("production", f)]["budget"] / 2]
print(f"R3 production non-matches stopping with >= half the budget unused: {len(early)}/{len(unmatched)}",
      "->", len(early) > len(unmatched) / 2)
cmp = collections.Counter()
for f in complete:
    ge, gp = gradient(by[("evolvability", f)]), gradient(by[("production", f)])
    cmp["better" if ge < gp else "worse" if ge > gp else "tie"] += 1
print(f"R4 evolvability vs production gradient: {dict(cmp)} ->", cmp["better"] > cmp["worse"])
for a in arms:
    spent = sorted(by[(a, f)].get("budget_spent") or 0 for f in complete)
    if spent:
        print(f"  {a:15s} budget spent median {spent[len(spent) // 2]:.1f}")
new = set().union(*exact.values())
if new:
    ledger = set()
    for db in ("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite", "/home/grant/decomp/kb-sbk1.sqlite"):
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as k:
            ledger |= {n for (n,) in k.execute("select distinct f.name from attempts a join functions f "
                                                "on f.addr=a.func_addr where a.exact=1")}
    print("matched functions already in a ledger:", sorted(new & ledger) or "none")
    print("assistance of matches:", {f: by[next(a for a in arms if f in exact[a]), f].get("assistance") for f in sorted(new)})
