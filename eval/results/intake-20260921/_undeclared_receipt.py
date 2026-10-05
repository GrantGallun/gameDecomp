"""Item 2's before/after with `undeclared_identifiers` in the sequence, and where the wins came from.

Waits for the run, then compares every arm this session has produced on the same frozen 200 states, so the
headline is unambiguous. The chain trace is what says whether a conversion came from the new step or from
something downstream finally being reachable.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
ARMS = [("wide-intake.json", "1. the two fixes (placeholder + negative offset)"),
        ("wide-intake-traced.json", "2. + the frontend step, chain traced"),
        ("wide-intake-types.json", "3. + source_type_declarations"),
        ("wide-intake-undeclared.json", "4. + undeclared_identifiers")]
AFTER = BASE / "wide-intake-undeclared.json"
DEADLINE = time.time() + 900

while time.time() < DEADLINE:
    try:
        payload = json.loads(AFTER.read_text(encoding="utf-8"))
        break
    except (OSError, ValueError):
        time.sleep(15)
else:
    raise SystemExit(f"{AFTER.name} did not appear")

print(f"{'sequence':48} {'conv':>5} {'exact':>6} {'rate':>7}")
rows_loaded = {}
for name, label in ARMS:
    path = BASE / name
    if not path.is_file():
        print(f"{label:48} (missing: {name})")
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    rows_loaded[label] = data
    print(f"{label:48} {data['sequence_converted']:5} {data['sequence_exact']:6} "
          f"{data['conversion_rate']:7.3f}")

action = payload["per_action"].get("eval.intake_runners.undeclared_identifiers") or {}
print(f"\nthe new step: {json.dumps({k: action.get(k, 0) for k in
                                   ('fired', 'compiled', 'exact', 'declined', 'crashed')})}")

# The chains that shortened because of it, read from the trace.
print(f"\nper-step chain removal, newest run:")
steps: dict[str, list[tuple[int, int]]] = {}
for row in payload["rows"]:
    trace = row.get("diagnostic_trace") or []
    for earlier, later in zip(trace, trace[1:]):
        if later["after"] == "final":
            continue
        steps.setdefault(later["after"], []).append(
            (len(earlier.get("classes") or []), len(later.get("classes") or [])))
for name, pairs in sorted(steps.items(), key=lambda kv: -(sum(a - b for a, b in kv[1]) / len(kv[1]))):
    removed = sum(a - b for a, b in pairs) / len(pairs)
    print(f"   {name:26} states={len(pairs):3}  removed={removed:+.2f} classes")

# What is left, and whether the whole-distance set moved.
blocked = [r for r in payload["rows"] if not r["sequence"]["compiled"]]
whole: Counter = Counter()
for row in blocked:
    classes = (row.get("diagnostic_trace") or [{}])[-1].get("classes") or []
    if len(classes) == 1:
        whole[classes[0]] += 1
print(f"\nblocked: {len(blocked)}; whole-distance classes now: {dict(whole.most_common())}")

if "3. + source_type_declarations" in rows_loaded:
    before = {r["function"] for r in rows_loaded["3. + source_type_declarations"]["rows"]
              if r["sequence"]["compiled"]}
    after = {r["function"] for r in payload["rows"] if r["sequence"]["compiled"]}
    print(f"\nnewly converted by adding undeclared_identifiers: {sorted(after - before)}")
    print(f"lost: {sorted(before - after)}")
    for row in payload["rows"]:
        if row["function"] in after - before:
            print(f"   {row['function']:44} score={row['sequence']['score']}")

(BASE / "undeclared-receipt.json").write_text(json.dumps(
    {"arms": {label: {"converted": data["sequence_converted"], "exact": data["sequence_exact"],
                      "rate": data["conversion_rate"]}
              for label, data in rows_loaded.items()},
     "new_step": {k: action.get(k, 0) for k in ("fired", "compiled", "exact", "declined", "crashed")},
     "per_step_chain_removal": {name: {"states": len(pairs),
                                       "removed": sum(a - b for a, b in pairs) / len(pairs)}
                                for name, pairs in steps.items()},
     "whole_distance_now": dict(whole),
     "note": "same frozen 200 states for every arm; the chain trace says whether a conversion came from "
             "the new step or from something downstream becoming reachable"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'undeclared-receipt.json'}")
