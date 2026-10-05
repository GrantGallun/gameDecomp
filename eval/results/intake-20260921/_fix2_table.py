"""Did the two shipped fixes move anything? Every arm on the same frozen frame, side by side.

`header_variant` now skips names the draft itself defines, so its fire count must FALL (it was adding
headers for the function's own name -- 4 states became unbuildable). `negative-offset` is new, so its row
did not exist. Neither is evidence of progress on its own: the question is whether the intake arm converts
more states, and whether the four redeclaration states now get past that error.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
ARMS = [("class-replay.json", "before any fix"),
        ("post-fix-intake.json", "after the six wiring fixes"),
        ("post-fix2-intake.json", "after header de-duplication + negative offsets")]


def load(name: str) -> dict | None:
    path = BASE / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


print(f"{'arm':46} {'conv':>5} {'exact':>6} {'rate':>7}")
for name, label in ARMS:
    payload = load(name)
    if payload is None:
        print(f"{label:46} MISSING")
        continue
    print(f"{label:46} {payload['sequence_converted']:5} {payload['sequence_exact']:6} "
          f"{payload['conversion_rate']:7.3f}")

print("\nframe identity between the last two arms:")
a, b = load("post-fix-intake.json"), load("post-fix2-intake.json")
if a and b:
    sa = {r["function"]: r.get("draft_sha256") for r in a["rows"]}
    sb = {r["function"]: r.get("draft_sha256") for r in b["rows"]}
    print(f"  same function set: {set(sa) == set(sb)}   differing drafts: "
          f"{sum(1 for f in sa if sa[f] != sb.get(f))}")

print("\nper-action, both post-fix arms:")
if a and b:
    actions = sorted(set(a["per_action"]) | set(b["per_action"]))
    print(f"  {'action':24} {'fired before':>12} {'fired after':>11} {'compiled after':>14}")
    for action in actions:
        short = action.split(".")[-1]
        x = (a["per_action"].get(action) or {})
        y = (b["per_action"].get(action) or {})
        print(f"  {short:24} {x.get('fired', '-'):>12} {y.get('fired', '-'):>11} "
              f"{y.get('compiled', '-'):>14}")

print("\nthe four states that were made unbuildable by their own header:")
for name in ("__allocParam", "__freeParam", "alMainBusPull", "updateRacePlayerGroundAlignment"):
    row = next((r for r in (b or {}).get("rows", []) if r["function"] == name), None)
    if row is None:
        continue
    stderr = ""
    for entry in row["actions"].values():
        if entry.get("stderr"):
            stderr = entry["stderr"]
    print(f"  {name:38} compiled={row['sequence']['compiled']}  "
          f"{'redeclaration' if 'redeclaration' in stderr else 'no redeclaration'}")

print("\nstates converted by the intake arm, after:")
if b:
    for row in b["rows"]:
        if row["sequence"]["compiled"]:
            print(f"  {row['sequence']['score']:7.3f} {'exact' if row['sequence']['exact'] else '     '} "
                  f"{row['tier']:7} {row['function']}")

if a and b:
    lost = [r["function"] for r in a["rows"] if r["sequence"]["compiled"]] 
    gained = [r["function"] for r in b["rows"] if r["sequence"]["compiled"]
              and r["function"] not in lost]
    gone = [f for f in lost if f not in [r["function"] for r in b["rows"] if r["sequence"]["compiled"]]]
    print(f"\nnewly converted: {gained}")
    print(f"no longer converted (regressions): {gone}")
