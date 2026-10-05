"""Does the frame now state its own sensitivity? Small check of the instrument surface."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

import eval.intake_control as control                                    # noqa: E402
import eval.intake_probe as probe                                        # noqa: E402
from eval.intake_probe import resolution                                 # noqa: E402

for size in (12, 40, 200, 400):
    print(json.dumps(resolution(size)))

# The two imports the control now takes from the probe must both exist and be the same objects, or the
# two arms would stop being comparable while still running.
assert control.resolution is probe.resolution
assert control.rank is probe.rank
assert control.TIER_ORDER == probe.TIER_ORDER
print("control shares the probe's resolution, rank and tier order")

# `failure_class` on a row must be present for the per-class breakdown the frame reports.
frame = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921/class-frame.json")
rows = json.loads(frame.read_text(encoding="utf-8"))["rows"]
print(f"old frame rows carry: {sorted(rows[0])}")
assert "failure_class" in rows[0] and "draft_sha256" in rows[0]
print("row provenance present: failure_class, size, tier, draft_sha256")

conn = sqlite3.connect(f"file:{Path.home()}/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
try:
    print("unsolved pool by tier:")
    pool = probe.unsolved_by_tier(Path.home() / "decomp/kb-sbk1.sqlite", exclude=set())
    for tier, entries in pool.items():
        print(f"  {tier:7} {len(entries):4}")
finally:
    conn.close()
