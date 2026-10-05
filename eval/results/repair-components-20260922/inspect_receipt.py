import json
import sqlite3
from pathlib import Path

db = sqlite3.connect(Path.home() / "decomp/experiments/repair-components-20260922/attempts.sqlite")
db.row_factory = sqlite3.Row
row = dict(db.execute("SELECT * FROM attempts ORDER BY id DESC LIMIT 1").fetchone())
meta = json.loads(row["sampling"])
print(json.dumps({"columns": list(row), "metadata_keys": list(meta),
    "values": {k: row[k] for k in ("id", "compiled", "exact", "score", "parent_attempt_id")},
    "recipe": meta.get("compiler_recipe")}, indent=2))
