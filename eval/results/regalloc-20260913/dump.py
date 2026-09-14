"""Side-by-side target/candidate dump for one cohort function: python dump.py NAME [SOURCE.c]"""
import json, sys
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import regalloc_probe
from solver import workspace
rows = {r["name"]: r for r in json.loads(Path("eval/results/regalloc-20260913/cohort.json").read_text())["functions"]}
row = rows[sys.argv[1]]
source = Path(sys.argv[2]).read_text() if len(sys.argv) > 2 else Path(row["source"]).read_text()
bench = regalloc_probe.Bench(row)
try:
    name = f"{sys.argv[1]}_dump"
    workspace.score(bench.ws, bench.isolated, name, source, conn=bench.conn, func=sys.argv[1])
    t = (bench.ws / "target_object_dump_normalized.s").read_text().splitlines()
    c = (bench.ws / f"{name}_object_dump_normalized.s").read_text().splitlines()
    for i in range(max(len(t), len(c))):
        a = t[i] if i < len(t) else ""; b = c[i] if i < len(c) else ""
        print(f"{i:3} {'  ' if a == b else '!!'} {a:34s} {b}")
finally:
    bench.close()
