"""Entry 2: sequential slice composition vs the one-shot composer.

Paired against entry 0's arm A on the SAME six functions (4/18 refused,
4/18 compiled, mean 39.0 over compiled).

Each slice sees its own ~45 lines plus the C accumulated so far -- never the
whole function. Refusals are counted at the slice level AND at the final
assembly, because they mean different things: a slice refusal is overload on a
bounded input (which should not happen), a final refusal is overload on the
accumulated C (which would mean the load came back).
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

from patterns.catalog import hints_for_asm
from solver import context as kb_context
from solver import llm, shifts, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
ep, MODEL, DRAWS = llm.host(), "gpt-oss:20b", 2
REF = ("i'm sorry", "i’m sorry", "can't provide", "can’t provide",
       "cannot provide", "can't produce", "can’t produce", "i cannot")

tiers = {e["function"]: e["tier"]
         for e in json.loads(Path("eval/sets/hard_v1.json").read_text())["dev"]}

n = refused_final = compiled = exact = 0
slice_total = slice_refused = slice_empty = 0
scores, rows = [], []
t0 = time.time()

for func in sys.argv[1:]:
    try:
        ws = workspace.bootstrap(repo, func)
        asm = workspace.target_asm(ws, func)
    except Exception as exc:
        print(f"!! {func}: {type(exc).__name__}", flush=True)
        continue
    regions = shifts.split_regions(asm)
    print(f"\n=== {func} [{tiers.get(func,'?')}] "
          f"{len(asm.splitlines())} lines, {len(regions)} slices ===",
          flush=True)
    kb, hints = kb_context.for_function(conn, func), hints_for_asm(asm)

    for d in range(DRAWS):
        t = time.time()
        try:
            code, st = shifts.sequential_compose(
                ep, MODEL, asm, regions, kb=kb, hints=hints,
                think="low", temperature=0.5, verbose=(d == 0))
        except Exception as exc:
            print(f"  draw{d}: ERROR {type(exc).__name__} -- not an attempt",
                  flush=True)
            continue
        n += 1
        slice_total += st["slices"]
        slice_refused += st["refused"]
        slice_empty += st["empty"]

        if any(w in code.lower()[:300] for w in REF):
            refused_final += 1
            rows.append((func, d, "refused-final", 0.0, st))
            print(f"  draw{d}: FINAL REFUSED  "
                  f"(slices {st['slices']}, refused {st['refused']})",
                  flush=True)
            continue
        att = workspace.score(ws, repo, f"seq_{d}", code)
        if att.compiled:
            compiled += 1
            scores.append(att.score)
        if att.exact:
            exact += 1
        state = ("EXACT" if att.exact else
                 (f"{att.score:.1f}%" if att.compiled else "no compile"))
        rows.append((func, d, state, att.score if att.compiled else 0.0, st))
        print(f"  draw{d}: {state:11} [{st['slices']} slices, "
              f"{st['refused']} refused, {st['decls']} decls, "
              f"{time.time()-t:.0f}s]", flush=True)

mean = sum(scores) / len(scores) if scores else 0.0
med = sorted(scores)[len(scores) // 2] if scores else 0.0
print(f"\n\n===== ENTRY 2 ({time.time()-t0:.0f}s) =====")
print(f"draws                {n}")
print(f"SLICE refusals       {slice_refused}/{slice_total} slices"
      f"   (empty stmts: {slice_empty})")
print(f"FINAL refusals       {refused_final}/{n}")
print(f"compiled             {compiled}/{n}")
print(f"exact                {exact}")
print(f"mean/median score    {mean:.1f} / {med:.1f}   (n={len(scores)})")
print()
print("BASELINE, entry 0 arm A, same 6 functions:")
print("  refused 4/18 (22%)   compiled 4/18 (22%)   mean 39.0  median 29.7")
print()
rr = 100 * refused_final / n if n else 0
cr = 100 * compiled / n if n else 0
print(f"THIS RUN:  refused {rr:.0f}%   compiled {cr:.0f}%")
print("PREDICTION was: refusals drop sharply AND compile rate rises.")
Path("entry2_rows.json").write_text(
    json.dumps([(f, d, s, sc) for f, d, s, sc, _ in rows], indent=1))
