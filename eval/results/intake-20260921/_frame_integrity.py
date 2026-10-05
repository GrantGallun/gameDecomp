"""Has the frozen frame drifted into containing states that no longer resolve?

`renderPickupIdle` is one of the 200, and bootstrapping it now raises
`target resolution for renderPickupIdle: 0 candidates`. The frame was built earlier in this session and its
composition was observed to move by 4 of 40 on the narrow frame because the failure class is re-derived from
a live KB; a function can also stop resolving for reasons outside the KB.

A frame that contains unresolvable states is a frame whose denominator is not what it claims, which is the
defect class this whole harness exists to catch. So this counts it: how many of the 200 fail to build a
context, and why.

Read-only.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                              # noqa: E402

REPO = Path.home() / "decomp/sbk1"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
frame = json.loads((BASE / "wide-frame.json").read_text(encoding="utf-8"))["rows"]
print(f"frame size: {len(frame)}")

conn = sqlite3.connect(str(Path.home() / "decomp/kb-sbk1.sqlite"))
failures: list[tuple[str, str]] = []
try:
    for index, entry in enumerate(frame, 1):
        try:
            context, why = build_context(REPO, entry["function"], conn=conn)
        except Exception as exc:                                           # noqa: BLE001
            failures.append((entry["function"], f"{type(exc).__name__}: {str(exc).splitlines()[0][:80]}"))
            continue
        if context is None:
            failures.append((entry["function"], str(why)[:90]))
        if index % 50 == 0:
            print(f"  ... {index}/{len(frame)} ({len(failures)} failures so far)", flush=True)
finally:
    conn.close()

print(f"\nstates that no longer build a context: {len(failures)} of {len(frame)}")
kinds = Counter(reason.split(":")[0] for _name, reason in failures)
for kind, count in kinds.most_common():
    print(f"  {count:4}  {kind}")
print("\nthe first 15:")
for name, reason in failures[:15]:
    print(f"  {name:44} {reason}")

ok = [entry["function"] for entry in frame
      if entry["function"] not in {name for name, _ in failures}]
(BASE / "frame-integrity.json").write_text(json.dumps(
    {"frame_size": len(frame), "unresolvable": len(failures),
     "kinds": dict(kinds), "failures": [{"function": n, "reason": r} for n, r in failures],
     "resolvable": len(ok),
     "note": ("read-only: every frame member is bootstrapped and asked for a context, so a denominator "
              "that has quietly shrunk is visible")}, indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'frame-integrity.json'}")
