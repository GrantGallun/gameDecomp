"""Score arm F against the reused arm A rows (PREREGISTRATION.md). Writes analysis.json."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REUSED = HERE.parent / "uopt-guided-20260915/run-1/rows.jsonl"
FAMILY = HERE.parent / "progress-census-20260915/family-nop-li/rows.jsonl"


def load(path, arm=None):
    rows = {}
    for line in path.read_text().splitlines() if path.is_file() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if arm is None or r.get("arm") == arm:
            rows[r["function"]] = r
    return rows


a, f = load(REUSED, "A"), load(HERE / "run-1/rows.jsonl", "F")
names = sorted(n for n in f if n in a and not f[n].get("error") and not a[n].get("error"))
exact_a = {n for n in names if a[n].get("exact")}
exact_f = {n for n in names if f[n].get("exact")}
family = load(FAMILY)
aerial = {n: r for n, r in family.items() if "AerialTrick" in n and not r.get("error")}
result = {
    "functions_compared": len(names), "errored_F": sorted(n for n, r in f.items() if r.get("error")),
    "exact_A": len(exact_a), "exact_F": len(exact_f),
    "A_not_F": sorted(exact_a - exact_f), "F_not_A": sorted(exact_f - exact_a),
    "motivating_aerial_exact": sum(bool(r.get("exact")) for r in aerial.values()), "motivating_aerial_run": len(aerial),
    "motivating_all": {n: [r.get("exact"), r.get("compiles"), r.get("exact_path")] for n, r in sorted(family.items())},
}
result["predictions"] = {
    "1_no_loss": not result["A_not_F"] and len(names) == 145,
    "2_half_of_aerial_exact": bool(aerial) and result["motivating_aerial_exact"] * 2 >= len(aerial),
}
(HERE / "analysis.json").write_text(json.dumps(result, indent=1))
print(json.dumps(result, indent=1))
