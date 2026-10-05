"""Why does `opaque_variant` decline on the states whose error is written into its own docstring?

`solver/compile_obligations.opaque_variant` says it is "the owner of `Selector requires struct/union pointer
as left hand side`, the dominant error after the placeholder itself is resolved". On the frozen 200-state
frame it fires on 0 of 200, and all 12 states where `member-on-typed-pointer` is the WHOLE remaining
distance report exactly that error.

Its inputs, in order:
    analyse(assembly)        parameter accesses at NON-NEGATIVE offsets
    typedecl.pointer_parameters(source, function)   <- the second gate
    ...

So the decline is somewhere on that path. This calls the pieces directly on one state and prints what each
returns, which is the difference between "the mechanism cannot work here" and "one condition is too strict".
"""
from __future__ import annotations

import json
import re
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


def final_candidate(context, conn):
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
    return current, best


conn = sqlite3.connect(str(KB))
try:
    context, why = build_context(REPO, NAME, conn=conn)
    if context is None:
        raise SystemExit(why)
    current, best = final_candidate(context, conn)
    asm = workspace.target_asm(workspace.bootstrap(REPO, NAME), NAME)

    print(f"{NAME}: final candidate {len(current)} chars, compiled={bool(best.get('compiled'))}")
    print("\nthe function's parameter list and the accesses on it:")
    header = next((ln for ln in current.splitlines() if NAME + "(" in ln), "")
    print(f"   {header.strip()[:110]}")
    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)", current):
        print(f"   access: {match.group(1)}->{match.group(2)}")

    analysis, rows = compile_obligations.analyse(asm)
    params = [a for a in analysis.accesses.values()
              if a.address and a.address.kind == "address" and a.address.name.startswith("param")]
    print(f"\nanalyse(assembly): {len(analysis.accesses)} accesses, {len(params)} on params")
    for access in params[:8]:
        print(f"   {access.address.name} offset {access.address.offset} width {access.width} "
              f"{'load' if access.is_load else 'store'} {access.opcode}")

    pointers = typedecl.pointer_parameters(current, NAME)
    print(f"\ntypedecl.pointer_parameters(source, function) -> {pointers}")
    definitions = typedecl.definition_params(current, NAME)
    print(f"typedecl.definition_params(...)              -> {definitions}")

    changed, info = compile_obligations.opaque_variant(REPO, NAME, current, asm)
    print(f"\nopaque_variant: changed={changed != current}")
    print(f"   report: {json.dumps({k: v for k, v in info.items() if k != 'layout'}, default=str)[:300]}")
    print(f"   layout: {json.dumps(info.get('layout'), default=str)[:400]}")
    print(f"   observed_parameter_slots: "
          f"{json.dumps(info.get('observed_parameter_slots'), default=str)[:300]}")
finally:
    conn.close()
