"""Paired: continuation from the search best (control) vs from the best merged candidate (compose), 32 compiles each."""
import json
from pathlib import Path

H = Path(__file__).resolve().parent
arms = {}
for arm in ("control", "compose"):
    rows = {}
    for path in (Path.home() / f"decomp/experiments/continue-{arm}-20260923/rows").glob("*.json"):
        r = json.loads(path.read_text())
        rows[r["function"]] = r
    arms[arm] = rows
common = sorted(set(arms["control"]) & set(arms["compose"]))
ex = {a: sorted(f for f in common if arms[a][f].get("exact")) for a in arms}
gain = [f for f in ex["compose"] if f not in ex["control"]]
loss = [f for f in ex["control"] if f not in ex["compose"]]
open_ = [f for f in common if f not in ex["compose"] and f not in ex["control"]]
better = sum(arms["compose"][f]["best_score"] > arms["control"][f]["best_score"] for f in open_)
worse = sum(arms["compose"][f]["best_score"] < arms["control"][f]["best_score"] for f in open_)
out = {"functions": len(common), "exact": {a: ex[a] for a in ex}, "compose_gains": gain, "compose_losses": loss,
       "open_compose_better": better, "open_compose_worse": worse,
       "mean_best_delta": round(sum(arms["compose"][f]["best_score"] - arms["control"][f]["best_score"]
                                    for f in open_) / max(1, len(open_)), 3)}
(H / "continue_compare.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
