"""Score run-1 (D, E) with the reused A, B rows against PREREGISTRATION.md. Writes analysis.json."""
import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REUSED = HERE.parent / "uopt-guided-20260915/run-1/rows.jsonl"
MOTIVATING = "updateEndingCreditsSlashRisingStar"


def load(path, arms):
    rows = {}
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r["arm"] in arms:
            rows[(r["function"], r["arm"])] = r          # last row wins if an arm was re-run
    return rows


rows = {**load(REUSED, "AB"), **load(HERE / "run-1/rows.jsonl", "DE")}
by = defaultdict(dict)
for (function, arm), r in rows.items():
    by[function][arm] = r
complete = {f: a for f, a in by.items() if set(a) >= set("ABDE") and not any(x.get("error") for x in a.values())}
names = set(complete)


def exact(arm):
    return {f for f in names if complete[f][arm].get("exact")}


replication = []
rep_path = HERE / "replication/rows.jsonl"
if rep_path.is_file():
    for (function, arm), r in load(rep_path, "B").items():
        old = by[function].get("B", {})
        same = (r.get("exact"), r.get("compiles")) == (old.get("exact"), old.get("compiles"))
        replication.append({"function": function, "same": same, "now": [r.get("exact"), r.get("compiles")],
                            "reused": [old.get("exact"), old.get("compiles")]})

both = sorted(exact("B") & exact("D"))
result = {
    "functions_complete": len(complete),
    "errored_rows": sorted(f"{f}:{a}" for (f, a), r in rows.items() if r.get("error")),
    "replication": replication,
    "replication_ok": bool(replication) and all(r["same"] for r in replication),
    "exact": {arm: len(exact(arm)) for arm in "ABDE"},
    "D_not_B": sorted(exact("D") - exact("B")), "B_not_D": sorted(exact("B") - exact("D")),
    "E_not_A": sorted(exact("E") - exact("A")), "A_not_E": sorted(exact("A") - exact("E")),
    "median_compiles_B_D_on_both": [statistics.median(complete[f]["B"]["compiles"] for f in both),
                                    statistics.median(complete[f]["D"]["compiles"] for f in both)] if both else None,
}
result["predictions"] = {
    "1_D_exact_on_motivating": MOTIVATING in exact("D"),
    "2_D_ge_B": result["exact"]["D"] >= result["exact"]["B"],
    "3_B_not_D_le_1": len(result["B_not_D"]) <= 1,
}
(HERE / "analysis.json").write_text(json.dumps(result, indent=1))
print(json.dumps(result, indent=1))
