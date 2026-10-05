"""Item 2's receipt: what the clang frontend step adds to the intake route, measured on the 200-state frame.

TWO CLAIMS ARE SEPARATE AND BOTH ARE CHECKED.

  1. WIRING IS SAFE. The frontend is an OBSERVATION -- it returns `changed: False` and never touches the
     candidate -- so adding it must not move the conversion count. If the rate moves, the step is not the
     pure observation it claims to be, and that is a defect rather than a result.

  2. WIRING IS WORTH SOMETHING. cfe stops at the first error and truncates its list there. Every repair
     module in `solver/` that gates on clang's wording is therefore starved on a route that only reads cfe.
     What the step buys is the count of independent blockers per state and the set of mechanisms those
     blockers make reachable.

Waits for the run to finish rather than racing it. Read-only.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
BEFORE = BASE / "wide-intake.json"
AFTER = BASE / "wide-intake-frontend.json"
DEADLINE = time.time() + 900

while time.time() < DEADLINE:
    try:
        payload = json.loads(AFTER.read_text(encoding="utf-8"))
        break
    except (OSError, ValueError):
        time.sleep(15)
else:
    raise SystemExit(f"{AFTER.name} did not appear within the wait budget")

before = json.loads(BEFORE.read_text(encoding="utf-8"))
print(f"{'arm':38} {'n':>4} {'conv':>5} {'exact':>6} {'rate':>7}")
for label, p in (("intake, before the frontend step", before), ("intake, with the frontend step", payload)):
    print(f"{label:38} {p['front_door_failures']:4} {p['sequence_converted']:5} "
          f"{p['sequence_exact']:6} {p['conversion_rate']:7.3f}")

sa = {r["function"]: r.get("draft_sha256") for r in before["rows"]}
sb = {r["function"]: r.get("draft_sha256") for r in payload["rows"]}
print(f"\nframe identity: same set={set(sa) == set(sb)}  differing drafts="
      f"{sum(1 for f in sa if sa[f] != sb.get(f))}")

moved = (before["sequence_converted"], before["sequence_exact"]) != \
        (payload["sequence_converted"], payload["sequence_exact"])
print(f"\nclaim 1, wiring is safe: the conversion count "
      f"{'MOVED -- the step is not a pure observation' if moved else 'did not move'}")

frontend = payload["per_action"].get("eval.intake_runners.frontend_diagnostics", {})
print(f"\nclaim 2, what the step adds:")
print(f"  fired (a candidate it could read)   : {frontend.get('fired', 0)}")
print(f"  declined                            : {frontend.get('declined', 0)}")
print(f"  gated                              : {frontend.get('gated', 0)}")
print(f"  crashed                            : {frontend.get('crashed', 0)}")

# The gate coverage over the blocked states, taken from the per-state rows.
blocked = [r for r in payload["rows"] if not r["sequence"]["compiled"]]
gates: Counter = Counter()
states_with_a_gate = 0
for row in blocked:
    entry = row["actions"].get("eval.intake_runners.frontend_diagnostics") or {}
    detail = entry.get("detail") or {}
    names = detail.get("gates") or {}
    if names:
        states_with_a_gate += 1
        for name in names:
            gates[name] += 1
print(f"\n  blocked states                     : {len(blocked)}")
print(f"  of those, reachable by >=1 repair   : {states_with_a_gate} "
      f"({100 * states_with_a_gate / max(1, len(blocked)):.1f}%)")
print(f"  by mechanism:")
for name, count in gates.most_common():
    print(f"    {count:4}  {name}")

errors = Counter()
for row in payload["rows"]:
    entry = row["actions"].get("eval.intake_runners.frontend_diagnostics") or {}
    reason = entry.get("reason") or ""
    if reason:
        errors[reason[:60]] += 1
print(f"\n  why it declined, when it did:")
for reason, count in errors.most_common(5):
    print(f"    {count:4}  {reason}")

(BASE / "frontend-step-receipt.json").write_text(json.dumps(
    {"before": {"converted": before["sequence_converted"], "exact": before["sequence_exact"]},
     "after": {"converted": payload["sequence_converted"], "exact": payload["sequence_exact"]},
     "conversion_moved": moved,
     "frontend_fired": frontend.get("fired", 0),
     "blocked_states": len(blocked),
     "blocked_with_a_reachable_repair": states_with_a_gate,
     "gates": dict(gates),
     "decline_reasons": dict(errors),
     "note": ("the frontend is an observation; converting more states is not its job. What it changes is "
              "which repair mechanisms a state makes reachable")},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'frontend-step-receipt.json'}")
