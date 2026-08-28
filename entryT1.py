"""Entry T1: tell the model the SDK types it already has.

The prompt currently says common.h defines only u8..f64 and instructs the model
to "supply any OTHER types INLINE". Both halves are wrong for SDK types:
common.h includes <PR/mbi.h>, so Gfx/Vtx/Mtx and friends already exist, and
defining them inline would be a redefinition error.

Verified by compiling before being named here: Gfx, Vtx, Vtx_t, Mtx, Vp, Vp_t,
Light, Ambient, Lights1, LookAt, Hilite, TexRect, Gwords all compile.
OSTask/OSMesgQueue/OSThread do NOT and are deliberately not named.

ONE VARIABLE: the type paragraph. Everything else -- asm, m2c draft, kb, hints,
temperature, draws -- is identical between arms.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path
from statistics import mean, median

from solver import llm, pipeline, refine, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
ep, MODEL, DRAWS = llm.host(), "gpt-oss:20b", 2

OLD = '''- "common.h" ALREADY defines u8, s8, u16, s16, u32, s32, u64, s64, f32, f64.
  Do NOT redeclare or typedef any of them -- redeclaring is a compile error.
- Supply any OTHER types (structs, unions, enums) and every extern declaration
  INLINE. Define a type BEFORE any declaration that uses it.'''

NEW = '''- "common.h" ALREADY defines u8, s8, u16, s16, u32, s32, u64, s64, f32, f64
  AND the Nintendo 64 SDK graphics types: Gfx, Gwords, Vtx, Vtx_t, Mtx, Vp,
  Vp_t, Light, Ambient, Lights1, LookAt, Hilite, TexRect.
  Do NOT redeclare or typedef ANY of them -- redeclaring is a compile error.
  USE them rather than reinventing them with raw pointer arithmetic. A display
  list is built with Gfx*, where g + 1 advances 8 bytes:
      Gfx *g = gSomeDlPtr;
      gSomeDlPtr = g + 1;
      g->words.w0 = 0xE7000000;
      g->words.w1 = 0;
  Writing that as u32* with (u8*)p + 8 produces different code and will not
  match. Note g->words.w0 and g->words.w1 are DIFFERENT locations; *p and p[0]
  are the SAME location.
  OS types (OSTask, OSMesgQueue, OSThread) are NOT available -- declare those
  inline if you need them.
- Supply any OTHER types (game-specific structs, unions, enums) and every
  extern declaration INLINE. Define a type BEFORE any declaration that uses it.'''

assert OLD in refine.FIRST_PROMPT, "FIRST_PROMPT text changed -- update OLD"

v3 = {e["function"]: e["tier"]
      for e in json.loads(Path("eval/sets/sbk1_v3.json").read_text())["dev"]}
hard = [e["function"] for e in
        json.loads(Path("eval/sets/hard_v1.json").read_text())["dev"]]
funcs = [f for f in hard if v3.get(f) == "medium"]
if sys.argv[1:]:
    funcs = funcs[:int(sys.argv[1])]
print(f"entry T1: {len(funcs)} failing medium functions, {DRAWS} draws, 2 arms")

REF = ("i'm sorry", "cannot provide", "can't provide", "can't produce")
res = {"A current": {"n": 0, "comp": 0, "exact": 0, "ref": 0, "s": []},
       "B sdk types": {"n": 0, "comp": 0, "exact": 0, "ref": 0, "s": []}}
uses_gfx = {"A current": 0, "B sdk types": 0}
t0 = time.time()

for fn in funcs:
    try:
        ws = workspace.bootstrap(repo, fn)
        asm = workspace.target_asm(ws, fn)
        draft = workspace.m2c_draft(ws)
    except Exception as exc:
        print(f"!! {fn}: {type(exc).__name__}", flush=True)
        continue
    pa = pipeline.build_prompt(repo, conn, fn, asm, draft, "reshape",
                               use_siblings=False)
    pb = pa.replace(OLD, NEW)
    assert pb != pa, f"TYPE PARAGRAPH NOT SUBSTITUTED for {fn}"
    workspace.assert_uncontaminated(pa, repo, fn)
    workspace.assert_uncontaminated(pb, repo, fn)

    line = f"  {fn[:34]:36}"
    for name, prompt in (("A current", pa), ("B sdk types", pb)):
        for d in range(DRAWS):
            try:
                text, _ = llm.generate(ep, MODEL, prompt, timeout=600,
                                       think="low", num_thread=12,
                                       num_predict=6000, temperature=0.7)
            except Exception:
                continue
            code = llm.extract_c(text)
            r = res[name]
            r["n"] += 1
            if any(w in code.lower()[:300] for w in REF):
                r["ref"] += 1
                continue
            if "Gfx" in code:
                uses_gfx[name] += 1
            att = workspace.score(ws, repo, f"t1_{name[0]}{d}", code)
            if att.compiled:
                r["comp"] += 1
                r["s"].append(att.score)
            if att.exact:
                r["exact"] += 1
        best = max(res[name]["s"][-DRAWS:], default=0.0)
        line += f"{name[0]}={best:5.1f}  "
    print(line, flush=True)

print(f"\n===== ENTRY T1 ({time.time()-t0:.0f}s) =====")
print(f"{'arm':13}{'draws':>6}{'refused':>8}{'compiled':>9}{'exact':>6}"
      f"{'mean':>8}{'median':>8}{'used Gfx':>9}")
for name, r in res.items():
    m = mean(r["s"]) if r["s"] else 0.0
    md = median(r["s"]) if r["s"] else 0.0
    print(f"{name:13}{r['n']:6}{r['ref']:8}{r['comp']:9}{r['exact']:6}"
          f"{m:8.1f}{md:8.1f}{uses_gfx[name]:9}")
a, b = res["A current"], res["B sdk types"]
dm = (mean(b["s"]) if b["s"] else 0) - (mean(a["s"]) if a["s"] else 0)
de = b["exact"] - a["exact"]
print(f"\ndelta mean {dm:+.1f}   delta exact {de:+d}")
print("PREDICTION: mean rises >10 AND/OR new exact matches appear.")
print("KILL: mean delta <10 and exact delta <3 -> type vocabulary is not the fix.")
