"""Call `function_boundary.certify` directly and capture the exception it swallows.

`workspace.score` wraps the call in `verification["function_boundary"] = ...`, and where the helper
declines it returns a result whose `error` key names the reason. The stored blob for rmonPrintf showed
`function_exact: False` and no `error` in the printed fields, so the reason is either absent or one level
down. This prints the WHOLE receipt, then re-runs the helper under a raise, so the refusal is quoted
rather than guessed.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import function_boundary

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
NAME = "rmonPrintf"
ws = REPO / "nonmatchings" / NAME

conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
try:
    row = conn.execute("SELECT addr,size FROM functions WHERE name=?", (NAME,)).fetchone()
finally:
    conn.close()
print(f"KB metadata for {NAME}: addr={row[0]} size={row[1]}")

stored = ws / f"{NAME}.verification.json"
if stored.is_file():
    blob = json.loads(stored.read_text(encoding="utf-8"))
    print("\nstored function_boundary receipt (full):")
    print(json.dumps(blob.get("function_boundary"), indent=2)[:1500])
else:
    print(f"\n{stored.name} not present")

arguments = dict(target=ws / "target.o", candidate=ws / f"{NAME}.o", assembly=ws / "target.s",
                 rom=REPO / "snowboardkids.z64", config=REPO / "snowboardkids.yaml",
                 symbols=REPO / "symbol_addrs.txt", function=NAME, address=row[0], size=row[1])
print("\nre-running function_boundary.certify with the KB's size:")
result = function_boundary.certify(**arguments)
print(json.dumps(result, indent=2)[:1200])

print("\nsame call with the size the TARGET OBJECT's own symbol reports (28):")
try:
    result = function_boundary.certify(**{**arguments, "size": 28})
    print(json.dumps({k: v for k, v in result.items() if k != "inputs"}, indent=2)[:1200])
except Exception:                                              # noqa: BLE001
    print("raised:")
    traceback.print_exc()
