"""Round 5 scoping: what are the uncovered `field:immediate` residuals? Opcode and operand shape at best nodes."""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import evidence_site  # noqa: E402

ROWS = Path.home() / "decomp/experiments/member-offset-population-20260923/rows"
kinds, fns = collections.Counter(), collections.defaultdict(set)
examples = collections.defaultdict(list)
for path in sorted(ROWS.glob("*.json")):
    row = json.loads(path.read_text())
    if row.get("exact") or not row.get("world"):
        continue
    world = json.loads(Path(row["world"]).read_text())["world"]
    nodes = [n for n in world["nodes"] if n["verdict"]["compiled"] and not n["verdict"]["exact"]]
    if not nodes:
        continue
    best = max(nodes, key=lambda n: n["verdict"]["score"])
    for sig, line, target, cand in evidence_site.sites(best["verdict"].get("diff") or "", best["verdict"].get("source_attribution")):
        if sig != "field:immediate":
            continue
        op = cand.split()[0]
        key = "frame:" + op if ",sp," in cand.replace(" ", "") or cand.replace(" ", "").endswith("(sp)") else op
        kinds[key] += 1
        fns[key].add(row["function"])
        if len(examples[key]) < 3:
            examples[key].append((row["function"], target, cand))
for k, n in kinds.most_common(14):
    print(f"{k:14} {n:4} instances {len(fns[k]):3} functions  e.g. {examples[k][0]}")
