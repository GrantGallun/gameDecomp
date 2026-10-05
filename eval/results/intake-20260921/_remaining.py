"""Where the remaining work is, from the post-fix arms rather than from impressions.

Four questions, each answered from a file that already exists:

  1. HOW FAR DOES THE REACH GO? A state can be non-compiling, compiling-not-exact, or certified. The
     count in each bucket is the size of the remaining problem, and the score gradient inside the middle
     bucket is its shape.
  2. WHAT DO THE STILL-BLOCKED DRAFTS WANT? First compiler error, classified, over the states the fixed
     intake arm could not open. That names the next owner.
  3. WHAT DOES THE CONTROL ARM ACTUALLY EARN? Which action produced each improvement, so "13 of 40" is
     attributable rather than aggregate.
  4. HOW BIG IS THE SAMPLE, HONESTLY? The frame size, the number of positives, and the observed frame
     movement, because every rate here rests on 40 states of one failure class.

Read-only; no model, nothing promoted.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
INTAKE = BASE / "post-fix-intake.json"
CONTROL = BASE / "post-fix-control.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


intake, control = load(INTAKE), load(CONTROL)
print("=" * 78)
print("1. THE REACH, on the same 40 states")
print("=" * 78)
converted = {r["function"]: r for r in intake["rows"] if r["sequence"]["compiled"]}
exact = {r["function"]: r for r in intake["rows"] if r["sequence"]["exact"]}
blocked = [r for r in intake["rows"] if not r["sequence"]["compiled"]]
print(f"  certified                      : {len(exact):2}   {sorted(exact)}")
print(f"  compiling, not exact           : {len(converted) - len(exact):2}")
for name, row in sorted(converted.items(), key=lambda kv: -(kv[1]["sequence"]["score"] or 0)):
    mark = "exact" if name in exact else "     "
    print(f"      {row['sequence']['score']:7.3f} {mark}  {row['tier']:7} {name}")
print(f"  still not compiling            : {len(blocked):2}")

print()
print("=" * 78)
print("2. WHAT THE STILL-BLOCKED DRAFTS WANT (first compiler error)")
print("=" * 78)
LINE = re.compile(r"line\s+\d+:\s*(?P<what>.+)$")
classes = Counter()
for row in blocked:
    stderr = ""
    for label, entry in row["actions"].items():
        if entry.get("stderr"):
            stderr = entry["stderr"]
    if not stderr:
        stderr = row.get("baseline_stderr") or ""
    match = LINE.search(stderr)
    classes[(match.group("what") if match else stderr or "<none>")[:58]] += 1
for text, count in classes.most_common(10):
    print(f"  {count:3}  {text}")

print()
print("=" * 78)
print("3. WHAT THE CONTROL ARM EARNS, by action")
print("=" * 78)
earned = Counter()
for row in control["rows"]:
    if row.get("improved") and row.get("best_label"):
        earned[row["best_label"].split(":")[0]] += 1
for action, count in earned.most_common():
    print(f"  {action:22} credited with {count} improvements")
print(f"  certified matches: "
      f"{[r['function'] for r in control['rows'] if r.get('exact')]}")

print()
print("=" * 78)
print("4. HOW BIG THE SAMPLE IS")
print("=" * 78)
movement = load(BASE / "class-frame-r4.json")
a = {r["function"] for r in intake["rows"]}
b = {r["function"] for r in movement["rows"]}
print(f"  frame size                     : {len(a)}")
print(f"  positives in the intake arm    : {len(converted)} "
      f"({100 * len(converted) / len(a):.1f}%), certified {len(exact)}")
print(f"  positives in the control arm   : {control['any_action_improved_any_state']}, "
      f"certified {control['certified_matches']}")
print(f"  frame membership drift observed : {len(a ^ b)} of 40 between two builds of one command")
print(f"  states the frame is drawn from  : the 'cfe: Syntax Error' class; the unsolved pool is "
      f"735 functions with attempts, the KB has 2,113")
