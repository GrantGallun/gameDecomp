"""m2c writes `*(ptr | n)`, which is invalid C because `ptr | int` has no operator.

THE ASSEMBLY, verbatim from `nonmatchings/osEPiRawReadIo/target.s`:

    lw   $t0, 0xC($a0)              ; t0 = the pointer member
    or   $t1, $t0, $a1              ; t1 = t0 | arg1
    or   $t2, $t1, $at              ; t2 = t1 | physBase
    lw   $t3, %lo(D_A0000000)($t2)  ; LOAD THROUGH t2 AS AN ADDRESS

The reference C is `*data = IO_READ(pihandle->baseAddress | devAddr);` -- the OR is genuinely there. What
differs is the OPERAND'S TYPE: `baseAddress` is a `u32` in `OSPiHandle`, so `u32 | u32` is an integer
address and `IO_READ` casts it. m2c instead declares the member as a pointer and dereferences the OR, so
the expression becomes `pointer | int`, which C has no operator for.

THE FIX IS THEREFORE NOT TO CHANGE THE `|`. It is to recognise the shape -- a dereference whose operand is
an OR-chain -- and place the cast where the reference places it, so the operands are integers and the
result is converted once, at the load:

    *(s32 *)((u32)arg0->unkC | arg1 | (u32)&D_A0000000)

WHY THAT IS SOUND AND NOT A GUESS: the assembly performs the OR in integer registers and then uses the
result as an address. The cast makes the C say exactly that. No offset is invented and no operator is
changed.

Measured before wiring, over the frozen frame's blocked states: how many carry this shape, and does the
cast compile them.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
VOIDFIX = BASE / "wide-intake-voidfix.json"


def cast_or_chain(source: str) -> tuple[str, list[dict]]:
    """`*(a | b | c)` -> `*(s32 *)((u32)a | (u32)b | (u32)c)`, and the store form likewise.

    The chain is taken as the longest run of `|`-joined operands inside the dereference, and every operand
    is cast to `u32` so the OR is performed in integers -- which is what the assembly does. Operands that
    are already casts or literals are left alone; a cast to a pointer type is re-cast from its base so the
    result is still an integer.
    """
    changes: list[dict] = []

    def operand(text: str) -> str:
        text = text.strip()
        if re.fullmatch(r"\(?\s*(?:s32|u32|s16|u16|s8|u8)\s*\)\s*&?\w+", text):
            return text                                   # already an integer-valued cast
        if re.fullmatch(r"0x[0-9A-Fa-f]+|\d+", text):
            return text
        return f"(u32){text}"

    # A dereference of an OR-chain: `*(X | Y ...)` or `*(X | Y ...) = ...`
    pattern = re.compile(r"\*\((?P<chain>[^()]*(?:\|[^()]*)+)\)")

    def replace(match: re.Match) -> str:
        parts = [part for part in match.group("chain").split("|")]
        if len(parts) < 2:
            return match.group(0)
        out = " | ".join(operand(part) for part in parts)
        changes.append({"before": match.group(0)[:70], "after": f"*(s32 *)({out})"[:70]})
        return f"*(s32 *)({out})"

    return pattern.sub(replace, source), changes


traced = json.loads(VOIDFIX.read_text(encoding="utf-8"))
blocked = [r for r in traced["rows"] if not r["sequence"]["compiled"]]
conn = sqlite3.connect(str(KB))
carried = fired = compiled = 0
shapes: Counter = Counter()
try:
    for row in blocked:
        name = row["function"]
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            continue
        initial = dict(context.initial_verdict or {})
        current, best, stderr = context.candidate, initial, initial.get("stderr") or ""
        for label in SEQUENCE:
            if not gated(label, stderr):
                continue
            ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                  "initial_verdict": {**initial, "stderr": stderr}}
            try:
                result = RUNNERS[label](ns, {})
            except Exception:                                          # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        if best.get("compiled"):
            continue
        rewritten, changes = cast_or_chain(current)
        if not changes:
            continue
        carried += 1
        verdict = context.compile_fn(rewritten)
        ok = bool(verdict.get("compiled"))
        fired += 1
        compiled += int(ok)
        first = next((ln.strip() for ln in (verdict.get("stderr") or "").splitlines() if ln.strip()), "")
        shapes["COMPILED" if ok else first[:52]] += 1
        if ok:
            print(f"   COMPILED {name:40} score={verdict.get('score')} changes={len(changes)}")
finally:
    conn.close()

print(f"\nstates carrying a `*(p | n)` dereference: {carried}")
print(f"  the cast compiles {compiled} of them")
print(f"\noutcomes: {dict(shapes.most_common(8))}")
