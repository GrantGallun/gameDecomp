"""Structural signals for a compiled candidate, beyond the scalar byte score.

The pipeline anchors on the highest-scoring candidate. That is a single scalar
over a residual with very different kinds of error in it, and the kinds are not
equally recoverable:

    a WRONG OFFSET is repairable      -- repad fixes padding deterministically
    a WRONG BRANCH SHAPE is not       -- nothing we have inverts control flow

So a candidate at 90% whose entire residual is offsets can be finished, while
one at 96% whose residual is branches cannot. Ranking them by score puts the
unfixable one first, and every repair pass then runs against the wrong anchor.
This module computes the signals needed to tell them apart.

Derived from the oracle's own instruction diff, so it costs one compile that
the pipeline already pays for and introduces no new disassembly tooling.

The signals are DESCRIPTIVE. Nothing here decides anything on its own -- the
oracle remains the only authority on whether a candidate matches, and a
candidate that looks structurally perfect by these numbers and does not verify
is simply wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MEM = re.compile(
    r"^([a-z][a-z0-9.]*)\s+(\$?\w+),\s*(-?(?:0x)?[0-9a-f]+)\((\$?\w+)\)")
BRANCH = {"b", "beq", "bne", "beqz", "bnez", "bgez", "blez", "bgtz", "bltz",
          "bgezal", "bltzal", "j", "jr", "jal", "jalr", "bc1t", "bc1f"}
RELOC = re.compile(r"%(?:hi|lo)\(([^)]*)\)")
OPCODE = re.compile(r"^([a-z][a-z0-9.]*)")


@dataclass
class Signals:
    """What is wrong with a candidate, by kind rather than by amount."""
    score: float = 0.0
    exact: bool = False
    compiled: bool = False
    diff_lines: int = 0
    instr_delta: int = 0        # |missing| - |extra|, signed, 0 is good
    layout: int = 0             # offset+width, kept for the Pareto axes
    offset: int = 0             # wrong offset -- repad CAN fix this
    width: int = 0              # wrong access width -- repad CANNOT: it
                                # resizes padding, it cannot retype a field
    structural: int = 0         # branch shape, opcode, missing instructions
    reloc: int = 0              # %hi/%lo symbol faults -- symbol repair
    regalloc: int = 0           # same op, same offset, different register

    @property
    def repairable(self) -> int:
        return self.layout + self.reloc + self.regalloc

    @property
    def unrepairable(self) -> int:
        return self.structural

    def dominates(self, other: "Signals") -> bool:
        """True when this candidate is at least as good on EVERY axis.

        Pareto dominance, so a candidate survives if it is best at anything --
        which is the whole point: the lowest structural count may sit on a
        lower-scoring candidate than the highest byte score.
        """
        axes = [
            (self.structural, other.structural),
            (abs(self.instr_delta), abs(other.instr_delta)),
            (self.layout, other.layout),
            (self.reloc, other.reloc),
            (-self.score, -other.score),
        ]
        return (all(a <= b for a, b in axes)
                and any(a < b for a, b in axes))


def _pairs(diff: str) -> tuple[list[tuple[str, str]], int, int]:
    minus = [l[1:].strip() for l in diff.splitlines()
             if l.startswith("-") and not l.startswith("---")]
    plus = [l[1:].strip() for l in diff.splitlines()
            if l.startswith("+") and not l.startswith("+++")]
    return list(zip(minus, plus)), len(minus), len(plus)


def analyse(diff: str, score: float = 0.0, exact: bool = False,
            compiled: bool = True) -> Signals:
    """Classify an instruction diff into kinds of error."""
    s = Signals(score=score, exact=exact, compiled=compiled)
    if not compiled:
        return s
    pairs, n_minus, n_plus = _pairs(diff or "")
    s.diff_lines = n_minus + n_plus
    s.instr_delta = n_minus - n_plus

    for a, b in pairs:
        ma, mb = MEM.match(a), MEM.match(b)
        if ma and mb:
            if ma.group(1) != mb.group(1):
                s.layout += 1
                s.width += 1                # lw vs lh vs lb: the field's TYPE
            elif ma.group(3) != mb.group(3):
                s.layout += 1
                s.offset += 1               # where the field sits
            else:
                s.regalloc += 1             # same access, different register
            continue
        if RELOC.search(a) or RELOC.search(b):
            if RELOC.findall(a) != RELOC.findall(b):
                s.reloc += 1
                continue
        oa = OPCODE.match(a).group(1) if OPCODE.match(a) else ""
        ob = OPCODE.match(b).group(1) if OPCODE.match(b) else ""
        if oa != ob:
            s.structural += 1
        elif oa in BRANCH:
            s.structural += 1               # same branch op, different target
        else:
            s.regalloc += 1                 # same op, different operands

    # instructions with no counterpart are missing or extra code, which is
    # structural by definition -- register allocation cannot change how many
    # instructions exist.
    s.structural += abs(n_minus - n_plus)
    return s


def pareto(items: list) -> list:
    """The non-dominated subset of (key, Signals) pairs, best-first."""
    keep = []
    for i, (k, sig) in enumerate(items):
        if any(other.dominates(sig)
               for j, (_k2, other) in enumerate(items) if i != j):
            continue
        keep.append((k, sig))
    keep.sort(key=lambda ks: (ks[1].structural, abs(ks[1].instr_delta),
                              -ks[1].score))
    return keep
