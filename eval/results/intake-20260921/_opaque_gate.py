"""Trace `opaque_variant`'s first gate on a state where it has the data and still produces nothing.

Known from the previous probe:
    analyse(assembly)  -> accesses include `param0 offset 12 width 4 load lw`, exactly the input the
                          docstring describes
    the draft           -> `s32 osEPiRawReadIo(void *arg0, s32 arg1, s32 *arg2)` with `arg0->unkC`
    opaque_variant      -> plans [], layout None

So the gate rejects an access that satisfies every stated condition. This prints, per access, which clause
of the filter rejects it, so the answer is a clause and not a guess.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                              # noqa: E402
from solver import compile_obligations, workspace                          # noqa: E402

REPO = Path.home() / "decomp/sbk1"
NAME = "osEPiRawReadIo"
SKIP = {"lwl", "lwr", "swl", "swr", "lwc1", "swc1", "ldc1", "sdc1"}

conn = sqlite3.connect(str(Path.home() / "decomp/kb-sbk1.sqlite"))
try:
    context, _ = build_context(REPO, NAME, conn=conn)
    candidate = context.candidate or ""
    asm = workspace.target_asm(workspace.bootstrap(REPO, NAME), NAME)
    analysis, rows = compile_obligations.analyse(asm)

    print(f"accesses in the assembly: {len(analysis.accesses)}")
    print(f"{'name':22} {'kind':10} {'offset':>7} {'width':>6} {'op':6} {'load':5} rejected-by")
    for access in analysis.accesses.values():
        a = access.address
        name = a.name if a else "(none)"
        kind = a.kind if a else "(none)"
        offset = a.offset if a else None
        reasons = []
        if not a:
            reasons.append("no address")
        else:
            if a.kind != "address":
                reasons.append(f"kind={a.kind!r} != 'address'")
            if not str(a.name).startswith("param"):
                reasons.append(f"name {a.name!r} does not start with 'param'")
            if a.offset is not None and a.offset < 0:
                reasons.append(f"offset {a.offset} < 0")
            if access.opcode in SKIP:
                reasons.append(f"opcode {access.opcode} is in SKIP")
        print(f"{str(name):22} {str(kind):10} {str(offset):>7} {access.width:6} {access.opcode:6} "
              f"{str(access.is_load):5} {'; '.join(reasons) or 'ACCEPTED'}")

    accepted = [a for a in analysis.accesses.values()
                if a.address and a.address.kind == "address"
                and str(a.address.name).startswith("param") and not (a.address.offset or 0) < 0
                and a.opcode not in SKIP]
    print(f"\naccepted by opaque_variant's filter: {len(accepted)}")
    for access in accepted:
        print(f"   {access.address.name} offset {access.address.offset} width {access.width} "
              f"{'load' if access.is_load else 'store'}")

    changed, info = compile_obligations.opaque_variant(REPO, NAME, candidate, asm)
    print(f"\nopaque_variant on the DRAFT (not the final candidate): changed={changed != candidate}")
    print(f"   plans: {json.dumps(info.get('plans'), default=str)[:300]}")
    print(f"   declined: {json.dumps(info.get('declined'), default=str)[:300]}")
    print(f"   layout: {json.dumps(info.get('layout'), default=str)[:300]}")
finally:
    conn.close()
