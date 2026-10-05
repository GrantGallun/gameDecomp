"""Read the frontend step's gate coverage out of the new payload, now that `detail` survives.

The previous receipt reported zero gates because the probe was throwing the runners' `detail` away, so the
per-mechanism counts never reached the file. This prints them from the payload itself rather than from a
side channel, and checks the two claims item 2 owes:

  1. wiring is safe      -- the conversion count did not move
  2. wiring is worth it  -- how many blocked states make a repair mechanism reachable that cfe alone
                            cannot name
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
payload = json.loads((BASE / "wide-intake-frontend.json").read_text(encoding="utf-8"))
before = json.loads((BASE / "wide-intake.json").read_text(encoding="utf-8"))

print(f"schema_version {payload.get('schema_version')}  "
      f"({len((BASE / 'wide-intake-frontend.json').read_bytes())} bytes)")
entry = payload["rows"][0]["actions"].get("eval.intake_runners.frontend_diagnostics") or {}
print(f"action entry keys now: {sorted(entry)}")

print(f"\nclaim 1 -- wiring is safe:")
print(f"  before the step : {before['sequence_converted']} converted, {before['sequence_exact']} exact")
print(f"  with the step   : {payload['sequence_converted']} converted, {payload['sequence_exact']} exact")
print(f"  moved           : "
      f"{(before['sequence_converted'], before['sequence_exact']) != (payload['sequence_converted'], payload['sequence_exact'])}")

blocked = [r for r in payload["rows"] if not r["sequence"]["compiled"]]
converted = [r for r in payload["rows"] if r["sequence"]["compiled"]]
gates: Counter = Counter()
states_with_gate = 0
blocked_with_gate = 0
first_errors: Counter = Counter()
for row in payload["rows"]:
    action = row["actions"].get("eval.intake_runners.frontend_diagnostics") or {}
    detail = action.get("detail") or {}
    found = detail.get("gates") or {}
    if found:
        states_with_gate += 1
        if not row["sequence"]["compiled"]:
            blocked_with_gate += 1
        for name in found:
            gates[name] += 1
    errors = detail.get("errors") or []
    if errors:
        first_errors[errors[0]["what"][:56]] += 1

print(f"\nclaim 2 -- what the step buys:")
print(f"  states with >=1 reachable repair mechanism : {states_with_gate} of {len(payload['rows'])}")
print(f"  of the {len(blocked)} still-blocked states          : {blocked_with_gate} "
      f"({100 * blocked_with_gate / max(1, len(blocked)):.1f}%)")
print(f"  by mechanism:")
for name, count in gates.most_common():
    print(f"    {count:4}  {name}")

print(f"\n  the most common FIRST clang error, which is what a repair would be written against:")
for text, count in first_errors.most_common(8):
    print(f"    {count:4}  {text}")

# cfe's view for comparison: how many errors it reported per blocked state.
cfe_counts = []
for row in blocked:
    best = ""
    for action in row["actions"].values():
        if action.get("stderr"):
            best = action["stderr"]
    cfe_counts.append(sum(1 for line in best.splitlines() if line.startswith("cfe:")))
print(f"\n  cfe reported a mean of {sum(cfe_counts) / max(1, len(cfe_counts)):.1f} errors on blocked states")
clang_counts = []
for row in blocked:
    action = row["actions"].get("eval.intake_runners.frontend_diagnostics") or {}
    detail = action.get("detail") or {}
    errors = detail.get("errors") or []
    if errors:
        clang_counts.append(len(errors))
if clang_counts:
    print(f"  clang reported at least {sum(clang_counts) / len(clang_counts):.1f} "
          f"(capped at 6 recorded per state)")

(BASE / "frontend-step-receipt.json").write_text(json.dumps(
    {"wiring_safe": {"before": [before["sequence_converted"], before["sequence_exact"]],
                     "after": [payload["sequence_converted"], payload["sequence_exact"]],
                     "moved": (before["sequence_converted"], before["sequence_exact"]) !=
                              (payload["sequence_converted"], payload["sequence_exact"])},
     "states": len(payload["rows"]),
     "blocked": len(blocked),
     "states_with_a_reachable_repair": states_with_gate,
     "blocked_with_a_reachable_repair": blocked_with_gate,
     "gates": dict(gates),
     "first_clang_errors": dict(first_errors.most_common(8)),
     "note": ("the frontend is an observation: it returns changed=False and cannot convert a state. What "
              "it changes is which repair mechanisms a blocked state makes reachable, which cfe alone "
              "cannot say because it truncates its error list at the first one")},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'frontend-step-receipt.json'}")
