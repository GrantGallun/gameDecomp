"""Read-only extraction of retained campaign evidence for register diagnosis."""
import json
import sqlite3
from pathlib import Path

name = "releaseSoundEffectHandleNode"
attempt = 108368
db = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
db.row_factory = sqlite3.Row
row = db.execute("select * from attempts where id=?", (attempt,)).fetchone()
print("columns:", list(row.keys()))
for key in ("source_code", "diff_summary"):
    print(f"\n[{key}]\n{row[key]}")
for key in row.keys():
    if key not in ("source_code", "diff_summary"):
        value = row[key]
        if value is not None and len(str(value)) < 1000:
            print(key, value)
ws = Path("/home/grant/decomp/sbk1/nonmatchings") / name
for suffix in ("target_object_dump_normalized.s",):
    path = ws / suffix
    print(f"\n[{path}]\n{path.read_text()}")
