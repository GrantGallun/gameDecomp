"""Save drawMenuAsciiTextDefaultScale's best node and its improving retype as a fire-test fixture."""
import hashlib
import json
from pathlib import Path

H = Path(__file__).resolve().parent
res = {json.loads(l)["source_sha256"]: json.loads(l)
       for l in (H.parent / "population-transfer-20260922/probes.jsonl").read_text().splitlines() if '"narrow:' in l}
probes = [p for p in json.loads((H / "probes-narrow.json").read_text()) if p["function"] == "drawMenuAsciiTextDefaultScale"]
best = max(probes, key=lambda p: res[hashlib.sha256(p["source"].encode()).hexdigest()]["score"])
row = json.loads((Path.home() / "decomp/experiments/width-population-20260923/rows/drawMenuAsciiTextDefaultScale--width.json").read_text())
world = json.loads(Path(row["world"]).read_text())["world"]
node = max((n for n in world["nodes"] if n["verdict"]["compiled"] and not n["verdict"]["exact"]), key=lambda n: n["verdict"]["score"])
fx = {"function": "drawMenuAsciiTextDefaultScale", "source": node["source"], "diff": node["verdict"].get("diff") or "",
      "source_attribution": node["verdict"].get("source_attribution"), "improving": best["source"]}
Path("/mnt/c/Code/gameDecomp/tests/fixtures/evidence_site_narrow_local.json").write_text(json.dumps(fx))
print("saved", res[hashlib.sha256(best["source"].encode()).hexdigest()]["score"], node["verdict"]["score"])
