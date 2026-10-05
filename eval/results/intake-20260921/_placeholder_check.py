"""Does `m2c_placeholders` handle the placeholder shape that dominates this frame?

Observation that motivates the check: the frame's head blocker is `Syntax Error` at the function's OWN
line (7, 10, 11), and the drafts on those lines look like

    u64 __ll_mul(s64 a0_unk0, ? a0_unk4, s64 a1_unk0, ? a1_unk4) {
    void osSyncPrintf(s8 *fmt, ? arg1, ? arg2, ? arg3, ...) {

i.e. `?` in a PARAMETER list. `DECL_LINE` is anchored with `^[ \t]*`, so it cannot match those.
`placeholders()` is documented as covering "the placeholder also appears inside parameter lists" via
`PARAM = (?<=[(,])\\s*\\?(?=\\s*[,)*])`.

The probe reported "resolved=0" for several of these states and the error did not move, which is the
silent-decline shape. This counts, per draft: placeholders the module FINDS, placeholders a lexical scan
of the RAW text finds, and whether a rewrite leaves any `?` in a declaration position. It is a
measurement of the module, not a fix.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                          # noqa: E402
from solver import m2c_placeholders                                    # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

# A deliberately crude, INDEPENDENT count: every `?` that is not inside a comment or a string literal.
# If this and `placeholders()` disagree, one of them is wrong and the drafts say which.
RAW = re.compile(r"\?")


def raw_questions(code: str) -> int:
    return len(RAW.findall(m2c_placeholders._masked(code)))


def remaining_decl_questions(code: str) -> list[str]:
    """Lines that still put a `?` where a type specifier belongs, after a rewrite."""
    out = []
    for line in m2c_placeholders._masked(code).splitlines():
        stripped = line.strip()
        if "?" not in stripped:
            continue
        if re.match(r"^[A-Za-z_][\w \t*]*\b\d*\s*[A-Za-z_(].*\?\s*\*?\s*[A-Za-z_]", stripped) or \
           re.match(r"^(extern\s+)?\?", stripped) or re.search(r"[(,]\s*\?\s*[,)*]", stripped):
            out.append(stripped[:90])
    return out


frame = json.loads((BASE / "class-frame.json").read_text(encoding="utf-8"))["rows"]
conn = sqlite3.connect(str(KB))
totals = {"seen": 0, "found": 0, "raw": 0, "resolved": 0, "still_after": 0, "now_compiles": 0}
print(f"{'function':40} {'raw?':>5} {'found':>6} {'resolved':>9} {'after':>6}  {'compiles':>8}")
try:
    for entry in frame:
        name = entry["function"]
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            continue
        draft = context.candidate or ""
        raw = raw_questions(draft)
        found = m2c_placeholders.placeholders(draft)
        rewritten, names = m2c_placeholders.rewrite(draft)
        after = remaining_decl_questions(rewritten)
        if raw or found:
            verdict = context.compile_fn(rewritten)
            compiles = bool(verdict.get("compiled"))
        else:
            compiles = False
        totals["seen"] += 1
        totals["raw"] += raw
        totals["found"] += len(found)
        totals["resolved"] += int(bool(names))
        totals["still_after"] += len(after)
        totals["now_compiles"] += int(compiles)
        flag = ""
        if raw and not found:
            flag = "   <-- raw placeholders, module finds NONE"
        elif after:
            flag = f"   <-- {len(after)} left: {after[0][:60]}"
        print(f"{name:40} {raw:5} {len(found):6} {str(bool(names)):>9} {len(after):6}  "
              f"{str(compiles):>8}{flag}")
finally:
    conn.close()

print(f"\ntotals: {json.dumps(totals)}")
print("\nthe two shapes, quoted from the drafts:")
for name in ("__ll_mul", "osSyncPrintf", "alSynSetPan"):
    context, _ = build_context(REPO, name, conn=sqlite3.connect(str(KB)))
    if context is None:
        continue
    signature = next((ln for ln in context.candidate.splitlines()
                      if name + "(" in ln and "(" in ln), "")
    print(f"   {name:16} {signature.strip()[:100]}")
    print(f"      placeholders() -> {m2c_placeholders.placeholders(context.candidate)}")
    print(f"      rewrite changes it -> "
          f"{m2c_placeholders.rewrite(context.candidate)[0] != context.candidate}")
