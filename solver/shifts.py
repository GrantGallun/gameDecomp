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


# --- compression + lexicon -------------------------------------------------
#
# The composer needs exact operands, offsets and symbol names to be byte-exact,
# and that detail is exactly what overloads it. The way out is not to drop the
# detail but to stop shipping it as 11KB of prose: strip what carries no
# information, and index the rest so the composer LOOKS UP a fact instead of
# re-reading the function to find it.
#
# All of this is mechanically derived from the assembly. It is evidence, not
# inference -- no model output feeds it, and it is reproducible from the binary.

ADDR_COMMENT = re.compile(r"/\*.*?\*/")
WIDTH = {"lb": 1, "lbu": 1, "sb": 1, "lh": 2, "lhu": 2, "sh": 2,
         "lw": 4, "sw": 4, "lwc1": 4, "swc1": 4, "lwl": 4, "lwr": 4,
         "ld": 8, "sd": 8, "ldc1": 8, "sdc1": 8}
SIGNED = {"lb": "s", "lh": "s", "lw": "s", "lbu": "u", "lhu": "u"}

INSN = re.compile(r"^\s*(\w[\w.]*)\s*(.*)$")
MEM = re.compile(r"^(\$\w+),\s*(-?(?:0x)?[0-9A-Fa-f]+)\((\$\w+)\)$")
LO_MEM = re.compile(r"^(\$\w+),\s*%lo\(([\w.]+)\)\((\$\w+)\)$")
HI = re.compile(r"%hi\(([\w.]+)\)")
IMM_OPS = {"addiu", "addi", "ori", "andi", "xori", "slti", "sltiu",
           "sll", "srl", "sra", "lui"}


def strip_asm(asm: str) -> str:
    """Remove the address/encoding comments. Lossless, and roughly halves it.

    `/* 48784 80047B84 0005C400 */` is file offset, vram address and raw
    encoding. None of it constrains the C source, and it is ~30 of every 61
    characters. Shipping it spends half the context budget on noise.
    """
    out = []
    for line in asm.splitlines():
        line = ADDR_COMMENT.sub("", line).rstrip()
        if line.strip():
            out.append(re.sub(r"\s{2,}", " ", line).strip())
    return "\n".join(out)


def lexicon(asm: str) -> str:
    """An index of every exact fact a summary would blur.

    Summaries are lossy on precisely the things byte-exactness depends on:
    which global, what offset, which constant, how big the frame. Those are
    finite and extractable, so they can be listed once as a lookup table rather
    than recovered by re-reading the assembly.
    """
    frame = None
    saves: list[tuple[str, str]] = []
    globals_: dict[str, set[str]] = {}
    addr_of: set[str] = set()
    calls: list[str] = []
    fields: dict[str, dict[str, str]] = {}
    consts: set[str] = set()
    backward = 0
    labels_seen: set[str] = set()

    for raw in strip_asm(asm).splitlines():
        if raw.startswith("glabel") or raw.endswith(":"):
            labels_seen.add(raw.rstrip(":").split()[-1])
            continue
        m = INSN.match(raw)
        if not m:
            continue
        op, rest = m.group(1), m.group(2).strip()
        parts = [p.strip() for p in rest.split(",")]

        if op == "jal":
            calls.append(rest.strip())
        elif op.startswith("b") and rest:
            tgt = parts[-1]
            if tgt in labels_seen:          # target already emitted => loop
                backward += 1

        for sym in HI.findall(rest):
            globals_.setdefault(sym, set())
        if op == "addiu" and "%lo(" in rest:
            s = re.search(r"%lo\(([\w.]+)\)", rest)
            if s:
                addr_of.add(s.group(1))

        lo = LO_MEM.match(rest)
        if lo and op in WIDTH:
            globals_.setdefault(lo.group(2), set()).add(
                f"{WIDTH[op]}b{SIGNED.get(op, '')}")
            continue

        mem = MEM.match(rest)
        if mem and op in WIDTH:
            reg, off, base = mem.group(1), mem.group(2), mem.group(3)
            if base == "$sp":
                if reg.startswith(("$s", "$ra", "$fp"))                         and (reg, off) not in saves:
                    # sw saves it and lw restores it from the same slot;
                    # listing the slot twice implies a frame twice as busy.
                    saves.append((reg, off))
            else:
                fields.setdefault(base, {})[off] = \
                    f"{WIDTH[op]}b{SIGNED.get(op, '')}"
            continue

        if op == "addiu" and len(parts) == 3 and parts[0] == parts[1] == "$sp":
            frame = parts[2].lstrip("-")
        elif op in IMM_OPS and len(parts) == 3 and "%" not in parts[2]:
            v = parts[2]
            if v.startswith(("0x", "-0x")) or v.lstrip("-").isdigit():
                if v not in ("0", "1", "2"):
                    consts.add(v)

    out = ["LEXICON -- exact facts extracted from the target. Consult this "
           "instead of re-deriving them."]
    if frame:
        out.append(f"frame: {frame} bytes"
                   + (f"; saved: {', '.join(f'{r}@{o}' for r, o in saves)}"
                      if saves else ""))
    if globals_:
        out.append("globals: " + ", ".join(
            f"{s}({'/'.join(sorted(w)) if w else 'addr'})"
            for s, w in sorted(globals_.items())))
    if addr_of:
        out.append("address-taken (array or struct base): "
                   + ", ".join(sorted(addr_of)))
    if calls:
        seen = list(dict.fromkeys(calls))
        out.append(f"calls ({len(calls)} sites): " + ", ".join(seen))
    for base, offs in sorted(fields.items()):
        out.append(f"fields via {base}: " + ", ".join(
            f"{o}:{w}" for o, w in sorted(offs.items(),
                                          key=lambda kv: int(kv[0], 0))))
    if consts:
        out.append("constants: " + ", ".join(sorted(consts)))
    out.append(f"loops: {backward} backward branch(es)")
    return "\n".join(out)


COMPRESSED_PROMPT = """\
You are reconstructing the original C source of one function compiled by
IDO 5.3 at -O2 for MIPS. The source is judged solely by whether recompiling it
reproduces the target object byte for byte.

The function was read in sections by separate passes. Their descriptions:

{summaries}

{lex}
{asm_block}{kb}{hints}
Rules:
- Output ONE self-contained C file in a single ```c code block. No prose.
- Only #include "common.h"; it already defines u8/s8/u16/s16/u32/s32/f32/f64.
  Supply any other types and externs INLINE, defined before use.
- C89: declarations at the start of a block. No do-while, no inline asm.
- Write source, not registers: operate on real variables, not locals named
  after t6/v0/a1.
- Every symbol, offset and constant you emit must appear in the lexicon above.
  If it is not there, you are inventing it.

Write the complete function now."""


def compressed_prompt(asm: str, summaries: list[str], kb: str = "",
                      hints: str = "", include_asm: bool = True) -> str:
    """Compose from summaries + lexicon, with the stripped asm optional.

    `include_asm=False` is the pure-lookup arm: no linear scan available at
    all. `include_asm=True` keeps the full detail but at half the character
    cost, which is the arm that should win if the load -- not the information
    -- was the problem.
    """
    block = ""
    if include_asm:
        block = ("\nTarget assembly (addresses and encodings stripped):\n"
                 f"```\n{strip_asm(asm)}\n```\n")
    return COMPRESSED_PROMPT.format(summaries="\n\n".join(summaries),
                                    lex=lexicon(asm), asm_block=block,
                                    kb=kb, hints=hints)
