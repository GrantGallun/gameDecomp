"""Score run-1 against PREREGISTRATION.md. Writes analysis.json next to this file."""
import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
rows = []
for line in (HERE / "run-1/rows.jsonl").read_text().splitlines():
    try:
        rows.append(json.loads(line))
    except ValueError:
        pass
by = defaultdict(dict)
for r in rows:
    by[r["function"]][r["arm"]] = r                   # last row wins if an arm was re-run
complete = {f: a for f, a in by.items() if set(a) == {"A", "B", "C"} and not any(x.get("error") for x in a.values())}
errors = [r for r in rows if r.get("error")]
motivating = {f for f, a in complete.items() if a["A"].get("motivating")}


def exact(arm, names):
    return sorted(f for f in names if complete[f][arm].get("exact"))


names = set(complete)
others = names - motivating
result = {
    "functions_complete": len(complete), "rows": len(rows), "errored_rows": len(errors),
    "exact": {arm: len(exact(arm, names)) for arm in "ABC"},
    "exact_excluding_motivating": {arm: len(exact(arm, others)) for arm in "ABC"},
    "exact_on_motivating": {arm: exact(arm, motivating) for arm in "ABC"},
    "B_not_A": sorted(set(exact("B", names)) - set(exact("A", names))),
    "A_not_B": sorted(set(exact("A", names)) - set(exact("B", names))),
    "C_not_B": sorted(set(exact("C", names)) - set(exact("B", names))),
    "B_not_C": sorted(set(exact("B", names)) - set(exact("C", names))),
}
both = [f for f in names if complete[f]["B"].get("exact") and complete[f]["C"].get("exact")]
compiles_b = [complete[f]["B"]["compiles"] for f in both]
compiles_c = [complete[f]["C"]["compiles"] for f in both]
result["exact_in_both_B_and_C"] = len(both)
result["median_compiles_B"] = statistics.median(compiles_b) if both else None
result["median_compiles_C"] = statistics.median(compiles_c) if both else None
result["C_fewer_same_more"] = [sum(c < b for b, c in zip(compiles_b, compiles_c)),
                               sum(c == b for b, c in zip(compiles_b, compiles_c)),
                               sum(c > b for b, c in zip(compiles_b, compiles_c))]
result["trace_calls_C_total"] = sum(complete[f]["C"].get("trace_calls", 0) for f in names)
first = defaultdict(int)
for f in names:
    for cls in complete[f]["C"].get("first_decisions", [])[:1]:
        first[cls] += 1
result["C_baseline_first_decision"] = dict(first)
result["predictions"] = {
    "1_B_ge_A_and_B_finds_motivating": result["exact"]["B"] >= result["exact"]["A"]
                                        and len(result["exact_on_motivating"]["B"]) == len(motivating),
    "2_C_ge_B": result["exact"]["C"] >= result["exact"]["B"],
    "3_C_fewer_median_compiles": (result["median_compiles_C"] is not None
                                  and result["median_compiles_C"] < result["median_compiles_B"]),
}
(HERE / "analysis.json").write_text(json.dumps(result, indent=1))
print(json.dumps(result, indent=1))
