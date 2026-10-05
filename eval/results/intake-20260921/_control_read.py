"""Read the Step 2 control honestly: per-action applicability, and the receipt for every state that moved.

Three separate questions, kept separate on purpose:
  1. did every original action get its declared inputs (or decline by name)?
  2. on how many states did an action move the certificate at all?
  3. on how many did it produce a certified match?
A "0" in (3) means nothing unless (1) is clean, which is the whole reason this prints (1) first.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
payload = json.loads((BASE / "class-control.json").read_text(encoding="utf-8"))

print(f"frame={payload['frame_size']} measured={payload['measured']} "
      f"errors={len(payload['errors'])} drift={len(payload['baseline_drift'])} "
      f"seconds={payload['seconds']}")
print(f"actions: {payload['actions']}")
print(f"intake actions excluded: {payload['intake_actions_excluded']}\n")

print("(1) per-action applicability over the frame:")
print(f"    {'action':22} {'fired':>6} {'declined':>9} {'n/a':>5} {'compiled':>9} {'exact':>6} "
      f"{'cands':>6}")
for name, stat in payload["per_action"].items():
    print(f"    {name:22} {stat['fired']:6} {stat['declined']:9} {stat['not_applicable']:5} "
          f"{stat['compiled']:9} {stat['exact']:6} {stat['candidates']:6}")

print("\n(1b) why an action declined, by reason:")
reasons: dict[str, int] = {}
for row in payload["rows"]:
    for name, item in row["actions"].items():
        if item.get("status") == "not-applicable":
            reasons[f"{name}: {item.get('reason')}"] = reasons.get(
                f"{name}: {item.get('reason')}", 0) + 1
for text, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:10]:
    print(f"    {count:4}  {text}")

print(f"\n(2) states where an action moved the certificate: "
      f"{payload['any_action_improved_any_state']} of {payload['measured']}")
for row in payload["rows"]:
    if not row.get("improved"):
        continue
    print(f"    {row['function']:38} {row['tier']:7} errors {row['baseline_errors']} -> "
          f"{row['errors_remaining_at_best']}  best={row['best_label']}")
    for name, item in row["actions"].items():
        for verdict in (item.get("verdicts") or []):
            if verdict.get("compiled") or verdict.get("errors_remaining") < row["baseline_errors"]:
                print(f"        {name:22} {verdict['name'][:28]:30} compiled={verdict['compiled']} "
                      f"exact={verdict['exact']} errors={verdict['errors_remaining']} "
                      f"score={verdict['score']}")
                if verdict.get("error_classes"):
                    print(f"            remaining: {verdict['error_classes']}")

print(f"\n(3) certified matches: {payload['certified_matches']}")

print("\n(3b) every state, one line, so the census is auditable:")
for row in payload["rows"]:
    fired = [k for k, v in row["actions"].items() if v.get("changed")]
    print(f"    {row['function']:38} {row['tier']:7} err={row['baseline_errors']:2} "
          f"improved={int(bool(row['improved']))} compiling={int(bool(row['compiling_anywhere']))} "
          f"fired={','.join(fired) if fired else '-'}")
