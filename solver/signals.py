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
# The `$` is OPTIONAL. objdump output in this project writes bare names --
# `lbu v1,0x24(a0)` -- so a pattern requiring `$` matched nothing, every
# instruction compared equal on registers, and non-memory register differences
# were silently counted as `immediate`. Named explicitly rather than \w+ so a
# label or symbol is never mistaken for a register.
# Both spellings occur: objdump writes names, `cc -S` writes numbers.
REGNAME = re.compile(
    r"\$\d+|\$?\b(?:zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)\b")


def _regs(instr: str) -> tuple[str, ...]:
    """Register operands only, so a differing CONSTANT is not read as one."""
    return tuple(m.group(0).lstrip("$") for m in REGNAME.finditer(instr))


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

    immediate: int = 0          # same opcode, different CONSTANT operand

    @property
    def repairable(self) -> int:
        """Faults an IMPLEMENTED pass can actually fix.

        This used to include reloc and regalloc, for which nothing is
        implemented, so "our tools own the whole residual" was reported about
        residuals no tool could touch. Availability of a repair and membership
        of a fault class are different questions and are now kept apart:
        `repairable` means we have the code, `classified` means we know the
        kind.
        """
        return self.layout                      # repad / reorder / width

    @property
    def no_repair_implemented(self) -> int:
        """Classified, but nothing in the codebase repairs it yet."""
        return self.reloc + self.regalloc + self.immediate

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
    """Pair each expected instruction with the produced one, by ALIGNMENT.

    This used to be zip(minus, plus) -- positional pairing, not alignment -- so
    a single unpaired instruction shifted every later pair and every
    classification after it described two instructions that have nothing to do
    with each other. The same defect was found and fixed in diffrepair and left
    here, which means every triage number reported from this module was built
    on drifted pairs.

    diffrepair.aligned_pairs_loose normalises the offset and the memory-op
    width before matching, so instructions differing only in those land in an
    equal block and can be compared. Anything it does not pair is a genuine
    structural difference and is counted from the line totals below.
    """
    minus = [l[1:].strip() for l in diff.splitlines()
             if l.startswith("-") and not l.startswith("---")]
    plus = [l[1:].strip() for l in diff.splitlines()
            if l.startswith("+") and not l.startswith("+++")]

    # diffrepair's alignment is deliberately CONSERVATIVE -- it pairs only what
    # is provably comparable, because it drives rewrites and a wrong pair
    # corrupts a struct. Classification wants the opposite: pair anything
    # plausibly corresponding and then judge it, so that a same-opcode register
    # difference is reported as a register fault rather than falling through to
    # structural. Aligning on the OPCODE alone gives that.
    import difflib

    from solver import diffrepair          # local: avoids an import cycle

    target, cand = diffrepair._streams(diff)
    if not target or not cand:
        return list(zip(minus, plus)), len(minus), len(plus)

    def op(instr: str) -> str:
        m = OPCODE.match(instr)
        return m.group(1) if m else instr

    pairs: list[tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(None, [op(x) for x in target],
                                      [op(x) for x in cand], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for t, c in zip(target[i1:i2], cand[j1:j2]):
                if t != c:
                    pairs.append((t, c))
        elif tag == "replace":
            # different opcodes facing each other: still a corresponding pair,
            # and classify() will call it structural
            for t, c in zip(target[i1:i2], cand[j1:j2]):
                pairs.append((t, c))
    return pairs, len(minus), len(plus)


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
        elif _regs(a) == _regs(b):
            # Same opcode and same registers, so what differs is a CONSTANT --
            # `slti $2,$3,4` against `slti $2,$3,5` is a wrong immediate, not a
            # wrong register. Counting it as register allocation inflated that
            # class and pointed a whole line of work at the wrong fault.
            s.immediate += 1
        else:
            s.regalloc += 1                 # same op, genuinely different regs

    # Lines the alignment could NOT pair are structural by construction: it
    # pairs anything differing only in offset or access width, so whatever is
    # left over differs in opcode, in branch target, or has no counterpart at
    # all. Counting only abs(n_minus - n_plus) here lost every differing-opcode
    # pair, because those arrive as one minus AND one plus and cancel.
    paired = len(pairs)
    unpaired_expected = max(0, n_minus - paired)
    unpaired_produced = max(0, n_plus - paired)
    s.structural += max(unpaired_expected, unpaired_produced)
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
