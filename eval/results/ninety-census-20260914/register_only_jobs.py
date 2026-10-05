"""Job histories of the register_only 90+ group (reads classes.json + ninety.json)."""
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
classes = json.loads((HERE / "classes.json").read_text())
rows = {r["function"]: r for r in json.loads((HERE / "ninety.json").read_text())}
group = classes["groups"].get("register_only", [])
profiles = Counter()
for name in group:
    r = rows[name]
    profiles.update(r["profiles"].keys())
    diff = next(x for x in classes["rows"] if x["function"] == name)
    print(f"{r['score']:7} n={r['instructions']:4} jobs={r['jobs']:3} stalled={int(r['stalled'])} reg_insns={diff['register_instructions']:3} "
          f"lane={r['lane']:11} {name[:34]:34} faults={r['faults']} ran_regalloc={'regalloc_search' in r['profiles']}")
print(dict(profiles))
