"""Failure-mode analysis of a regalloc search directory.

    python eval/results/regalloc-20260913/analyze.py search-1 > search-1-analysis.json

Per family: how often it compiled, how often it was INERT (same gradient and
signatures as its parent: probably identical codegen), better / worse / exact,
and which signatures it fixed or introduced. Per function: the best state's
remaining signatures and register substitutions -- the residual that no current
family reaches, grouped so new generators can be aimed at the largest groups.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

folder = Path(__file__).resolve().parent / sys.argv[1]
summaries = [json.loads(line) for line in (folder / "summary.jsonl").open()]
families = defaultdict(lambda: Counter())
fixed, introduced = defaultdict(Counter), defaultdict(Counter)
remaining_signatures, remaining_substitutions = Counter(), Counter()
shapes = Counter()
per_function = []

for summary in summaries:
    rows = [json.loads(line) for line in (folder / f"{summary['function']}.jsonl").open()]
    best = None
    for row in rows:
        if row.get("depth", 0) == 0:
            best = row
            continue
        family = row.get("family", "?")
        stats = families[family]
        stats["variants"] += 1
        if not row.get("compiled"):
            stats["no_compile"] += 1
            continue
        verdict = (row.get("delta") or {}).get("verdict")
        if row.get("exact"):
            stats["exact"] += 1
        elif verdict == "same" and not (row["delta"]["fixed"] or row["delta"]["introduced"]):
            stats["inert"] += 1
        elif verdict:
            stats[verdict] += 1
        for name, count in (row.get("delta") or {}).get("fixed", {}).items():
            fixed[family][name] += count
        for name, count in (row.get("delta") or {}).get("introduced", {}).items():
            introduced[family][name] += count
        if row.get("compiled") and best and tuple(row.get("gradient", [9e9])) < tuple(best.get("gradient", [9e9])):
            best = row
    if summary.get("outcome") == "exact":
        per_function.append({"function": summary["function"], "outcome": "exact", "family": summary.get("family"),
                             "label": summary.get("exact_label"), "depth": summary.get("exact_depth")})
        continue
    if not best or "signatures" not in best:
        per_function.append({"function": summary["function"], "outcome": summary.get("outcome")})
        continue
    for name, count in best["signatures"].items():
        remaining_signatures[name] += count
    for pair, count in best.get("substitutions", {}).items():
        remaining_substitutions[pair] += count
    # The dominant residual shape per function, for grouping.
    top = max(best["signatures"], key=best["signatures"].get) if best["signatures"] else "non_register"
    shapes[top] += 1
    per_function.append({"function": summary["function"], "outcome": summary.get("outcome"),
                         "baseline_gradient": summary.get("baseline_gradient"), "best_gradient": best.get("gradient"),
                         "best_signatures": best.get("signatures"), "best_substitutions": best.get("substitutions"),
                         "example_differences": [d for d in best.get("differences", []) if isinstance(d.get("target"), str)][:4]})

report = {
    "functions": len(summaries),
    "outcomes": dict(Counter(s.get("outcome") for s in summaries)),
    "exact_by_family": dict(Counter(s.get("family") for s in summaries if s.get("outcome") == "exact")),
    "compiles": sum(s.get("compiles", 0) for s in summaries),
    "families": {name: dict(stats) for name, stats in families.items()},
    "fixed_by_family": {name: dict(c) for name, c in fixed.items()},
    "introduced_by_family": {name: dict(c) for name, c in introduced.items()},
    "unexact_dominant_signature": dict(shapes),
    "unexact_remaining_signatures": dict(remaining_signatures),
    "unexact_top_substitutions": dict(remaining_substitutions.most_common(25)),
    "per_function": per_function,
}
print(json.dumps(report, indent=1))
