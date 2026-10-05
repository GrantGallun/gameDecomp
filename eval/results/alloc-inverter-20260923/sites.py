"""Print the assignment line at each raise site of the blocked-check functions."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from solver import alloc_inverter  # noqa: E402
import run  # noqa: E402

for path in sorted((run.E / "restart-round3-20260923/rows").glob("*.json")):
    row = json.loads(path.read_text())
    if row.get("exact") or not row.get("world"):
        continue
    world = json.loads(Path(row["world"]).read_text())["world"]
    source = next(n for n in world["nodes"] if n["id"] == row["best_id"])["source"]
    name = row["function"]
    if name not in sys.argv[1:]:
        continue
    b, e = alloc_inverter._body(source, name)
    print("==", name, "locals:", alloc_inverter.local_offsets(source, name))
    for x in ("var_a1", "temp_v0", "var_s0_2", "var_s1", "var_s1_2", "var_s3"):
        for site in alloc_inverter._assignment_sites(source, name, x):
            line = source[source.rfind("\n", 0, site - 1) + 1:site]
            print(f"  {x}@{site}: {line.strip()}")
