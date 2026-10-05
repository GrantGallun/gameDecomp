"""Round 8 replay: new narrow-local retypes at each unsolved best node (width run)."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import evidence_site  # noqa: E402

HERE = Path(__file__).resolve().parent
ROWS = Path.home() / "decomp/experiments/width-population-20260923/rows"
probes = []
for path in sorted(ROWS.glob("*.json")):
    row = json.loads(path.read_text())
    if row.get("exact") or not row.get("world"):
        continue
    world = json.loads(Path(row["world"]).read_text())["world"]
    nodes = [n for n in world["nodes"] if n["verdict"]["compiled"] and not n["verdict"]["exact"]]
    if not nodes:
        continue
    best = max(nodes, key=lambda n: n["verdict"]["score"])
    v = best["verdict"]
    for label, cand in evidence_site.variants(best["source"], row["function"], v.get("diff") or "", v.get("source_attribution")):
        if label.startswith("evidence_site:extra:") and cand.count("s32") > best["source"].count("s32"):
            probes.append({"function": row["function"], "label": f"narrow:{label}", "source": cand, "parent_score": v["score"]})
(HERE / "probes-narrow.json").write_text(json.dumps(probes, indent=1))
print(len(probes), "candidates in", len({p["function"] for p in probes}), "functions")
