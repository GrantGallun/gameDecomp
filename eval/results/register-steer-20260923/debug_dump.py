"""Read-only: is verdict['dump'] the same text the workspace's normalized dump holds?"""
import json
from pathlib import Path
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import uopt_attribution  # noqa: E402

census = json.loads((Path(__file__).resolve().parent / "census.json").read_text())
c = next(x for x in census if x["function"] == "MusStop")
row = json.loads((Path.home() / "decomp/experiments/gated-population-20260923/rows/MusStop--gated.json").read_text())
world = json.loads(Path(row["world"]).read_text())["world"]
node = next(n for n in world["nodes"] if n["id"] == c["best_id"])
dump = node["verdict"]["dump"]
print("verdict dump head:\n", dump[:400])
ws = Path.home() / "decomp/experiments/gated-population-20260923/ws/MusStop/gated/nonmatchings/MusStop"
f = ws / "MusStop_object_dump_normalized.s"
print("file exists", f.exists(), "\nfile head:\n", f.read_text()[:400] if f.exists() else "")
print("target head:\n", (ws / "target_object_dump_normalized.s").read_text()[:300])
try:
    uopt_attribution.attribute(dump, c["trace"]["level5"], c["trace"]["level6"], c["trace"]["ugen"], "MusStop")
    print("attribute OK with verdict dump")
except uopt_attribution.Declined as e:
    print("declined:", e)
