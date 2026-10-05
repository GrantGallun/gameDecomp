"""Build round starts: the best node of each still-unsolved function of a finished round's rows directory."""
import hashlib
import json
import sys
from pathlib import Path

PT = Path("/mnt/c/Code/gameDecomp/eval/results/population-transfer-20260922")
pop = {p["function"]: p for p in json.loads((PT / "population.json").read_text())}
rows_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
starts, solved = [], []
for path in sorted(rows_dir.glob("*.json")):
    row = json.loads(path.read_text())
    if row.get("exact"):
        solved.append(row["function"])
        continue
    if not row.get("world") or row["function"] not in pop:
        continue
    world = json.loads(Path(row["world"]).read_text())["world"]
    best = next(n for n in world["nodes"] if n["id"] == row["best_id"])
    base = {k: pop[row["function"]][k] for k in ("function", "addr", "insn_count", "source_attempt_id")}
    starts.append({**base, "source": best["source"], "source_sha256": hashlib.sha256(best["source"].encode()).hexdigest()})
out.write_text(json.dumps(starts, indent=1))
print(json.dumps({"starts": len(starts), "already_exact": len(solved)}))
