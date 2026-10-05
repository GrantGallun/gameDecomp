"""New member->offset candidates at each unsolved function's best node (index-form run), for compilation."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import evidence_site  # noqa: E402

HERE = Path(__file__).resolve().parent
ROWS = Path.home() / "decomp/experiments/index-form-population-20260923/rows"
probes, fixtures = [], []
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
        if "field:offset" in label and "(*(" in cand and "(*(" not in best["source"].split(label.split("@")[-1])[0][:0] + "":
            if cand.count("(u8 *)(") + cand.count("(unsigned char *)(") > best["source"].count("(u8 *)(") + best["source"].count("(unsigned char *)("):
                probes.append({"function": row["function"], "label": f"member_offset:{label}", "source": cand,
                               "parent_score": v["score"]})
                if len(fixtures) < 40:
                    fixtures.append({"function": row["function"], "source": best["source"], "diff": v.get("diff") or "",
                                     "source_attribution": v.get("source_attribution"), "candidate": cand})
(HERE / "probes-member.json").write_text(json.dumps(probes, indent=1))
(HERE / "fixture-candidates.json").write_text(json.dumps(fixtures))
print(len(probes), "candidates in", len({p["function"] for p in probes}), "functions")
