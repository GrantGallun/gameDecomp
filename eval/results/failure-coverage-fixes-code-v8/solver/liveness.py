"""Backward liveness over solver.cfg, to measure REGISTER PRESSURE at calls.

WHY THIS AND NOT ANOTHER ALLOCATOR MODEL
    Two attempts to reach the allocation residual have been refuted. Statement
    permutation moves colouring ties and never touched the anchor; the `save`
    model scored 50-58% against a 50% chance baseline because webs rebuilt from
    POST-allocation assembly are not uopt's webs.

    Both tried to predict which web should get which colour. This measures
    something structural instead: HOW MANY values are simultaneously live. If
    the target keeps N values alive across a call and the candidate keeps N+1,
    the candidate is holding a value the target recomputes, and that is an
    extra web -- localised to a specific program point, with an obvious source
    remedy (do not hold it; read it again after the call). No priority model is
    involved, so the reason the `save` work failed does not apply here.

    IDO emits its own answer for the candidate as `.livereg` masks in `cc -S`
    output, but target.s carries none -- the ROM disassembly has no such
    directive -- so the target side must be computed. Both sides are therefore
    computed here, which keeps the comparison apples-to-apples rather than
    mixing a compiler's annotation with our own analysis.

STILL A DIAGNOSTIC. It reports where pressure differs; it proposes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from solver import cfg, uopt

# Registers the ABI lets a call destroy. A caller-saved register live ACROSS a
# call must be spilled, so these are what pressure at a call site is about.
CALLER_SAVED = frozenset(
    ["v0", "v1", "a0", "a1", "a2", "a3"] + [f"t{i}" for i in range(10)])
# uopt.COLOR_POOL stops at t5 because t6-t9 are not in ITS allocation pool, but
# they are ordinary caller-saved registers that carry values across calls.
# Filtering on the colour pool silently dropped every t6-t9 value and
# under-reported pressure -- a smoke test with `t6` live across a call reported
# one live register instead of two.
ALLOCATABLE = frozenset(uopt.COLOR_POOL) | CALLER_SAVED
CALL_OPS = frozenset({"jal", "jalr"})


def _regs(insn: cfg.Instruction) -> list[str]:
    out = []
    for op in insn.operands:
        for a, b in uopt.REG.findall(op):
            r = uopt.normalize(a or b)
            if r in ALLOCATABLE:
                out.append(r)
    return out


def _def_use(insn: cfg.Instruction) -> tuple[set[str], set[str]]:
    """(defined, used) allocatable registers for one instruction.

    A store's first operand is READ, not written, which is the usual place a
    naive def/use split goes wrong and would make every stored value look dead.
    """
    regs = _regs(insn)
    if not regs:
        return set(), set()
    writes_first = bool(uopt.WRITES_FIRST.match(insn.opcode))
    if writes_first:
        return {regs[0]}, set(regs[1:])
    return set(), set(regs)


@dataclass
class Pressure:
    """Live caller-saved registers immediately after each call."""
    call_index: int
    live: frozenset


def analyse(asm: str) -> tuple[dict[int, frozenset], list[Pressure]]:
    """(live-out per instruction index, pressure at each call site)."""
    graph = cfg.build(asm)
    if graph.entry is None or not graph.blocks:
        return {}, []

    live_in: dict[int, set[str]] = {b: set() for b in graph.blocks}
    live_out: dict[int, set[str]] = {b: set() for b in graph.blocks}

    changed = True
    guard = 0
    while changed and guard < 200:          # converges; guard against a cycle
        changed = False
        guard += 1
        for bid in sorted(graph.blocks, reverse=True):
            block = graph.blocks[bid]
            out: set[str] = set()
            for succ in block.successors:
                out |= live_in.get(succ, set())
            if block.unknown_successor:
                # An unresolved jump could reach anywhere; assuming nothing is
                # live would silently under-report pressure, so be pessimistic.
                out |= set(CALLER_SAVED)
            cur = set(out)
            for insn in reversed(block.instructions):
                d, u = _def_use(insn)
                cur = (cur - d) | u
            if cur != live_in[bid] or out != live_out[bid]:
                live_in[bid], live_out[bid] = cur, out
                changed = True

    per_insn: dict[int, frozenset] = {}
    calls: list[Pressure] = []
    for bid, block in graph.blocks.items():
        cur = set(live_out[bid])
        for insn in reversed(block.instructions):
            per_insn[insn.index] = frozenset(cur)
            if insn.opcode in CALL_OPS:
                calls.append(Pressure(insn.index,
                                      frozenset(cur & CALLER_SAVED)))
            d, u = _def_use(insn)
            cur = (cur - d) | u
    calls.sort(key=lambda p: p.call_index)
    return per_insn, calls


def pressure_profile(asm: str) -> list[int]:
    """Number of caller-saved registers live across each call, in order."""
    _per_insn, calls = analyse(asm)
    return [len(p.live) for p in calls]
