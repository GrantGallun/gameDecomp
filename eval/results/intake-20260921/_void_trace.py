"""Why is `derived` still empty after the `void` demotion? Trace the pieces on one state."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import compile_obligations, typedecl, workspace                # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
NAME = "osEPiRawReadIo"

conn = sqlite3.connect(str(KB))
try:
    context, why = build_context(REPO, NAME, conn=conn)
    if context is None:
        raise SystemExit(why)
    initial = dict(context.initial_verdict or {})
    current, best, stderr = context.candidate, initial, initial.get("stderr") or ""
    for label in SEQUENCE:
        if not gated(label, stderr):
            continue
        ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
              "initial_verdict": {**initial, "stderr": stderr}}
        try:
            result = RUNNERS[label](ns, {})
        except Exception:                                                  # noqa: BLE001
            continue
        if not result.get("changed"):
            continue
        verdict = context.compile_fn(result["source"])
        if rank(verdict) >= rank(best):
            current, best = result["source"], verdict
            stderr = verdict.get("stderr") or ""

    print(f"{NAME}: final candidate {'compiles' if best.get('compiled') else 'does not compile'}")
    signature = next((ln for ln in current.splitlines() if NAME + "(" in ln), "")
    print(f"  signature: {signature.strip()[:110]}")
    print(f"  `void *` in it: {'void' in signature}")

    asm = workspace.target_asm(workspace.bootstrap(REPO, NAME), NAME)
    analysis, _ = compile_obligations.analyse(asm)
    layout = {}
    for access in analysis.accesses.values():
        a = access.address
        if not a or a.kind != "address" or not a.name.startswith("param") or a.offset < 0:
            continue
        if access.opcode in {"lwl", "lwr", "swl", "swr", "lwc1", "swc1", "ldc1", "sdc1"}:
            continue
        signed = 0 if access.opcode in {"lbu", "lhu"} else 1
        from solver import structgen
        field = (a.offset, access.width, structgen.field_type(access.width, signed))
        layout.setdefault(a.name, {})
        if a.offset not in layout[a.name] or access.is_load:
            layout[a.name][a.offset] = field
    layout = {base: sorted(slots.values()) for base, slots in layout.items()}
    print(f"\n  layout opaque_variant builds: {layout}")

    pointers = typedecl.pointer_parameters(current, NAME)
    print(f"  typedecl.pointer_parameters : {pointers}")

    plans = typedecl.plan(current, NAME, layout, set())
    print(f"  typedecl.plan               : {len(plans)} plan(s)")
    for plan in plans:
        print(f"     type={plan['type']!r} params={plan['params']} named={plan['named']}")
        print(f"     text={plan['text'].replace(chr(10), ' ')[:130]}")

    print(f"\n  PRIMITIVE_TYPES has void: {'void' in typedecl.PRIMITIVE_TYPES}")
    print(f"  declared_in(source, 'void') -> {typedecl.declared_in(current, 'void')}")
    if not plans:
        print("  -> `plan` returned nothing; the demotion did not take effect on this path, or another "
              "filter rejects it. Check `declared_in` and the params list above.")
finally:
    conn.close()
