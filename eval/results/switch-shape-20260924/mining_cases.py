"""Exploration: mining pairs whose reference adds `case` (the case+ family): draft shape and residual features."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "draft-reference-mining-20260924"))
import mine
out = []
for path in sorted((mine.E / "rows").glob("*.json")):
    row = json.loads(path.read_text())
    ref, draft = row.get("ref") or {}, row.get("draft") or {}
    if not (ref.get("compiled") and draft.get("compiled") and row.get("target_dump")):
        continue
    if mine.mask(ref["dump"]) != mine.mask(row["target_dump"]):
        continue
    a, b = mine.shape(row["draft_def"]), mine.shape(row["ref_def"])
    if b["case"] > a["case"]:
        res = mine.residual(row["target_dump"], draft["dump"])
        out.append((row["function"], a["case"], b["case"], a["goto"], a["if"], draft["score"],
                    sorted(res[0]) if res else "EXACT"))
for o in out:
    print(o[:6], [f for f in o[6] if not f.startswith("R:field")] if o[6] != "EXACT" else o[6])
print(len(out))
if len(sys.argv) > 1:
    row = json.loads((mine.E / "rows" / f"{sys.argv[1]}.json").read_text())
    print(row["draft_def"]); print("=" * 60); print(row["ref_def"])
