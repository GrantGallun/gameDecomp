"""A model of IDO's uopt register allocator, built from its stated algorithm.

The reference project measured uopt directly with an instrumented build and
wrote down what it does:

    Colors are chosen lowest-index-first, and the pool is
        c1=v0 c2=v1 c3=a0 c4=a1 c5=a2 c6=a3 c7=t0 ... c12=t5, c14-c22=s0-s8
    Webs are colored in descending `save`, ties break on web number, which is
    construction chronology.
    save = totalsave / nocs, and nocs = ((n - 2) >> 2) + 2 for n occurrences.

That is a specification, not a heuristic, which makes "which register does this
value get" a computable question rather than something to discover by
perturbing C and recompiling. The nudge search that motivated this module tried
the latter and moved nothing: 0 of 4 catalogued nudges reduced a 36-fault
register residual.

WHAT IS KNOWN AND WHAT IS ASSUMED -- the distinction matters, because half this
model is quoted and half is guessed:

    KNOWN     the color pool and its order
    KNOWN     nocs = ((n - 2) >> 2) + 2
    KNOWN     ordering is descending save, ties on construction order
    ASSUMED   totalsave. The notes give the quotient but not the numerator.
              Modelled here as occurrence weight with loop nesting counted
              10x per level, which is the classic spill-cost estimate. If the
              validation below fails, this assumption is the first suspect.

VALIDATION BEFORE USE. `rank_agreement` checks the model against IDO's own
output on functions we have already matched: if webs with higher predicted save
do not take lower-indexed registers, the model is wrong and must not be used to
steer anything. That check is the whole point of building it this way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Quoted directly from the measured notes. The index IS the color number, so
# position in this list is the allocation preference.
COLOR_POOL = ["v0", "v1", "a0", "a1", "a2", "a3",
              "t0", "t1", "t2", "t3", "t4", "t5",
              "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]
COLOR_INDEX = {name: i for i, name in enumerate(COLOR_POOL)}

INSTR = re.compile(r"^\s*([a-z][a-z0-9.]*)\s+(.*)$")
REG = re.compile(r"\$(\w+)")
# Numbered registers appear in -S output; map the ones in the pool.
NUMBERED = {"2": "v0", "3": "v1", "4": "a0", "5": "a1", "6": "a2", "7": "a3",
            "8": "t0", "9": "t1", "10": "t2", "11": "t3", "12": "t4",
            "13": "t5", "16": "s0", "17": "s1", "18": "s2", "19": "s3",
            "20": "s4", "21": "s5", "22": "s6", "23": "s7", "24": "s8"}

# Opcodes whose FIRST operand is written rather than read.
WRITES_FIRST = re.compile(
    r"^(l[bhwd]u?|l[wd]c1|move|li|lui|addu?|addiu|subu?|and|andi|or|ori|xor|"
    r"xori|sll|srl|sra|sllv|srlv|srav|slt|slti|sltu|sltiu|mul|mult|multu|"
    r"div|divu|mfhi|mflo|neg|negu|not|nor)$")
BRANCH = re.compile(r"^(b|beq|bne|beqz|bnez|bgez|blez|bgtz|bltz|j|jal|jr)")


def normalize(reg: str) -> str:
    return NUMBERED.get(reg, reg)


@dataclass
class Web:
    """A def-use chain of one value, in construction order."""
    number: int                     # construction chronology; breaks ties
    register: str                   # the color uopt actually gave it
    occurrences: int = 0            # n
    weight: float = 0.0             # totalsave estimate (ASSUMED, not quoted)
    lines: list = field(default_factory=list)
    precolored: bool = False        # ABI-fixed, so not a colouring decision

    @property
    def nocs(self) -> int:
        """nocs = ((n - 2) >> 2) + 2 -- quoted exactly from the measurements."""
        n = self.occurrences
        return ((n - 2) >> 2) + 2 if n >= 2 else 1

    @property
    def save(self) -> float:
        """save = totalsave / nocs. Coarse and NON-monotone in n by design."""
        return self.weight / self.nocs if self.nocs else 0.0


def _loop_depth(lines: list[str]) -> list[int]:
    """Crude nesting depth per line, from backward branches to seen labels."""
    labels: dict[str, int] = {}
    depth = [0] * len(lines)
    cur = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.endswith(":"):
            labels[stripped[:-1]] = i
        m = INSTR.match(line)
        depth[i] = cur
        if m and BRANCH.match(m.group(1)):
            target = m.group(2).split(",")[-1].strip()
            if target in labels:
                cur = max(0, cur)          # a backward branch closes a loop
                for j in range(labels[target], i + 1):
                    depth[j] += 1
    return depth


def webs(asm: str) -> list[Web]:
    """Reconstruct def-use chains from colored assembly, in program order.

    A web is split at each redefinition of its register, which is the standard
    definition and the best available from post-coloring output: uopt's own
    pre-coloring structure is not visible in what `cc -S` emits.
    """
    lines = [l for l in asm.splitlines()
             if l.strip() and not l.strip().startswith((".", "#"))]
    depth = _loop_depth(lines)
    open_web: dict[str, Web] = {}
    out: list[Web] = []
    counter = 0

    for i, line in enumerate(lines):
        m = INSTR.match(line)
        if not m:
            continue
        opcode, operands = m.group(1), m.group(2)
        regs = [normalize(r) for r in REG.findall(operands)]
        if not regs:
            continue
        weight = 10.0 ** depth[i]

        written = regs[0] if WRITES_FIRST.match(opcode) else None
        for r in regs:
            if r not in COLOR_INDEX:
                continue                    # sp, ra, zero, at: not colored
            if r == written and r in open_web:
                out.append(open_web.pop(r))  # redefinition closes the old web
            w = open_web.get(r)
            if w is None:
                counter += 1
                w = open_web[r] = Web(number=counter, register=r)
            w.occurrences += 1
            w.weight += weight
            w.lines.append(i)

    out.extend(open_web.values())
    out.sort(key=lambda w: w.number)
    return out


def allocation_order(ws: list[Web]) -> list[Web]:
    """Descending save, ties broken on construction order -- as measured."""
    return sorted(ws, key=lambda w: (-w.save, w.number))


ARG_REGS = {"a0", "a1", "a2", "a3"}
RET_REGS = {"v0", "v1"}


def mark_precolored(ws: list[Web], asm: str) -> None:
    """Flag webs whose register the ABI fixes, not uopt's priority scheme.

    This was the model's largest error. On isRacePlayerRespawnSurfaceValid the
    ranking was 0% concordant across 14 pairs -- a perfect inversion, which is
    a sign error rather than noise. The cause: the top-ranked web held `a3`,
    and a3 is index 5 in the colour pool, so every comparison against it was
    wrong.

    a3 was not a colouring decision. It was the fourth argument to a call. The
    pool does include a0-a3 and v0/v1, so uopt can colour into them, but a
    value feeding a `jal` or taken from its return is PRECOLOURED by the
    calling convention and never competed for that register on `save`.

    Including those webs measures the ABI and calls it allocation.
    """
    lines = [l for l in asm.splitlines()
             if l.strip() and not l.strip().startswith((".", "#"))]
    call_lines = {i for i, l in enumerate(lines)
                  if (m := INSTR.match(l)) and m.group(1) in ("jal", "jalr")}
    if not call_lines:
        return
    for w in ws:
        if w.register not in ARG_REGS | RET_REGS:
            continue
        # argument setup sits just before a call; a return value just after
        for ln in w.lines:
            near = any(0 <= c - ln <= 4 for c in call_lines) if \
                w.register in ARG_REGS else \
                any(0 <= ln - c <= 3 for c in call_lines)
            if near:
                w.precolored = True
                break


def rank_agreement(ws: list[Web]) -> tuple[int, int]:
    """(concordant, comparable) pairs between predicted rank and real color.

    The law under test: a web the model ranks earlier should hold a
    lower-indexed register. Only pairs with DIFFERENT save and DIFFERENT
    colors are comparable -- equal save says nothing about order, and two webs
    sharing a color are simply non-interfering.
    """
    ranked = [w for w in allocation_order(ws) if not w.precolored]
    concordant = comparable = 0
    for i, a in enumerate(ranked):
        for b in ranked[i + 1:]:
            if a.save == b.save or a.register == b.register:
                continue
            comparable += 1
            if COLOR_INDEX[a.register] < COLOR_INDEX[b.register]:
                concordant += 1
    return concordant, comparable
