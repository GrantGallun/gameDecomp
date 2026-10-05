"""Why did the fixed operator drop the two functions where v1's raise performed? Class of the range and site gate."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from solver import alloc_inverter  # noqa: E402
import action_check  # noqa: E402
import run  # noqa: E402

for name, x in (("freeRelocatableHeapBlock", "temp_v0"), ("func_80058C00", "temp_v0")):
    row = next(r for p in (run.E / "restart-round3-20260923/rows").glob("*.json")
               if (r := json.loads(p.read_text())).get("function") == name)
    world = json.loads(Path(row["world"]).read_text())["world"]
    src = next(n for n in world["nodes"] if n["id"] == row["best_id"])["source"]
    report, proc = action_check.trace(run.workspace(name), name, src)
    off = next(o for o, v in alloc_inverter.local_offsets(src, name).items() if v == x)
    entry = next(e for e in report["ranges"] if e["kind"] == "M" and e["offset"] == off)
    print(name, {k: entry.get(k) for k in ("class", "actual", "desired", "votes", "order", "selected_by_model",
                                            "blockers")})
    for site in alloc_inverter._assignment_sites(src, name, x):
        line = src[src.rfind("\n", 0, site - 1) + 1:site]
        print("   site", site, line.strip(), "counts:", alloc_inverter._read_counts(src, name, x, site))
    rec = proc.ranges[entry["lr"]]
    print("   preferences", rec.preferences(), "forbidden", sorted(rec.forbidden or ()))
