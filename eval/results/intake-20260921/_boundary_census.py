"""How many functions certify under the ROM-backed function-extent certificate while `attempt.exact` is false?

THE QUESTION `rmonPrintf` RAISED. `solver/workspace.score` sets `exact = verification["exact"]`, which is
the OBJECT-level certificate. When that fails it also runs `solver/function_boundary.certify`, which
compares the function's own bytes against the ROM and writes `verification["function_boundary"]`. Nothing
reads that field back: `eval/completion_campaign.py:210` does, and sets a node status, but `Attempt.exact`
does not, so every harness keyed on `attempt.exact` -- this session's probes included -- scores a
certified-correct function as a failure.

That is either a small curiosity about one printf stub or a systematic undercount, and the difference is a
count. This counts it over every stored verification receipt on disk, and reports what the class looks like.

READ-ONLY. It writes nothing to the KB, promotes nothing, and changes no marker.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path.home() / "decomp/sbk1"
NONMATCHINGS = REPO / "nonmatchings"


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


rows = []
for path in sorted(NONMATCHINGS.glob("*/*.verification.json")):
    blob = load(path)
    if not isinstance(blob, dict):
        continue
    boundary = blob.get("function_boundary") or {}
    if not isinstance(boundary, dict):
        boundary = {}
    rows.append({
        "function": path.parent.name,
        "object_exact": bool(blob.get("exact")),
        "certificate_status": blob.get("status"),
        "boundary_present": bool(boundary),
        "boundary_exact": bool(boundary.get("function_exact")),
        "boundary_status": boundary.get("status"),
        "boundary_error": boundary.get("error"),
        "whole_rom_verified": bool(boundary.get("whole_rom_verified")),
        "requires_isolated_integration": bool(boundary.get("requires_isolated_integration")),
    })

print(f"stored verification receipts: {len(rows)}")
by_status = Counter(r["certificate_status"] for r in rows)
print(f"by certificate status: {dict(by_status)}")

boundary_ran = [r for r in rows if r["boundary_present"]]
boundary_passed = [r for r in boundary_ran if r["boundary_exact"]]
uncounted = [r for r in boundary_passed if not r["object_exact"]]

print(f"\nfunction_boundary ran on      : {len(boundary_ran)}")
print(f"  and found the bytes exact   : {len(boundary_passed)}")
print(f"  with boundary_exact TRUE and object_exact FALSE : {len(uncounted)}")
print(f"    -> these are functions whose own bytes match the ROM, counted as failures by every harness "
      f"keyed on attempt.exact")

if boundary_ran:
    declined = [r for r in boundary_ran if not r["boundary_exact"]]
    print(f"\nboundary ran and DECLINED: {len(declined)}")
    reasons = Counter(r["boundary_error"] for r in declined)
    for reason, count in reasons.most_common(8):
        print(f"  {count:4}  {str(reason)[:100]}")

print(f"\nthe uncounted certified functions:")
for row in uncounted[:40]:
    print(f"  {row['function']:44} object={row['certificate_status']:24} "
          f"boundary={row['boundary_status']}")
if len(uncounted) > 40:
    print(f"  ... {len(uncounted) - 40} more")

# Classify WHY the object certificate failed for them, since that is what decides whether they are
# genuinely one TU-layout artefact away or something more interesting.
print(f"\nwhy the object certificate failed for them:")
kinds = Counter()
for row in uncounted:
    path = NONMATCHINGS / row["function"] / f"{row['function']}.verification.json"
    blob = load(path) or {}
    image_t = (blob.get("target_image") or {}).get("sections") or {}
    image_c = (blob.get("candidate_image") or {}).get("sections") or {}
    text_t = (image_t.get(".text") or {}).get("size")
    text_c = (image_c.get(".text") or {}).get("size")
    same_bytes = (image_t.get(".text") or {}).get("sha256") == (image_c.get(".text") or {}).get("sha256")
    kind = ("text bytes identical, section sizes differ" if same_bytes and text_t != text_c else
            "text bytes identical, sizes equal" if same_bytes else
            "text bytes DIFFER")
    kinds[kind] += 1
    row["text_target"], row["text_candidate"], row["text_same"] = text_t, text_c, same_bytes
for kind, count in kinds.most_common():
    print(f"  {count:4}  {kind}")

out = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921/boundary-census.json")
out.write_text(json.dumps(
    {"receipts": len(rows), "boundary_ran": len(boundary_ran), "boundary_passed": len(boundary_passed),
     "uncounted": len(uncounted), "by_status": dict(by_status), "kinds": dict(kinds),
     "rows": rows, "uncounted_functions": [r["function"] for r in uncounted],
     "note": "read-only census of stored verification receipts; nothing promoted"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {out}")
