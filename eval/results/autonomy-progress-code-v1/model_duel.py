"""Head-to-head on the same functions, with the fixed harness.

qwen2.5-coder:14b was judged "attempts where gpt-oss refuses and still scores
~0" -- but that was measured with fence-leaking extraction, no prefill, and
refusal text being compiled as candidates. That verdict is not safe now.

What matters is not just which model scores higher. With a perfect verifier,
running both and keeping the best is FREE: the oracle cannot be fooled, so a
weaker model that succeeds on DIFFERENT functions is strictly additive. So this
measures overlap and complement, not just means.
"""
import datetime as dt
import sqlite3
import sys
from pathlib import Path

from solver import llm, pipeline, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
ep = llm.host()
DRAWS = 2
PREFILL = '```c\n#include "common.h"\n'
MODELS = ["gpt-oss:20b", "qwen2.5-coder:14b"]

FUNCS = sys.argv[1:] or [
    "drawMenuSolidRect", "calculateRaceTimerDelta", "func_8005804C",
    "updateRacePlayerSmoothedPathOffset", "renderRaceUiSingleTrailEffect",
    "initControllerPakRaceRecordSaveFlow", "gameThreadMain",
    "compressRaceRecordReplayData",
]

res = {m: {"n": 0, "ref": 0, "comp": 0, "exact": 0, "s": [], "best": {}}
       for m in MODELS}

for fn in FUNCS:
    ws = workspace.bootstrap(repo, fn)
    asm = workspace.target_asm(ws, fn)
    prompt = pipeline.build_prompt(repo, conn, fn, asm,
                                   workspace.m2c_draft(ws), "reshape",
                                   use_siblings=False)
    line = f"  {fn[:34]:36}"
    for model in MODELS:
        r = res[model]
        best = 0.0
        for _ in range(DRAWS):
            try:
                text, _m = llm.generate(ep, model, prompt, timeout=420,
                                        think="low", num_thread=12,
                                        temperature=0.7, prefill=PREFILL)
            except Exception as exc:
                print(f"    {model} ERROR {type(exc).__name__}", flush=True)
                continue
            r["n"] += 1
            code = llm.extract_c(text)
            if llm.is_refusal(text) or llm.is_refusal(code):
                r["ref"] += 1
                continue
            if not code:
                continue
            att = workspace.score(ws, repo, f"duel_{model[:4]}", code,
                                  conn=conn, func=fn,
                                  strategy=f"duel-{model}")
            if att.compiled:
                r["comp"] += 1
                r["s"].append(att.score)
                best = max(best, att.score)
            if att.exact:
                r["exact"] += 1
        r["best"][fn] = best
        line += f"  {model.split(':')[0][:8]}={best:6.2f}"
    print(line, flush=True)

print(f"\n{'model':20}{'draws':>7}{'refused':>9}{'compiled':>10}"
      f"{'exact':>7}{'mean':>8}{'max':>8}")
for m, r in res.items():
    mean = sum(r["s"]) / len(r["s"]) if r["s"] else 0.0
    mx = max(r["s"]) if r["s"] else 0.0
    print(f"{m:20}{r['n']:7}{r['ref']:9}{r['comp']:10}{r['exact']:7}"
          f"{mean:8.1f}{mx:8.1f}")

# the union is the point: a weaker model that wins on DIFFERENT functions is
# strictly additive, because the oracle picks the winner for free
a, b = MODELS
wins_a = [f for f in FUNCS if res[a]["best"].get(f, 0) > res[b]["best"].get(f, 0)]
wins_b = [f for f in FUNCS if res[b]["best"].get(f, 0) > res[a]["best"].get(f, 0)]
union = sum(max(res[a]["best"].get(f, 0), res[b]["best"].get(f, 0))
            for f in FUNCS) / max(1, len(FUNCS))
solo_a = sum(res[a]["best"].get(f, 0) for f in FUNCS) / max(1, len(FUNCS))
solo_b = sum(res[b]["best"].get(f, 0) for f in FUNCS) / max(1, len(FUNCS))
print(f"\nper-function best score, averaged over {len(FUNCS)} functions:")
print(f"  {a:22} {solo_a:6.2f}")
print(f"  {b:22} {solo_b:6.2f}")
print(f"  UNION (oracle picks)   {union:6.2f}")
print(f"\n{a} wins on {len(wins_a)}, {b} wins on {len(wins_b)}")
for f in wins_b:
    print(f"    {b} wins: {f}")
print("\nIf the union beats both, a cascade is worth building; if one model")
print("dominates every function, it is not.")
