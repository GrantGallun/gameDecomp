"""Diff-guided refinement on the near-misses with correct control flow.

The shape analysis found 12 of 19 structural failures have ZERO branch delta --
the model got the program's structure right and is off by one to three
instructions in straight-line code. Those are not comprehension failures; they
are corrections waiting to be made.

The machinery already exists (refine.DIFF_PROMPT, strategy "fix-diff", the
highest mean of any strategy in the database at 50.3) but the PIPELINE never
calls it -- it samples fresh candidates and routes to reshape/retype instead. A
function at 85.82% with -1 instruction is thrown away and resampled.

This is not prompt enrichment. Nothing is added up front; the model is shown
what it actually produced versus what was wanted, after the fact.
"""
import datetime as dt
import sqlite3
import sys
from pathlib import Path

from patterns.catalog import hints_for_asm
from solver import llm, refine, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
ep, MODEL = llm.host(), "gpt-oss:20b"
ITERS = int(sys.argv[1]) if sys.argv[1:] else 3
PREFILL = '```c\n#include "common.h"\n'
CUT = dt.datetime(2026, 8, 29, 0, 0).timestamp()

# best prefilled candidate per function, highest scorers first
best: dict[str, tuple[float, str]] = {}
for name, src, sc in conn.execute(
        "select f.name, a.source_code, a.score from attempts a"
        " join functions f on f.addr=a.func_addr"
        " where a.created_at>=? and a.compiled=1 and a.score<100"
        " and a.source_code is not null", (CUT,)):
    if src and sc > best.get(name, (-1.0, ""))[0]:
        best[name] = (sc, src)

targets = sorted(best.items(), key=lambda kv: -kv[1][0])[:8]
print(f"{len(targets)} functions, {ITERS} refinement iterations each\n")

closed, improved, flat = [], [], []
for name, (score0, src) in targets:
    ws = workspace.bootstrap(repo, name)
    asm = workspace.target_asm(ws, name)
    att = workspace.score(ws, repo, "dr0", src, conn=conn, func=name,
                          strategy="diffrefine-base")
    cur_code, cur = src, att.score
    if not att.compiled:
        print(f"  {name[:38]:40} baseline does not rebuild -- skipped")
        continue

    history = ""
    for it in range(1, ITERS + 1):
        if att.exact:
            break
        prompt = refine.DIFF_PROMPT.format(
            asm=asm, score=att.score, code=cur_code, diff=(att.diff or "")[:4000],
            history=history, hints=hints_for_asm(asm))
        workspace.assert_uncontaminated(prompt, repo, name)
        try:
            text, _m = llm.generate(ep, MODEL, prompt, timeout=420,
                                    think="low", num_thread=12,
                                    temperature=0.4, prefill=PREFILL)
        except Exception as exc:
            print(f"    iter{it} ERROR {type(exc).__name__}")
            break
        code = llm.extract_c(text)
        if not code or llm.is_refusal(code):
            history += f"\niteration {it}: no usable output"
            continue
        a2 = workspace.score(ws, repo, f"dr{it}", code, conn=conn, func=name,
                             strategy="diffrefine")
        if a2.compiled and a2.score > cur:
            cur, cur_code, att = a2.score, code, a2
        else:
            history += (f"\niteration {it} scored "
                        f"{a2.score if a2.compiled else 0:.2f}, not kept")

    tag = "EXACT" if att.exact else f"{cur:.3f}%"
    print(f"  {name[:38]:40} {score0:7.3f} -> {tag:>9}  ({cur-score0:+.3f})",
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

print(f"\n===== DIFF-GUIDED REFINEMENT =====")
print(f"CLOSED to byte-exact : {len(closed)}")
for f in closed:
    print(f"    {f}")
print(f"improved             : {len(improved)}")
for f, a, b in improved:
    print(f"    {f}  {a:.3f} -> {b:.3f}  ({b-a:+.3f})")
print(f"no movement          : {len(flat)}")
