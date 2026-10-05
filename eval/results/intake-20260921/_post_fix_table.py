"""Before/after across every arm, with the reason each number moved stated in the table.

The control arm's numbers MUST move, and the movement is the evidence that the fixes took effect rather
than a reason to distrust them: `redraft` used to be credited with firing on 40 of 40 states by deleting
the `#include "common.h"` wrapper, so its fire count has to fall, and states it never actually helped can
no longer be attributed to it.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

INTAKE = [("class-replay.json", "intake arm  BEFORE (wrong order, placeholder step missing)"),
          ("post-fix-intake.json", "intake arm  AFTER  (campaign order, module fixed)"),
          ("class-replay-widths.json", "intake arm  AFTER + target-derived widths")]
CONTROL = [("class-control.json", "control arm BEFORE (redraft/uopt-trace reporting falsely)"),
           ("post-fix-control.json", "control arm AFTER  (all six defects fixed)")]


def load(name: str):
    path = BASE / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


print("INTAKE ARM — the campaign's four intake actions")
print(f"  {'arm':52} {'n':>3} {'conv':>5} {'exact':>6} {'rate':>7}")
for name, label in INTAKE:
    payload = load(name)
    if payload is None:
        print(f"  {label:52} MISSING")
        continue
    print(f"  {label:52} {payload['front_door_failures']:3} {payload['sequence_converted']:5} "
          f"{payload['sequence_exact']:6} {payload['conversion_rate']:7.3f}")

print("\nCONTROL ARM — the original seven wired actions, one at a time")
print(f"  {'arm':52} {'n':>3} {'impr':>5} {'comp':>5} {'exact':>6}")
for name, label in CONTROL:
    payload = load(name)
    if payload is None:
        print(f"  {label:52} MISSING")
        continue
    print(f"  {label:52} {payload['measured']:3} {payload['any_action_improved_any_state']:5} "
          f"{sum(1 for r in payload['rows'] if r.get('compiling_anywhere')):5} "
          f"{payload['certified_matches']:6}")

print("\nCONTROL per-action, before and after")
before, after = load("class-control.json"), load("post-fix-control.json")
if before and after:
    print(f"  {'action':22} {'fired before':>12} {'fired after':>11} {'n/a before':>10} "
          f"{'n/a after':>9}")
    for action in sorted(after["per_action"]):
        short = action.split(".")[-1]
        b = (before["per_action"].get(action) or {})
        a = after["per_action"][action]
        print(f"  {short:22} {b.get('fired', '-'):>12} {a.get('fired', '-'):>11} "
              f"{b.get('not_applicable', '-'):>10} {a.get('not_applicable', '-'):>9}")

    print("\nwhy each control number moved:")
    print("  redraft        fired 40 -> 0: it no longer 'changes' the candidate by deleting the")
    print("                 `#include \"common.h\"` wrapper that workspace.m2c_draft prepends")
    print("  uopt-trace     not-applicable 40 -> its real prerequisite path, which needs the traced")
    print("                 compiler build and both object dumps")
    print("  diffrepair     still not-applicable on a non-compiling candidate, now saying why and")
    print("                 naming the phase in which it is reachable")

    print("\nthe states the control now credits, with the action that earned it:")
    for row in after["rows"]:
        if not row.get("improved"):
            continue
        print(f"  {row['function']:40} {row['tier']:7} errors {row['baseline_errors']} -> "
              f"{row['errors_remaining_at_best']}  best={row['best_label']}")

    lost = [r["function"] for r in after["rows"]
            if not r.get("improved") and any(r["function"] == b["function"] and b.get("improved")
                                             for b in before["rows"])]
    print(f"\nstates that were 'improved' before and are not now: {lost}")
