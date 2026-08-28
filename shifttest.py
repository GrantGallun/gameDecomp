"""Test region-split reading against single-pass on functions that overload it."""

import sqlite3
import sys
from pathlib import Path

from patterns.catalog import hints_for_asm
from solver import context as kb_context
from solver import llm, shifts, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
endpoint = llm.host()
MODEL = "gpt-oss:20b"

REFUSAL_WORDS = ("i'm sorry", "i’m sorry", "can't provide", "can’t provide",
                 "cannot provide", "can't produce", "can’t produce")

for func in sys.argv[1:]:
    ws = workspace.bootstrap(repo, func)
    asm = workspace.target_asm(ws, func)
    regions = shifts.split_regions(asm)
    n_ins = len(asm.splitlines())
    print(f"\n=== {func} ===")
    print(f"  {n_ins} asm lines -> {len(regions)} regions "
          f"(~{n_ins//max(1,len(regions))} lines each)")

    summaries = shifts.summarise_regions(endpoint, MODEL, regions,
                                         think="low", verbose=True)
    refused = sum(1 for s in summaries
                  if any(w in s.lower() for w in REFUSAL_WORDS))
    print(f"  region summaries: {len(summaries)}, refusals: {refused}")

    prompt = shifts.compose_prompt(
        asm, summaries,
        kb=kb_context.for_function(conn, func),
        hints=hints_for_asm(asm))
    workspace.assert_uncontaminated(prompt, repo, func)

    best = None
    for i in (1, 2):
        text, _ = llm.generate(endpoint, MODEL, prompt, timeout=600,
                               think="low", num_thread=12, temperature=0.7)
        code = llm.extract_c(text)
        if any(w in code.lower()[:200] for w in REFUSAL_WORDS):
            print(f"  compose {i}: REFUSED")
            continue
        att = workspace.score(ws, repo, f"shift_{i}", code)
        state = "EXACT" if att.exact else (f"{att.score:.2f}%" if att.compiled
                                           else "no compile")
        print(f"  compose {i}: {state}  ({len(code)} bytes)")
        if best is None or att.score > best:
            best = att.score
    print(f"  -> best via shifts: {best if best is not None else 'all refused'}")
