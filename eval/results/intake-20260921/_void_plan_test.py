"""Does removing `void` from the struct-planning primitive set make `opaque_variant` fire?

THE DEFECT. `solver/typedecl.plan` skips any pointer parameter whose type is in `PRIMITIVE_TYPES`, and
`void` is in that set -- correctly, for C. But m2c writes EVERY untyped parameter as `void *`:

    s32 osEPiRawReadIo(void *arg0, s32 arg1, s32 *arg2) { ... arg0->unkC ... }

and `opaque_variant`'s whole job is to give that parameter a padded struct built from the assembly's
accesses at known offsets. So the one type it exists to handle is the one type `plan` refuses to plan.
Measured: `opaque_variant` fires on 0 of 200 states, and on `osEPiRawReadIo` it ACCEPTS both parameter
accesses (`param0 offset 12 width 4 load`) and then returns `plans: []`.

That is the same shape as every other defect this session: the mechanism is present, its inputs are
correct, and one condition is scaled to the wrong question. `void` is not a struct, so it must never be
USED as a struct name -- but "cannot be a struct" is not the same as "has nothing to plan".

This measures the change WITHOUT applying it: it patches the module in-process, runs one state end to end,
and compiles the result through the oracle. Nothing is written to any module.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                              # noqa: E402
from solver import compile_obligations, typedecl, workspace                # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"

TARGETS = ("osEPiRawReadIo", "osEPiRawWriteIo", "initMenuAsciiFontTexture",
           "__MusIntProcessContinuousVolume", "__MusIntProcessContinuousPitchBend")


def run(name: str, conn) -> dict:
    context, why = build_context(REPO, name, conn=conn)
    if context is None:
        return {"function": name, "error": why}
    candidate = context.candidate or ""
    asm = workspace.target_asm(workspace.bootstrap(REPO, name), name)
    changed, info = compile_obligations.opaque_variant(REPO, name, candidate, asm)
    verdict = context.compile_fn(changed) if changed != candidate else (context.initial_verdict or {})
    plans = info.get("plans") or []
    return {"function": name,
            "fired": changed != candidate,
            "plans": [{"type": p["type"], "params": p["params"], "named": p["named"],
                       "text": p["text"][:120]} for p in plans],
            "declined": info.get("declined") or [],
            "compiled": bool(verdict.get("compiled")), "exact": bool(verdict.get("exact")),
            "score": verdict.get("score"),
            "first_error": next((ln.strip() for ln in (verdict.get("stderr") or "").splitlines()
                                 if ln.strip()), "")[:90]}


conn = sqlite3.connect(str(KB))
original = typedecl.PRIMITIVE_TYPES
try:
    print("=" * 78)
    print("BEFORE: as shipped")
    print("=" * 78)
    before = [run(name, conn) for name in TARGETS]
    for row in before:
        print(f"  {row['function']:34} fired={row.get('fired')} plans={len(row.get('plans') or [])} "
              f"compiled={row.get('compiled')}")

    # The change, in process only: `void` is not a struct, but "not a struct" is not "nothing to plan".
    typedecl.PRIMITIVE_TYPES = frozenset(original - {"void"})
    print("\n" + "=" * 78)
    print("AFTER: `void` removed from the planning primitive set (in process only)")
    print("=" * 78)
    after = [run(name, conn) for name in TARGETS]
    for row in after:
        print(f"  {row['function']:34} fired={row.get('fired')} plans={len(row.get('plans') or [])} "
              f"compiled={row.get('compiled')} score={row.get('score')}")
        for plan in (row.get("plans") or []):
            print(f"      plan: type={plan['type']!r} params={plan['params']} named={plan['named']}")
            print(f"            {plan['text'].replace(chr(10), ' ')[:110]}")
        for decline in (row.get("declined") or [])[:2]:
            print(f"      declined: {json.dumps(decline, default=str)[:150]}")
        if row.get("first_error"):
            print(f"      cfe: {row['first_error']}")
finally:
    typedecl.PRIMITIVE_TYPES = original
    conn.close()

moved = [(b["function"], b.get("compiled"), a.get("compiled"))
         for b, a in zip(before, after) if b.get("compiled") != a.get("compiled")]
print(f"\nstates whose verdict moved: {moved}")
print(f"fired before: {sum(1 for r in before if r.get('fired'))}  "
      f"fired after: {sum(1 for r in after if r.get('fired'))}")
