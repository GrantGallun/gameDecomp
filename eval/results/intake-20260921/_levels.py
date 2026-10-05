"""The three-level table, BEFORE and AFTER, computed from the recorded rows.

`frontend` on the BEFORE side comes from `diagnostic_trace[-1]["clang"]`, which is where the audit read
it: both of those receipts were produced with `--trace-frontend`. The AFTER side carries the level as a
first-class field on every row, so it is read directly. Nothing here re-runs anything.
"""
import json
from pathlib import Path

BASE = Path("eval/results/intake-20260921")


def levels(payload: dict) -> dict:
    rows = payload["rows"]
    ido = sum(1 for r in rows if r["sequence"]["compiled"])
    exact = sum(1 for r in rows if r["sequence"]["exact"])
    frontend = 0
    for row in rows:
        trace = row.get("diagnostic_trace")
        if trace:
            frontend += int((trace[-1].get("clang") or "") == "passed")
        else:
            frontend += int(bool(row["sequence"].get("frontend_passed")))
    both = 0
    for row in rows:
        trace = row.get("diagnostic_trace")
        passed = ((trace[-1].get("clang") or "") == "passed" if trace
                  else bool(row["sequence"].get("frontend_passed")))
        both += int(bool(row["sequence"]["compiled"]) and passed)
    return {"rows": len(rows), "ido_compiled": ido, "ido_and_frontend": both,
            "byte_exact": exact, "frontend_passed": frontend}


for name in ("wide-frame.json", "wide-intake-traced.json", "wide-intake-undeclared.json",
             "wide-intake-voidfix.json", "wide-intake-orfix.json", "wide-intake-acceptance.json"):
    payload = json.loads((BASE / name).read_text(encoding="utf-8"))
    print(f"{name:30} {json.dumps(levels(payload))}")
