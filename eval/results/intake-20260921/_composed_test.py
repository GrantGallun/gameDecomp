"""The `*(p | n)` shape only appears AFTER `opaque_variant` types the pointer, so test them composed.

Sequencing, measured rather than assumed:
    the sequence's baseline leaves `osEPiRawReadIo` blocked with `Syntax Error` (the `|` is not parsed that
    far), so a scan of the phase-4 blocked states finds NO `*(p | n)` -- confirmed, 0 states.
    `opaque_variant` turns `void *arg0` into `struct T *arg0`, which resolves `arg0->unkC` AND makes the
    `|` an actual type error (`Dereferenced a non-pointer`).

So the two changes compose, and measuring either alone understates both. This measures the composition on
every blocked state, and reports which one did what.

Read-only apart from the attempts the oracle logs.
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
from solver import compile_obligations, workspace                          # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

# `*(...)` with a `|` inside, on ONE LINE. Two earlier patterns failed here for instructive reasons:
#   `[^()]*` cannot cross the `( )` in a nearby macro comment, which m2c drafts are full of;
#   `[^()\n]*` cannot cross the `(s32)` cast that is PART of the expression.
# Taking everything up to the line's last `)` is enough, because the chain's operands cannot contain an
# unbalanced `)` -- and it is checked against the real line rather than assumed.
DEREF_OR = re.compile(r"\*\((?P<chain>[^;\n]*\|[^;\n]*)\)")


def cast_or_chain(source: str) -> tuple[str, int]:
    """`*(a | b | c)` -> `*(s32 *)((u32)a | (u32)b | (u32)c)`.

    The assembly performs the OR in integer registers and uses the result as an address -- verified on
    `osEPiRawReadIo`: `or $t1,$t0,$a1` / `or $t2,$t1,$at` / `lw $t3,%lo(D_A0000000)($t2)`. The cast makes
    the C say that, changing no operator and inventing no offset.
    """
    count = 0

    def operand(text: str) -> str:
        text = text.strip()
        if re.fullmatch(r"\(?\s*(?:s32|u32|s16|u16|s8|u8)\s*\)\s*&?\w+", text):
            return text
        if re.fullmatch(r"0x[0-9A-Fa-f]+|\d+", text):
            return text
        return f"(u32){text}"

    def replace(match: re.Match) -> str:
        nonlocal count
        parts = match.group("chain").split("|")
        if len(parts) < 2:
            return match.group(0)
        count += 1
        return "*(s32 *)(" + " | ".join(operand(part) for part in parts) + ")"

    return DEREF_OR.sub(replace, source), count


traced = json.loads((BASE / "wide-intake-voidfix.json").read_text(encoding="utf-8"))
blocked = [r for r in traced["rows"] if not r["sequence"]["compiled"]]
print(f"blocked states: {len(blocked)}\n")

conn = sqlite3.connect(str(KB))
stats: Counter = Counter()
wins: list[tuple[str, float, int, int]] = []
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

        asm = workspace.target_asm(workspace.bootstrap(REPO, name), name)
        typed, report = compile_obligations.opaque_variant(REPO, name, current, asm)
        derived = len(report.get("derived_void_parameters") or [])
        if typed != current:
            stats["opaque_variant emitted something"] += 1
        casted, count = cast_or_chain(typed)
        if count:
            stats["carries a *(p | n) dereference"] += 1
        if casted == current:
            continue
        verdict = context.compile_fn(casted)
        ok = bool(verdict.get("compiled"))
        stats["COMPILED" if ok else "still fails"] += 1
        if ok:
            wins.append((name, verdict.get("score") or 0.0, derived, count))
finally:
    conn.close()

print(f"outcomes: {json.dumps(dict(stats.most_common()), indent=2)}")
print(f"\ncompiled by opaque_variant + the OR-chain cast: {len(wins)}")
for name, score, derived, count in sorted(wins, key=lambda w: -w[1]):
    print(f"   {name:44} score={score:7.3f}  void-params={derived} or-chains={count}")
