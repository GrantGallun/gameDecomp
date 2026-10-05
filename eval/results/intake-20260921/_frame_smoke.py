"""Smoke test for the size-bucket frame: counts per tier and the apportioned frame itself.

Read-only. Exists so the frame composition is inspected BEFORE a measurement runs on it -- the previous
frame was built by a query nobody checked, and its bias was only noticed a session later.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import bucket_frame, unsolved_by_tier   # noqa: E402

KB = Path.home() / "decomp/kb-sbk1.sqlite"
CLASSES = ("syntax-error", "undeclared-symbol", "selector", "other", "compiles")

pool = unsolved_by_tier(KB, exclude=set())
print("unsolved pool by tier:", {k: len(v) for k, v in pool.items()})
print("total unsolved:", sum(len(v) for v in pool.values()))
for tier, entries in pool.items():
    counts = {c: sum(1 for e in entries if e["failure_class"] == c) for c in CLASSES}
    print(f"  {tier:7} n={len(entries):4}  {counts}")

entries, alloc = bucket_frame(KB, 40, exclude=set(), failure_class="syntax-error", per_tier=8)
print("\nallocation:")
print(json.dumps(alloc, indent=2))
print("\nframe (order as measured):")
for entry in entries:
    print(f"  {entry['tier']:7} size={entry['size']:6} attempts={entry['attempts']:3} "
          f"{entry['function']}")
