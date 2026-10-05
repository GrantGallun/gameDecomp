"""Preserve costs for the shared-workspace pilot that violated its freeze."""
import hashlib
import json
from pathlib import Path
import sqlite3

OUT = Path(__file__).resolve().parent / "pilot-v1"
DB = Path.home() / "decomp/experiments/search-evolution-20260922/pilot-v1/attempts.sqlite"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = conn.execute("SELECT id,source_code,source_sha256 FROM attempts").fetchall()
assert all(hashlib.sha256(source.encode()).hexdigest() == digest for _, source, digest in rows)
errors = []
for path in OUT.glob("*--*.world.json"):
    if not path.name.startswith("r1-research--"):
        continue
    payload = json.loads(path.read_text())
    errors += [{"world": path.name, "node": n["id"], "error": n["verdict"].get("error")}
               for n in payload["world"]["nodes"] if n["verdict"].get("error")]
result = {"status": "aborted", "reason": "shared solver/call_arity_repair.py changed after freeze",
          "actual_compiler_receipts": len(rows), "callback_failures_without_compilation": len(errors),
          "source_hashes_valid": True, "advancement_allowed": False, "training_eligible": False,
          "errors": errors, "database": str(DB)}
(OUT / "aborted.json").write_text(json.dumps(result, indent=2))
print(json.dumps({k: v for k, v in result.items() if k != "errors"}, indent=2))
