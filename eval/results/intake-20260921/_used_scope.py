"""Two things at once: is `renderPickupIdle` really in the frame, and what does the `used` scan return?

The dead-state alarm came from `_type_compile_test.py` raising on `renderPickupIdle` while the frame
integrity check builds all 200 contexts without a failure. One of those two observations is wrong and this
settles which, because "the frame contains dead states" is a claim about the whole instrument.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                              # noqa: E402

REPO = Path.home() / "decomp/sbk1"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
frame = json.loads((BASE / "wide-frame.json").read_text(encoding="utf-8"))["rows"]
names = {row["function"] for row in frame}
print(f"renderPickupIdle in the frame: {'renderPickupIdle' in names}")
print(f"similarly-named members: {sorted(n for n in names if 'PickupIdle' in n or 'Pickup' in n)[:8]}")

conn = sqlite3.connect(str(Path.home() / "decomp/kb-sbk1.sqlite"))
try:
    for name in ("renderPickupIdle", "renderPickupShardParticle"):
        if name not in names:
            print(f"\n{name}: not a frame member, so the earlier error was a bad test input, not a dead state")
            continue
        context, why = build_context(REPO, name, conn=conn)
        print(f"\n{name}: context={'built' if context else why}")
        if context is None:
            continue
        source = context.candidate or ""
        signature = next((ln for ln in source.splitlines() if name + "(" in ln), "")
        print(f"  signature: {signature.strip()[:100]}")
        # The parameter names, so `used` can be restricted to accesses THROUGH THEM.
        params = re.search(r"\(\s*(?P<params>[^)]*)\)", signature)
        param_names = []
        if params:
            for part in params.group("params").split(","):
                words = re.findall(r"[A-Za-z_]\w*", part)
                if words:
                    param_names.append(words[-1])
        print(f"  parameters: {param_names}")

        all_accesses = sorted({m.group(1) for m in
                               re.finditer(r"\b\w+\s*->\s*(?P<member>[A-Za-z_]\w*)", source)})
        via_params = sorted({m.group(2) for m in
                             re.finditer(r"\b(?P<base>\w+)\s*->\s*(?P<member>[A-Za-z_]\w*)", source)
                             if m.group("base") in param_names})
        print(f"  ALL `->` members in the draft   : {all_accesses}")
        print(f"  members reached via a PARAMETER : {via_params}")
        print(f"  the difference (locals' members, which this pass must ignore): "
              f"{sorted(set(all_accesses) - set(via_params))}")
finally:
    conn.close()
