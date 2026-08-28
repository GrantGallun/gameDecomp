"""Split the READING of a function across instances, not the compiling.

The measured failure on long functions is p^N with p~0.97: one instance reads
449 instructions and must get every one right in a single pass. It refuses on
exactly those functions and never on short ones, which is the signature of
overload rather than objection.

Register allocation is global, so the COMPILATION cannot be split -- there is
no instruction prefix to freeze (measured: median prefix-exact depth 0).
But COMPREHENSION can be. One instance can describe instructions 1-60 without
holding the other 389, and a composer can work from summaries plus the
skeleton rather than from 66KB of assembly.

Each region gets a fresh instance with a bounded input. That is the "shifts and
breaks" idea: no single instance carries the whole load, and none inherits a
long degraded context.

This does NOT claim to beat the p^N bound. It claims the bound was measured on
a process that overloads one instance, and that a different process has a
different p.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from solver import llm

BRANCH = re.compile(r"\b(b|beq|bne|blez|bgtz|bltz|bgez|beqz|bnez|j|jr|jal|"
                    r"bc1t|bc1f)\w*\b")
LABEL = re.compile(r"^\s*\.?L?\w+:\s*$")

REGION_PROMPT = """\
Below is one REGION of a larger MIPS function compiled by IDO 5.3 at -O2.
You are not being asked to produce C, and you do not need the rest of the
function.

Describe, in at most 6 short lines, what this region does at the level of C
statements: which variables are read or written, the arithmetic performed, and
any conditional or loop structure. Name concrete offsets and registers where
they matter. If it is a recognisable idiom (array index, sign-extension, a
division by a power of two, a struct copy), say so.

REGION {index} of {total} (instructions {start}-{end}):
```
{asm}
```

Description:"""

COMPOSE_PROMPT = """\
You are reconstructing the original C source of one function compiled by
IDO 5.3 at -O2 for MIPS. The source is judged solely by whether recompiling it
reproduces the target object byte for byte.

The function was read in sections by separate passes. Their descriptions:

{summaries}

Full target assembly, for exact reference:
```
{asm}
```
{kb}{hints}
Rules:
- Output ONE self-contained C file in a single ```c code block. No prose.
- Only #include "common.h"; it already defines u8/s8/u16/s16/u32/s32/f32/f64.
  Supply any other types and externs INLINE, defined before use.
- C89: declarations at the start of a block. No do-while, no inline asm.
- Write source, not registers: operate on real variables, not locals named
  after t6/v0/a1.

Write the complete function now."""


@dataclass
class Region:
    index: int
    start: int
    end: int
    text: str


def split_regions(asm: str, target_size: int = 45) -> list[Region]:
    """Cut the function at control-flow boundaries, near `target_size`.

    Splitting at branches rather than every N lines keeps each region a
    coherent unit of control flow, so a description of it is meaningful on its
    own. A region cut mid-branch would need context the instance does not have.
    """
    lines = [l for l in asm.splitlines() if l.strip()]
    regions, cur, start = [], [], 0

    for i, line in enumerate(lines):
        cur.append(line)
        at_boundary = bool(BRANCH.search(line)) or bool(LABEL.match(line))
        if len(cur) >= target_size and at_boundary:
            regions.append(Region(len(regions) + 1, start, i, "\n".join(cur)))
            cur, start = [], i + 1

    if cur:
        regions.append(Region(len(regions) + 1, start, len(lines) - 1,
                              "\n".join(cur)))
    return regions


def summarise_regions(endpoint: str, model: str, regions: list[Region],
                      timeout: int = 300, num_thread: int = 12,
                      think: str = "", verbose: bool = False) -> list[str]:
    """One fresh instance per region. No shared context between them."""
    out = []
    for r in regions:
        prompt = REGION_PROMPT.format(index=r.index, total=len(regions),
                                      start=r.start, end=r.end, asm=r.text)
        text, _ = llm.generate(endpoint, model, prompt, timeout=timeout,
                               num_thread=num_thread, think=think,
                               num_predict=700, temperature=0.3)
        summary = text.strip()
        out.append(f"--- region {r.index} (instructions {r.start}-{r.end}) ---\n"
                   f"{summary}")
        if verbose:
            first = summary.splitlines()[0][:90] if summary.strip() else "(empty)"
            print(f"      region {r.index}/{len(regions)}: {first}", flush=True)
    return out


def compose_prompt(asm: str, summaries: list[str], kb: str = "",
                   hints: str = "") -> str:
    return COMPOSE_PROMPT.format(summaries="\n\n".join(summaries), asm=asm,
                                 kb=kb, hints=hints)
