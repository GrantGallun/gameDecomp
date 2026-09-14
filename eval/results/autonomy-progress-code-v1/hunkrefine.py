"""Refine ONE diff hunk at a time instead of the whole diff.

Measured: whole-diff refinement corrects a 52-instruction function with 8
differing lines (91.75 -> 99.46) and does nothing at all for a 265-instruction
function with 158 differing lines -- six of eight returned the IDENTICAL score
three times running, handed a diff and giving back the same code.

So the model localises a small correction and cannot localise a large one. The
fix is not better glasses, it is a smaller thing to look at: split the diff into
hunks and correct one at a time, so every step is the 8-line kind that works.

This is the shifts idea applied where it fits. Comprehension turned out not to
be the bottleneck -- the model reads control flow correctly, 12 of 19 structural
failures have zero branch delta. CORRECTION is inherently local, and that is
where splitting should pay.

Earliest hunk first: a divergence early in the function shifts everything after
it, so later hunks are often consequences rather than independent faults.
"""
import datetime as dt
import re
import sqlite3
import sys
from pathlib import Path

from solver import llm, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
ep, MODEL = llm.host(), "gpt-oss:20b"
PREFILL = '```c\n#include "common.h"\n'
CUT = dt.datetime(2026, 8, 29, 0, 0).timestamp()
MAX_HUNKS = int(sys.argv[1]) if sys.argv[1:] else 4

HUNK_PROMPT = """\
You are correcting ONE SPECIFIC MISMATCH in a C function that already compiles
and is {score:.2f}% correct. Everything else about it is right -- change as
little as possible.

TARGET ASSEMBLY (what your C must produce):
```
{asm}
```

CURRENT C:
```c
{code}
```

THE ONE MISMATCH TO FIX (`-` = target expects, `+` = your code produced):
```
{hunk}
```

Everything outside this mismatch already matches and must not change.

What source shape produces the target's instructions here:
- extra sll 16 / sra 16 means the value is s16, not s32
- bgez then addiu 2^n-1 then sra n is signed division by 2^n, never >> n
- an extra or missing intermediate local changes register allocation and
  instruction order, and is often the entire difference
- lui + addiu is a 32-bit address load: a reference to a named global
- an extra jal means you called something the target computes inline

Output the COMPLETE corrected C file in one ```c block.
"""


def hunks(diff: str) -> list[str]:
    """Contiguous runs of +/- lines, each with a little context."""
    lines = [l for l in diff.splitlines()
             if not l.startswith(("---", "+++", "@@"))]
    out, cur = [], []
    for l in lines:
        if l.startswith(("-", "+")):
            cur.append(l)
        elif cur:
            out.append("\n".join(cur))
            cur = []
    if cur:
        out.append("\n".join(cur))
    return out


best: dict[str, tuple[float, str]] = {}
for name, src, sc in conn.execute(
        "select f.name, a.source_code, a.score from attempts a"
        " join functions f on f.addr=a.func_addr"
        " where a.created_at>=? and a.compiled=1 and a.score<100"
        " and a.source_code is not null", (CUT,)):
    if src and sc > best.get(name, (-1.0, ""))[0]:
        best[name] = (sc, src)

targets = sorted(best.items(), key=lambda kv: -kv[1][0])[:8]
print(f"{len(targets)} functions, up to {MAX_HUNKS} hunks each\n")

closed, improved, flat = [], [], []
for name, (score0, src) in targets:
    ws = workspace.bootstrap(repo, name)
    asm = workspace.target_asm(ws, name)
    att = workspace.score(ws, repo, "hr0", src, conn=conn, func=name,
                          strategy="hunkrefine-base")
    if not att.compiled:
        print(f"  {name[:38]:40} baseline does not rebuild -- skipped")
        continue
    cur_code, cur = src, att.score
    hs = hunks(att.diff or "")
    fixed_any = 0

    for hi, h in enumerate(hs[:MAX_HUNKS], 1):
        if att.exact:
            break
        prompt = HUNK_PROMPT.format(score=cur, asm=asm, code=cur_code,
                                    hunk=h[:1500])
        workspace.assert_uncontaminated(prompt, repo, name)
        try:
            text, _m = llm.generate(ep, MODEL, prompt, timeout=420,
                                    think="low", num_thread=12,
                                    temperature=0.3, prefill=PREFILL)
        except Exception:
            continue
        code = llm.extract_c(text)
        if not code or llm.is_refusal(code):
            continue
        a2 = workspace.score(ws, repo, f"hr{hi}", code, conn=conn, func=name,
                             strategy="hunkrefine")
        if a2.compiled and a2.score > cur:
            cur, cur_code, att = a2.score, code, a2
            fixed_any += 1
            hs = hunks(att.diff or "")      # diff changed; re-split

    tag = "EXACT" if att.exact else f"{cur:.3f}%"
    print(f"  {name[:38]:40} {score0:7.3f} -> {tag:>9}  ({cur-score0:+.3f})"
          f"  [{len(hunks(att.diff or ''))} hunks left, {fixed_any} fixed]",
          flush=True)
    if att.exact:
        closed.append(name)
        Path("matched_recovered").mkdir(exist_ok=True)
        Path(f"matched_recovered/{name}.c").write_text(cur_code,
                                                       encoding="utf-8")
    elif cur - score0 > 0.005:
        improved.append((name, score0, cur))
    else:
        flat.append(name)

print(f"\n===== HUNK-SCOPED REFINEMENT =====")
print(f"CLOSED to byte-exact : {len(closed)}")
for f in closed:
    print(f"    {f}")
print(f"improved             : {len(improved)}")
for f, a, b in improved:
    print(f"    {f}  {a:.3f} -> {b:.3f}  ({b-a:+.3f})")
print(f"no movement          : {len(flat)}")
print("\nwhole-diff refinement improved 1 of 8; this is the comparison.")
