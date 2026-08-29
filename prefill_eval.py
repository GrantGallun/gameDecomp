"""Does prefill produce ANSWERS, or just non-refusals?

Prefill eliminates refusals (9/9 -> 0/9, confirmed). That test measured
refusals only. Region splitting already removed 100% of refusals and gained
zero matches, so "no longer refuses" is not evidence of capability.

This measures what actually matters: compile rate, score, exact -- on the six
functions that refused every draw.
"""
import sqlite3
import sys
from pathlib import Path

from solver import llm, pipeline, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
ep, MODEL, DRAWS = llm.host(), "gpt-oss:20b", 3
PREFILL = '```c\n#include "common.h"\n'

FUNCS = sys.argv[1:] or [
    "drawPulsingAssetTableSprite",
    "pushRaceCourseSurfaceBoundaryWithVelocity",
    "gameThreadMain",
    "drawMenuPanelBackdrop",
    "drawMenuAsciiFontTile",
    "updateRaceSplitscreenSelectPlayerCountIcons",
]

res = {k: {"n": 0, "ref": 0, "comp": 0, "exact": 0, "s": []}
       for k in ("no prefill", "PREFILLED")}

for fn in FUNCS:
    ws = workspace.bootstrap(repo, fn)
    asm = workspace.target_asm(ws, fn)
    prompt = pipeline.build_prompt(repo, conn, fn, asm,
                                   workspace.m2c_draft(ws), "reshape",
                                   use_siblings=False)
    line = f"  {fn[:38]:40}"
    for label, pre in (("no prefill", ""), ("PREFILLED", PREFILL)):
        r = res[label]
        best = 0.0
        for _ in range(DRAWS):
            try:
                text, _m = llm.generate(ep, MODEL, prompt, timeout=420,
                                        think="low", num_thread=12,
                                        temperature=0.7, prefill=pre)
            except Exception as exc:
                print(f"    {label} ERROR {type(exc).__name__}", flush=True)
                continue
            r["n"] += 1
            code = llm.extract_c(text)
            if llm.is_refusal(text) or llm.is_refusal(code):
                r["ref"] += 1
                continue
            if not code:
                continue
            att = workspace.score(ws, repo, f"pf_{label[0]}", code, conn=conn,
                                  func=fn, strategy=f"prefill-{label[0]}")
            if att.compiled:
                r["comp"] += 1
                r["s"].append(att.score)
                best = max(best, att.score)
            if att.exact:
                r["exact"] += 1
        line += f"  {label}: {best:6.2f}"
    print(line, flush=True)

print(f"\n{'arm':12}{'draws':>7}{'refused':>9}{'compiled':>10}"
      f"{'exact':>7}{'mean':>8}{'max':>8}")
for k, r in res.items():
    m = sum(r["s"]) / len(r["s"]) if r["s"] else 0.0
    mx = max(r["s"]) if r["s"] else 0.0
    print(f"{k:12}{r['n']:7}{r['ref']:9}{r['comp']:10}{r['exact']:7}"
          f"{m:8.1f}{mx:8.1f}")
print("\nrefusals gone is NOT the result; compiled/exact is.")
