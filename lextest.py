"""Three arms: does compressing the composer's input remove its refusals?

A  summaries + FULL raw asm      (current; refused 1 of 2)
B  summaries + lexicon only      (pure lookup, ~7% of raw)
C  summaries + lexicon + stripped asm (lossless, ~34% of raw)

Region summaries are computed ONCE per function and shared by all three arms,
so the only variable is the composer's input.
"""
import sqlite3, sys, time
from pathlib import Path
from patterns.catalog import hints_for_asm
from solver import context as kb_context
from solver import llm, shifts, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
ep, MODEL, DRAWS = llm.host(), "gpt-oss:20b", 2
REF = ("i'm sorry", "i\u2019m sorry", "can't provide", "can\u2019t provide",
       "cannot provide", "can't produce", "can\u2019t produce")
tally = {}

for func in sys.argv[1:]:
    ws = workspace.bootstrap(repo, func)
    asm = workspace.target_asm(ws, func)
    regions = shifts.split_regions(asm)
    print(f"\n=== {func} ({len(asm.splitlines())} lines, "
          f"{len(regions)} regions) ===", flush=True)
    sums = shifts.summarise_regions(ep, MODEL, regions, think="low",
                                    num_thread=12)
    rref = sum(1 for s in sums if any(w in s.lower() for w in REF))
    print(f"  regions: {len(sums)} summarised, {rref} refused", flush=True)

    kb, hints = kb_context.for_function(conn, func), hints_for_asm(asm)
    arms = {
        "A raw     ": shifts.compose_prompt(asm, sums, kb, hints),
        "B lex only": shifts.compressed_prompt(asm, sums, kb, hints,
                                               include_asm=False),
        "C lex+strip": shifts.compressed_prompt(asm, sums, kb, hints,
                                                include_asm=True),
    }
    for name, prompt in arms.items():
        workspace.assert_uncontaminated(prompt, repo, func)
        st = tally.setdefault(name, {"ref": 0, "comp": 0, "n": 0,
                                     "best": 0.0, "exact": 0})
        best = 0.0
        for i in range(DRAWS):
            st["n"] += 1
            t0 = time.time()
            try:
                text, _ = llm.generate(ep, MODEL, prompt, timeout=600,
                                       think="low", num_thread=12,
                                       temperature=0.7)
            except Exception as e:
                print(f"  {name} draw{i}: ERROR {type(e).__name__}", flush=True)
                continue
            code = llm.extract_c(text)
            if any(w in code.lower()[:250] for w in REF):
                st["ref"] += 1
                print(f"  {name} draw{i}: REFUSED  "
                      f"[{len(prompt)} char prompt]", flush=True)
                continue
            att = workspace.score(ws, repo, f"lex_{i}", code)
            if att.compiled:
                st["comp"] += 1
            if att.exact:
                st["exact"] += 1
            best = max(best, att.score if att.compiled else 0.0)
            state = ("EXACT" if att.exact else
                     (f"{att.score:.1f}%" if att.compiled else "no compile"))
            print(f"  {name} draw{i}: {state:11} "
                  f"[{len(prompt)} char prompt, {time.time()-t0:.0f}s]",
                  flush=True)
        st["best"] += best

print("\n\n===== TOTALS =====")
print(f"{'arm':12} {'draws':>6} {'refused':>8} {'compiled':>9} "
      f"{'exact':>6} {'sum-best':>9}")
for name, st in tally.items():
    print(f"{name:12} {st['n']:6} {st['ref']:8} {st['comp']:9} "
          f"{st['exact']:6} {st['best']:9.1f}")
