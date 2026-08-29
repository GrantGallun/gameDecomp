"""Does LENGTH cause refusals, or does DIFFICULTY?

Measured: refusal rate is 21% under 100 instructions and 67-78% above. But
longer functions are also harder, and both explanations predict exactly that
curve. Looking at more refusals cannot separate them.

So hold difficulty fixed and vary length. Take functions that NEVER refuse,
and pad their prompts with neutral filler -- text that adds no task
information and changes no requirement -- until the prompt is as long as a
large function's.

  refusals appear   -> length/position drives it (abstention under load)
  refusals absent   -> difficulty drives it, and the length story is wrong

The filler is deliberately inert: a repeated block of generic compiler prose.
Padding with anything task-relevant would be prompt enrichment, which is
refuted, and would confound the test all over again.
"""
import sqlite3
import sys
from pathlib import Path

from solver import llm, pipeline, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
ep, MODEL, DRAWS = llm.host(), "gpt-oss:20b", 3

FILLER_UNIT = """
/* Build note (informational only; does not affect the task above).
 * The toolchain compiles each translation unit independently and links the
 * resulting objects in a fixed order. Object layout is determined by the
 * assembler, and the linker resolves relocations afterwards. Debug sections
 * are emitted but not consulted during comparison. None of this changes what
 * you are asked to produce. */
"""

FUNCS = sys.argv[1:] or [
    "isRacePlayerRespawnSurfaceValid",
    "calculateRaceTimerDelta",
    "func_8005804C",
    "updateRacePlayerSmoothedPathOffset",
]


def refusal_rate(prompt: str, draws: int) -> tuple[int, int]:
    ref = 0
    for _ in range(draws):
        try:
            text, _m = llm.generate(ep, MODEL, prompt, timeout=420,
                                    think="low", num_thread=12,
                                    temperature=0.7)
        except Exception:
            continue
        if llm.is_refusal(llm.extract_c(text)) or llm.is_refusal(text):
            ref += 1
    return ref, draws


print(f"{len(FUNCS)} short/never-refusing functions, {DRAWS} draws per arm\n")
tot = {"short": [0, 0], "padded": [0, 0]}

for fn in FUNCS:
    ws = workspace.bootstrap(repo, fn)
    asm = workspace.target_asm(ws, fn)
    base = pipeline.build_prompt(repo, conn, fn, asm,
                                 workspace.m2c_draft(ws), "reshape",
                                 use_siblings=False)
    # pad to roughly the size of a 300-instruction function's prompt
    target_chars = 26000
    pad = FILLER_UNIT * max(1, (target_chars - len(base)) // len(FILLER_UNIT))
    padded = base.replace("TARGET ASSEMBLY:", pad + "\nTARGET ASSEMBLY:", 1)

    r1, d1 = refusal_rate(base, DRAWS)
    r2, d2 = refusal_rate(padded, DRAWS)
    tot["short"][0] += r1
    tot["short"][1] += d1
    tot["padded"][0] += r2
    tot["padded"][1] += d2
    print(f"  {fn[:38]:40} short {len(base):6}ch {r1}/{d1} refused   "
          f"padded {len(padded):6}ch {r2}/{d2} refused", flush=True)

print(f"\n===== WHY DO THEY REFUSE? =====")
for k, (r, d) in tot.items():
    print(f"  {k:8} {r:3}/{d:3} refused  ({100*r/max(1,d):3.0f}%)")
s = 100 * tot["short"][0] / max(1, tot["short"][1])
p = 100 * tot["padded"][0] / max(1, tot["padded"][1])
print(f"\n  delta from LENGTH ALONE: {p - s:+.0f} points")
print("  large jump  -> abstention under load; length itself drives it")
print("  no change   -> difficulty drives it and the length story is wrong")
