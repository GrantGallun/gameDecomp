"""Three ways to stop the refusals, on six functions that refuse 4/4.

Baseline is 100% refusal, so any reduction is unambiguous.

  A  current prompt
  B  LEXICAL -- strip source-reproduction wording. XSTest finds lexical
     overfitting is the primary cause of false refusals, and the model answers
     "I can't provide the full source code for that function" to a prompt that
     says "reconstructing the original C source". An earlier framing test
     removed GAME and COPYRIGHT words and never touched these.
  C  PREFILL -- end the prompt mid-answer with an open ```c fence. A refusal
     cannot begin if the first tokens are already code. Mechanical, and
     independent of why the model wanted to refuse.

Scores are reported too: forcing an answer out of a model that wanted to
abstain is not free, and a refusal-free arm that produces worse code is not an
improvement.
"""
import sqlite3
import sys
from pathlib import Path

from solver import llm, pipeline, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
ep, MODEL, DRAWS = llm.host(), "gpt-oss:20b", 3

# phrases that describe the task as REPRODUCING SOURCE, replaced with phrasing
# that describes it as producing code that assembles to given machine code
LEXICAL = [
    ("You are reconstructing the original C source of one function from its\ncompiled output.",
     "You are writing a C function that assembles to a given sequence of machine\ninstructions."),
    ("reconstructing the original C source of one function from its compiled output",
     "writing a C function that assembles to a given instruction sequence"),
    ("the source is judged solely by whether\nrecompiling it reproduces the target object byte for byte",
     "the result is judged solely by whether\ncompiling it yields the same object bytes"),
    ("the source is judged solely by whether recompiling it reproduces the target object byte for byte",
     "the result is judged solely by whether compiling it yields the same object bytes"),
    ("your\njob is to recover what a human WROTE", "your\njob is to produce an equivalent C form"),
    ("job is to recover what a human WROTE", "job is to produce an equivalent C form"),
    ("WRITE SOURCE, NOT REGISTERS", "WRITE ORDINARY C, NOT REGISTER SHUFFLING"),
    ("Original game code is terse", "Code of this kind is terse"),
    ("Write C that compiles to assembly matching the TARGET exactly.",
     "Produce C that compiles to the instruction sequence shown."),
]

PREFILL = "\n\nHere is the file:\n\n```c\n"

FUNCS = sys.argv[1:] or [
    "drawPulsingAssetTableSprite",
    "pushRaceCourseSurfaceBoundaryWithVelocity",
    "gameThreadMain",
    "drawMenuPanelBackdrop",
    "drawMenuAsciiFontTile",
    "updateRaceSplitscreenSelectPlayerCountIcons",
]

res = {a: {"n": 0, "ref": 0, "comp": 0, "scores": [], "exact": 0}
       for a in ("A current", "B lexical", "C prefill")}
applied_lexical = 0

for fn in FUNCS:
    ws = workspace.bootstrap(repo, fn)
    asm = workspace.target_asm(ws, fn)
    base = pipeline.build_prompt(repo, conn, fn, asm, workspace.m2c_draft(ws),
                                 "reshape", use_siblings=False)

    lex = base
    hits = 0
    for old, new in LEXICAL:
        if old in lex:
            lex = lex.replace(old, new)
            hits += 1
    applied_lexical += hits

    arms = {"A current": (base, ""),
            "B lexical": (lex, ""),
            "C prefill": (base + PREFILL, "```c\n")}

    line = f"  {fn[:38]:40}"
    for name, (prompt, prefix) in arms.items():
        r = res[name]
        for _ in range(DRAWS):
            try:
                text, _m = llm.generate(ep, MODEL, prompt, timeout=420,
                                        think="low", num_thread=12,
                                        temperature=0.7)
            except Exception:
                continue
            r["n"] += 1
            full = prefix + text
            if llm.is_refusal(text) or llm.is_refusal(llm.extract_c(full)):
                r["ref"] += 1
                continue
            code = llm.extract_c(full)
            if not code:
                continue
            att = workspace.score(ws, repo, f"rt_{name[0]}", code, conn=conn,
                                  func=fn, strategy=f"refusaltest-{name[0]}")
            if att.compiled:
                r["comp"] += 1
                r["scores"].append(att.score)
            if att.exact:
                r["exact"] += 1
        line += f" {name[0]}={r['ref']:2}ref"
    print(line, flush=True)

print(f"\nlexical substitutions applied across all prompts: {applied_lexical}")
assert applied_lexical > 0, "NO lexical substitution applied -- arm B is arm A"

print(f"\n{'arm':12}{'draws':>7}{'refused':>9}{'ref%':>7}"
      f"{'compiled':>10}{'exact':>7}{'mean':>8}")
for name, r in res.items():
    m = sum(r["scores"]) / len(r["scores"]) if r["scores"] else 0.0
    pct = 100 * r["ref"] / max(1, r["n"])
    print(f"{name:12}{r['n']:7}{r['ref']:9}{pct:6.0f}%{r['comp']:10}"
          f"{r['exact']:7}{m:8.1f}")
print("\nbaseline for these six functions was 4/4 refusals each = 100%")
