"""Label each 90+ function's aligned differences by mechanical cause (reuses failure-census classify rules).

    python3 eval/results/ninety-census-20260914/classify.py

Writes classes.json with single-cause groups, family presence and per-function rows,
plus bench health (fresh compile/exact disagreements with the stored node).
"""
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = (HERE.parents[0] / "failure-census-20260914" / "classify.py").read_text()
rules = {"__name__": "rules", "__file__": str(HERE.parents[0] / "failure-census-20260914" / "classify.py")}
exec(BASE[:BASE.index("\nrows = []")], rules)                # rule definitions only; the base report is not rewritten
labels, family = rules["labels"], rules["family"]

rows, health = [], Counter()
for path in sorted((HERE / "diffs").glob("*.json")):
    entry = json.loads(path.read_text())
    found = labels(entry)
    health["error" if entry.get("error") else "not_compiled" if not entry.get("compiled")
           else "exact" if entry.get("exact") else "compiled"] += 1
    n_register = (entry.get("compare") or {}).get("register_instructions") or 0
    rows.append({"function": entry["function"], "score": entry.get("score"), "fresh_score": entry.get("fresh_score"),
                 "instructions": entry.get("instructions"), "labels": found, "register_instructions": n_register,
                 "families": sorted({family(l) for l in found}), "semantic": entry.get("semantic"),
                 "certificate": entry.get("certificate_status"), "boundary": entry.get("boundary_error")})

groups = {}
for r in rows:
    causes = [f for f in r["families"] if f != "register"]
    key = ("register_only" if not causes else causes[0] + ("+register" if "register" in r["families"] else "")
           if len(causes) == 1 else "multi")
    groups.setdefault(key, []).append(r["function"])
report = {"functions": len(rows), "bench": dict(health),
          "single_cause_groups": {k: len(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))},
          "family_presence": dict(Counter(f for r in rows for f in r["families"]).most_common()),
          "label_presence": dict(Counter(l for r in rows for l in set(r["labels"])).most_common(45)),
          "two_cause_pairs": dict(Counter("+".join(sorted(f for f in r["families"] if f != "register"))
                                          for r in rows if len([f for f in r["families"] if f != "register"]) == 2).most_common(12))}
(HERE / "classes.json").write_text(json.dumps({**report, "groups": groups, "rows": rows}, indent=1))
print(json.dumps(report, indent=1))
