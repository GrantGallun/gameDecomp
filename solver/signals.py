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
# The FPU file is spelled `$f0`..`$f31` and was missing entirely, so the very
# bug described above survived in the coprocessor half: `lwc1 $f4` against
# `lwc1 $f0` compared EQUAL on registers and fell through to `immediate`. A
# float allocator choice was reported as a wrong CONSTANT, which sent literal
# repair after functions whose residual was register allocation.
# `$fp` is the frame pointer and stays with the integer names; `\$f\d+`
# requires a digit after the `f`, so the two alternatives cannot collide.
REGNAME = re.compile(
    r"\$f\d+"
    r"|\$\d+|\$?\b(?:zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)\b")


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
    ordering: int = 0           # the instruction exists VERBATIM on both
                                # sides -- displaced, not wrong

    immediate: int = 0          # same opcode, different CONSTANT operand
    branch_shift: int = 0       # same branch to the ALIGNED counterpart of its
                                # target: the address moved because instructions
                                # were inserted elsewhere. Not a fault, and not an
                                # axis -- the insertion is already counted.

    @property
    def repairable(self) -> int:
        """Faults with a generally applicable implemented pass.

        This used to omit immediate repair and elsewhere treated every
        relocation and register fault as owned. Availability of a broad repair,
        a conditional repair, and mere classification are now kept apart.

        `ordering` belongs here: rewrites.statement_order_rewrites owns it, and
        it is what closed updateRacePlayerMode53AerialTrick. Before this kind
        existed a displaced instruction was billed to `offset`, so triage sent
        diffrepair after a struct that was already correct.
        """
        return (self.layout + self.immediate    # layout/retype + literal repair
                + self.ordering)                # statement_order_rewrites

    @property
    def conditional_repair(self) -> int:
        """Faults with a bounded pass that applies only to some residuals.

        Relocation addends and named data-symbol substitutions are repairable;
        jump-table section/layout relocations generally are not. The coarse
        classifier cannot tell those cases apart, so it must not claim either
        that all relocation faults are owned or that none are.
        """
        return self.reloc

    @property
    def no_repair_implemented(self) -> int:
        """Classified, but nothing in the codebase repairs it yet."""
        return self.regalloc

    @property
    def unrepairable(self) -> int:
        """Coarse structural faults; a few narrow signatures have generators."""
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
            (self.regalloc, other.regalloc),
            (self.ordering, other.ordering),
            (self.immediate, other.immediate),
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


HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
LOCAL_TARGET = BRANCH - {"jr", "jal", "jalr"}


def line_map(diff: str) -> dict[int, int] | None:
    """Target instruction index -> aligned candidate index (index = byte offset / 4).

    Branch operands are function-relative byte offsets, so one instruction
    inserted near the top moves every later target: `b 41c` against `b 490` is
    the same jump when 0x41c and 0x490 are corresponding instructions. Counted
    as structural, such shifts appeared in 387 of the 729 unsolved functions the
    structural axis flagged, on top of the insertion that caused them
    (eval/results/structural-residual-20260929).

    Built from the hunk headers: shown lines are aligned by opcode like _pairs,
    skipped regions are identical on both sides and map by the hunk's delta.
    Returns None for a diff without hunk headers, so callers keep the old,
    conservative reading instead of guessing.
    """
    t_rows, c_rows, starts = [], [], []
    t_line = c_line = None
    for line in diff.splitlines():
        m = HUNK.match(line)
        if m:
            t_line, c_line = int(m.group(1)) - 1, int(m.group(2)) - 1
            starts.append((t_line, c_line))
            continue
        if t_line is None or not line or line.startswith(("---", "+++")):
            continue
        body = line[1:].strip()
        if line[0] in " -":
            if body:
                t_rows.append((t_line, body))
            t_line += 1
        if line[0] in " +":
            if body:
                c_rows.append((c_line, body))
            c_line += 1
    if not starts:
        return None
    import difflib

    def op(instr: str) -> str:
        m = OPCODE.match(instr)
        return m.group(1) if m else instr

    shown: dict[int, int] = {}
    matcher = difflib.SequenceMatcher(None, [op(b) for _, b in t_rows],
                                      [op(b) for _, b in c_rows], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "replace"):
            for (ti, _), (ci, _) in zip(t_rows[i1:i2], c_rows[j1:j2]):
                shown[ti] = ci
    final = (t_line, c_line)
    shown_t = {i for i, _ in t_rows}

    class _Map(dict):
        def get(self, i, default=None):
            if i in self:
                return self[i]
            if i in shown_t:            # a shown line with no counterpart
                return default
            for ts, cs in starts:       # skipped region before this hunk
                if i < ts:
                    return i + cs - ts
            return i + final[1] - final[0]
    return _Map(shown)


def _branch_target(instr: str) -> int | None:
    operand = instr.rsplit(",", 1)[-1].split()[-1]
    try:
        return int(operand, 16)
    except ValueError:
        return None


def label_equivalent(a: str, b: str, lmap) -> bool:
    """Both branches jump to corresponding instructions."""
    if lmap is None or OPCODE.match(a).group(1) not in LOCAL_TARGET:
        return False
    ta, tb = _branch_target(a), _branch_target(b)
    if ta is None or tb is None or ta % 4 or tb % 4:
        return False
    return lmap.get(ta // 4) == tb // 4


ALLOCATABLE = re.compile(r"\b(?:v[01]|a[0-3]|t[0-9]|s[0-8])\b")


def distances(diff: str) -> tuple[int, int]:
    """(instruction distance, register distance) between the target and candidate streams.

    Instruction distance: unmatched instructions on either side once allocatable register names are
    blanked, i.e. what no register assignment could fix. Register distance: aligned instructions equal
    once blanked but different as written, i.e. what register search owns. Measured motivation
    (eval/results/site-edits-20260929): the similarity score charged a correct stride fix that shifted
    temporaries as a regression (99.811 -> 98.302); ranking instructions first closed it.
    """
    import difflib
    from solver import diffrepair          # local: avoids an import cycle
    target, cand = diffrepair._streams(diff)
    bt = [ALLOCATABLE.sub("R", x) for x in target]
    bc = [ALLOCATABLE.sub("R", x) for x in cand]
    matched = register = 0
    for a, b, n in difflib.SequenceMatcher(None, bt, bc, autojunk=False).get_matching_blocks():
        matched += n
        register += sum(1 for k in range(n) if target[a + k] != cand[b + k])
    return len(bt) + len(bc) - 2 * matched, register


def analyse(diff: str, score: float = 0.0, exact: bool = False,
            compiled: bool = True) -> Signals:
    """Classify an instruction diff into kinds of error."""
    s = Signals(score=score, exact=exact, compiled=compiled)
    if not compiled:
        return s
    pairs, n_minus, n_plus = _pairs(diff or "")
    s.diff_lines = n_minus + n_plus
    s.instr_delta = n_minus - n_plus

    # An instruction appearing VERBATIM on both sides of the residual was not
    # miscompiled -- it was emitted somewhere else. Opcode-only alignment pairs
    # a moved instruction against whichever same-opcode neighbour now occupies
    # its slot, so a pure scheduling difference arrived here dressed as a
    # struct fault: updateRacePlayerMode16AerialTrick reported three wrong
    # offsets for three `lw`s that were correct and merely rotated, and triage
    # sent diffrepair after a layout that already agreed with the binary.
    # Judged over the WHOLE residual, not pairwise, because a rotation is a
    # cycle (A->B, B->C, C->A) in which no individual pair looks like a swap.
    on_both = {t for t, _ in pairs} & {c for _, c in pairs}
    lmap = line_map(diff or "")

    for a, b in pairs:
        if a in on_both and b in on_both:
            s.ordering += 1
            continue
        ma, mb = MEM.match(a), MEM.match(b)
        if ma and mb:
            if ma.group(1) != mb.group(1):
                s.layout += 1
                s.width += 1                # lw vs lh vs lb: the field's TYPE
            elif ma.group(3) != mb.group(3):
                target_base = ma.group(4).lstrip("$")
                candidate_base = mb.group(4).lstrip("$")
                if target_base == candidate_base == "sp":
                    # An sp-relative offset is an allocator-selected spill
                    # home, not a struct field. Calling it layout sent
                    # diffrepair after source structs for a register problem.
                    s.regalloc += 1
                else:
                    s.layout += 1
                    s.offset += 1           # where the field sits
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
        elif oa in BRANCH and label_equivalent(a, b, lmap):
            # same jump to the corresponding instruction: at most a register
            # difference in the condition remains
            if _regs(a) == _regs(b):
                s.branch_shift += 1
            else:
                s.regalloc += 1
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
