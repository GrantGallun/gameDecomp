"""Which unsolved best nodes still contain m2c's M2C_MEMCPY_ALIGNED pseudo-call, with its arguments (exploration)."""
import json
import re
from pathlib import Path

import census

d = json.loads((census.HERE / "census.json").read_text())
for r in d["rows"]:
    row = json.loads((census.E / "rows" / f"{r['function']}--routed.json").read_text())
    world = json.loads(Path(row["world"]).read_text())["world"]
    src = next(n for n in world["nodes"] if n["id"] == row["best_id"])["source"]
    calls = re.findall(r"M2C_MEMCPY_ALIGNED\(([^;]*)\);", src)
    if calls:
        print(f"{r['function'][:40]:40s} {r['score']:6.2f} {r['class']:13s} {calls}")
