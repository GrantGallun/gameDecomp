"""Recheck the exact final sources from the exposed assembly-only intake frame."""
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.dev_set_export import recover_source
from eval.intake_probe import classify_residual
from solver.frontend_diagnostics import analyse

OUT = Path(__file__).resolve().parent
REPO = Path.home() / "decomp/sbk1"
frame = json.loads((ROOT / "eval/results/intake-20260921/wide-intake-clean.json").read_text())
conn = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
rows = []
for entry in frame["rows"]:
    name = entry["function"]
    source, lineage = recover_source(conn, entry["sequence"]["final_sha256"])
    if source is None:
        raise RuntimeError((name, lineage))
    folder = OUT / "states" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "before.c").write_text(source)
    target = conn.execute("SELECT t.name FROM functions f JOIN tus t ON t.id=f.tu_id WHERE f.name=?", (name,)).fetchone()[0]
    frontend = analyse(source, repo=REPO, target=target)
    (folder / "frontend.json").write_text(json.dumps(frontend, indent=2) + "\n")
    classes = Counter(classify_residual(e["what"]) for e in frontend["errors"])
    rows.append({"function": name, "target": target, "sha256": hashlib.sha256(source.encode()).hexdigest(),
                 "lineage": lineage, "sequence": entry["sequence"], "classes": dict(classes),
                 "frontend_status": frontend["status"], "errors": frontend["error_count"],
                 "conflicts": [e for e in frontend["errors"] if classify_residual(e["what"]) == "redeclaration/conflict"]})
    if len(rows) % 25 == 0:
        print(f"Rechecked {len(rows)}/200", flush=True)
counts = Counter(k for row in rows for k in row["classes"])
(OUT / "census.json").write_text(json.dumps({"rows": rows, "classes": dict(counts)}, indent=2) + "\n")
print(json.dumps(dict(counts), indent=2))
for row in rows:
    if row["conflicts"] and len(row["classes"]) <= 2:
        print(row["function"], row["classes"], row["conflicts"][:2])
