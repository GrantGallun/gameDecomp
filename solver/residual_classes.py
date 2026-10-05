"""Residual counts per fault class, ordered so a search can fix one class at a time.

Measured motivation (eval/results/width-edits-20260929, branch-routing-20260929): each class owner acts on
its own class -- retyping reduced extension faults in 106 of 299 functions, branch-shape repairs improved 52
of 75 -- yet neither finished a function. The classes co-occur (91 of 842 unsolved functions have a single
residual axis), and the search judged every child by total instruction distance, so in 56 of the 106 the
class-correct child was discarded: fixing a width adds or removes `sll/sra` and shifts what follows.

`key` orders a child by the earliest class that still differs, upstream first: a change of control flow
moves everything after it, a width change adds or removes instructions, while an offset, a constant or a
register is local. A child that reduces an earlier class is kept even when later classes grow, because the
later classes are then repaired in their turn; an earlier class can never regress along a kept path.
The oracle still decides: only `workspace.repair_complete` ends a search.
"""
from __future__ import annotations

import collections
import re

from solver import diffrepair, signals

# frame follows width: it is set by the declared locals (catalog ido53-frame-layout), and a frame change
# moves every stack-relative offset and constant downstream.
CLASSES = ("control_flow", "width", "frame", "layout", "operand", "instructions", "registers")

_COND = {"beq", "bne", "beqz", "bnez", "bgez", "blez", "bgtz", "bltz", "bc1t", "bc1f",
         "beql", "bnel", "beqzl", "bnezl", "bgezl", "bltzl", "blezl", "bgtzl"}
EXTENSION = re.compile(r"^(?:(?:sll|sra|srl)\s+\w+,\w+,(?:0x10|16|0x18|24)|andi\s+\w+,\w+,(?:0xff|0xffff))$")


def _flow(stream: list[str]) -> collections.Counter:
    counts = collections.Counter()
    for instr in stream:
        op = instr.split()[0] if instr.split() else ""
        if op in _COND:
            counts[op] += 1
        elif op in ("b", "j"):
            counts["uncond"] += 1
        elif op == "jr":
            counts["return" if instr.rstrip().endswith("ra") else "jumptable"] += 1
    return counts


def control_flow(target: list[str], candidate: list[str]) -> int:
    """L1 distance between the branch/jump inventories: count or kind of control transfers differs."""
    a, b = _flow(target), _flow(candidate)
    return sum(abs(a[k] - b[k]) for k in set(a) | set(b))


def _extension_kinds(stream: list[str]) -> collections.Counter:
    """Sign/zero extensions by kind, registers blanked.

    An extension is `andi r,x,0xff|0xffff`, or `sll r,x,N` whose result is shifted back by `sra`/`srl` N
    within the next few instructions (N = 16 or 24). A lone `sll 16|24` is not one: byte packing
    `(b0 << 24) | (b1 << 16) | ...` (__osContRamWrite, __osPfsGetInitData) was billed as width until
    2026-09-29, and so was a lone `sra`/`srl` extracting a high byte or half.
    """
    kinds = collections.Counter()
    for i, instr in enumerate(stream):
        parts = instr.replace(",", " ").split()
        if len(parts) < 4:
            continue
        op, dst, amount = parts[0], parts[1], parts[-1]
        if op == "andi" and amount in ("0xff", "0xffff"):
            kinds[("andi", amount)] += 1
        elif op == "sll" and amount in ("0x10", "16", "0x18", "24"):
            for later in stream[i + 1:i + 4]:
                q = later.replace(",", " ").split()
                if len(q) >= 4 and q[0] in ("sra", "srl") and q[2] == dst and q[-1] == amount:
                    kinds[(q[0], amount)] += 1          # sra: sign extension, srl: zero extension
                    break
    return kinds


def width(target: list[str], candidate: list[str]) -> int:
    """Sign/zero extensions one side performs more often than the other, by kind.

    The first version counted every extension inside a differing diff region. A renamed or moved
    extension (`andi t1,v1,0xffff` against `andi t0,v1,0xffff`, Fwave; __osSumcalc) was then a width
    fault, and in the focus trial 143 of 207 searches spent their budget on it: the class key put a
    register difference first in line. Registers belong to the last class.
    """
    a, b = _extension_kinds(target), _extension_kinds(candidate)
    return sum(abs(a[k] - b[k]) for k in set(a) | set(b))


def frame(target: list[str], candidate: list[str]) -> int:
    """Frame-size difference in 8-byte units (IDO aligns frames to 8); 0 when either side has no frame."""
    def size(stream):
        for instr in stream:
            m = re.match(r"^addiu\s+sp,sp,-(0x[0-9a-f]+|\d+)$", instr)
            if m:
                return int(m.group(1), 0)
        return None
    a, b = size(target), size(candidate)
    if a is None or b is None or a == b:
        return 0
    return max(1, abs(a - b) // 8)


def _operand(diff: str, reloc: int) -> int:
    """Wrong constants, excluding stack-relative ones (`addiu t0,sp,0x3c`): those move with the frame.

    With them counted, merging a copy-back temporary in osMotorStart (92.1, registers 22 -> 6) was
    rejected for one more "operand" fault that was only a frame offset (composed-edits amendment 8).
    """
    pairs, _n, _m = signals._pairs(diff or "")
    on_both = {t for t, _ in pairs} & {c for _, c in pairs}
    n = 0
    for a, b in pairs:
        if (a in on_both and b in on_both) or (signals.MEM.match(a) and signals.MEM.match(b)):
            continue
        if signals.RELOC.search(a) or signals.RELOC.search(b):
            continue
        oa, ob = signals.OPCODE.match(a), signals.OPCODE.match(b)
        if not oa or not ob or oa.group(1) != ob.group(1) or oa.group(1) in signals.BRANCH:
            continue
        regs = signals._regs(a)
        if regs == signals._regs(b) and "sp" not in regs:
            n += 1
    return n + reloc


def counts(diff: str) -> dict[str, int]:
    """Per-class residual for one compiled candidate's diff against the target."""
    target, candidate = diffrepair._streams(diff or "")
    s = signals.analyse(diff or "", 0.0, False, True)
    instructions, registers = signals.distances(diff or "")
    return {"control_flow": control_flow(target, candidate), "width": width(target, candidate),
            "frame": frame(target, candidate), "layout": s.layout, "operand": _operand(diff, s.reloc),
            "instructions": instructions, "registers": registers}


def key(attempt) -> tuple:
    """Lexicographic class order; uncompiled sorts last. Drop-in for site_edits.gradient."""
    if not attempt.compiled:
        return (1,) + (10**9,) * len(CLASSES)
    if getattr(attempt, "exact", False):
        return (0,) * (len(CLASSES) + 1)
    c = counts(attempt.diff or "")
    return (0,) + tuple(c[k] for k in CLASSES)


_TRANSFER = _COND | {"b", "j", "jr"}


def focus(diff: str):
    """Instruction predicate for the first class still wrong, for site localisation; None past width.

    Later classes (layout, operand, registers) are local to their own instruction already, so the plain
    heaviest-lines ranking finds them; the first two are exactly the ones whose knock-on buries them.
    """
    target, candidate = diffrepair._streams(diff or "")
    if control_flow(target, candidate):
        return lambda instr: (instr.split() or [""])[0] in _TRANSFER
    if width(target, candidate):
        return lambda instr: bool(EXTENSION.match(instr))
    return None
