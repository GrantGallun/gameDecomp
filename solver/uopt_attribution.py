"""Which uopt live range holds each register operand of a compiled function.

The chain, each link measured on the SBK1 census (2026-09-14):

    final assembly block  ->  ugen u-code block  ->  uopt flow-graph node  ->  (node, colour) -> live range

* (node, colour) names one range. uopt's live ranges are sets of blocks and ranges sharing a
  block interfere, so no colour is held twice in a node: 0 exceptions in 89,403 pairs.
* Emission order is NOT assembly order: as1 forwards stores into loads and substitutes
  registers. Control flow survives as1, so the mapping is at segment granularity: both sides are
  cut at mandatory terminators (call, conditional branch, return, jump) and aligned 1:1.
  A ugen jump may be elided (its blocks fold into the next segment), and a ugen jump to the exit
  may become a duplicated epilogue (`ret`). Anything else declines.
* Blocks come from `solver.uopt_calls`, whose flow-graph match already declines on inconsistency.

On the census this attributed 1,556 of 1,968 procedures (87% of those whose flow graph maps).
Remaining declines are switch statements (`xjp` expanded into compare chains) and folded branches.
In a segment holding several nodes, an operand is attributed only when exactly one range among
them holds its colour; otherwise it is left unattributed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from solver import cfg, uopt_calls, uopt_trace

NUMERIC_MARKER = "# MIPS_DIFF_NUMERIC_BRANCH_BASE 0x0"
REGISTER_NAMES = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
                  "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "t8", "t9", "k0", "k1", "gp", "sp", "s8", "ra"]
REGISTER_NUMBER = {name: number for number, name in enumerate(REGISTER_NAMES)} | {"fp": 30}
CALL_OPS = {"jal", "jalr", "bal"}
_OPERAND_REGISTER = re.compile(r"^(?:.*\()?\$?([a-z]\w*)\)?$")


def colour_register(colour: int) -> int | None:
    """Register number for an integer colour: 1-13 are v0..t6, 14-21 s0..s7, 22 s8."""
    if 1 <= colour <= 13:
        return colour + 1
    if 14 <= colour <= 21:
        return colour + 2
    return 30 if colour == 22 else None


REGISTER_COLOUR = {colour_register(c): c for c in range(1, 23)}


@dataclass
class Attribution:
    function: str
    instructions: list[cfg.Instruction]
    nodes: dict[int, list[int]]                       # instruction index -> uopt nodes of its segment
    operands: dict[int, list[tuple[int, str, int | None]]] = field(default_factory=dict)   # (position, reg, lr)
    elided_jumps: int = 0


class Declined(Exception):
    pass


def strip_padding(asm: str) -> str:
    """Drop alignment nops after the final `jr ra` delay slot."""
    lines = [line for line in asm.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    returns = [i for i, line in enumerate(lines) if re.match(r"^jr\s+\$?ra$", line.strip())]
    if returns and all(line.strip() == "nop" for line in lines[returns[-1] + 2:]):
        lines = lines[:returns[-1] + 2]
    return "\n".join(lines)


def _asm_segments(asm: str) -> tuple[tuple[cfg.Instruction, ...], list[tuple[str, int, int]]]:
    graph = cfg.build(asm + "\n" + NUMERIC_MARKER + "\n")
    insns = graph.instructions
    leaders = {block.start for block in graph.blocks.values()}
    leaders.update(i.index + 2 for i in insns if i.opcode in CALL_OPS and i.index + 2 < len(insns))
    starts = sorted(leaders)
    segments, start = [], 0
    for k, s in enumerate(starts):
        e = starts[k + 1] - 1 if k + 1 < len(starts) else len(insns) - 1
        transfers = [m for m in insns[s:e + 1] if cfg.is_control_transfer(m.opcode) or m.opcode in CALL_OPS]
        if not transfers:
            continue
        op = transfers[-1].opcode
        kind = ("call" if op in CALL_OPS else "ret" if op == "jr" else
                "cond" if cfg.is_conditional_branch(op) else "jump")
        segments.append((kind, start, e))
        start = e + 1
    if start < len(insns):
        segments.append(("fall", start, len(insns) - 1))
    return insns, segments


def _ugen_segments(blocks: list[uopt_calls.Block], node_of_block: dict[int, int]) -> list[tuple[str, list[int]]]:
    segments, nodes = [], []
    for index, block in enumerate(blocks):
        if index in node_of_block:
            nodes.append(node_of_block[index])
        last = block.ops[-1][0]
        kind = ("call" if block.ends_call else "cond" if last in uopt_calls.CONDITIONAL else
                "jump" if last == "ujp" else "ret" if last == "end" else None)
        if kind:
            segments.append((kind, nodes))
            nodes = []
    if nodes:
        segments.append(("fall", nodes))
    return segments


def align(ugen: list[str], asm: list[str]) -> list[tuple[int, int, str]] | None:
    """Pair segment kinds; a ugen 'jump' may be skipped or become an asm 'ret'. Fewest skips wins.

    A `jump -> cond` pairing was TRIED and REVERTED 2026-09-17. The reasoning was that
    drawCharacterSelectCoursePreviewPanel8 shows ugen {call x8, cond x2, jump x3, ret x1} against asm
    {call x8, cond x3, jump x0, ret x1}, so skipping all three jumps leaves 11 segments against the
    asm's 12 and exactly one jump would have to pair. Adding the pairing did NOT fix it: the sequences
    diverge earlier, at position 1, where ugen has `call` and asm has `cond`.

    So the real mismatch is in how `_ugen_segments` and `_asm_segments` CUT the code, not in which
    kinds may pair, and the aligner's assumption that segment kinds correspond positionally does not
    hold for that function. The pairing was unconfirmed, so it is not kept -- a proposed pattern does
    not change behaviour until it is confirmed.
    """
    best: dict[tuple[int, int], tuple[int, tuple[int, int, str] | None]] = {(0, 0): (0, None)}
    for i in range(len(ugen) + 1):
        for j in range(len(asm) + 1):
            if (i, j) not in best:
                continue
            cost = best[(i, j)][0]
            moves = []
            if i < len(ugen) and j < len(asm) and (ugen[i] == asm[j] or (ugen[i], asm[j]) == ("jump", "ret")):
                moves.append(((i + 1, j + 1), cost, "pair"))
            if i < len(ugen) and ugen[i] == "jump":
                moves.append(((i + 1, j), cost + 1, "skip"))
            for key, new_cost, how in moves:
                if key not in best or best[key][0] > new_cost:
                    best[key] = (new_cost, (i, j, how))
    end = (len(ugen), len(asm))
    if end not in best:
        return None
    path, key = [], end
    while key != (0, 0):
        step = best[key][1]
        path.append(step)
        key = step[:2]
    return path[::-1]


def attribute(asm: str, level5: str, level6: str, dump: str, function: str) -> Attribution:
    """Raises Declined with a reason when any link of the chain does not hold."""
    order = uopt_calls.flow_graph_order(level5)
    graphs = uopt_calls.flow_graphs(level5)
    procedures = uopt_calls.ugen_procedures(dump)
    if function not in graphs or len(order) != len(procedures) or function not in order:
        raise Declined("flow graph or ugen procedure missing")
    blocks = uopt_calls.blocks_of(procedures[order.index(function)])
    pairs = uopt_calls.match(graphs[function], blocks)
    if pairs is None:
        raise Declined("flow graph does not match the u-code")
    proc = uopt_trace.join(level5, level6).get(function)
    if proc is None:
        raise Declined("no colouring trace")
    node_of_block = {index: node for node, index in pairs.items() if index is not None}
    usegs = _ugen_segments(blocks, node_of_block)
    insns, asegs = _asm_segments(strip_padding(asm))
    path = align([k for k, _ in usegs], [k for k, *_ in asegs])
    if path is None:
        raise Declined("segment terminators do not align")
    holders: dict[tuple[int, int], int] = {}
    for record in proc.ranges.values():
        if record.color > 0:
            for node in set(record.default_blocks) | {row[0] for row in record.blocks}:
                holders[(node, record.color)] = record.lr
    result = Attribution(function, list(insns), {})
    pending: list[int] = []
    segment = iter(asegs)
    for i, _j, how in path:
        pending = pending + usegs[i][1]
        if how == "skip":
            result.elided_jumps += 1
            continue
        _kind, start, end = next(segment)
        for insn in insns[start:end + 1]:
            result.nodes[insn.index] = list(pending)
            rows = []
            for position, operand in enumerate(insn.operands):
                if (cfg.is_control_transfer(insn.opcode) or insn.opcode in CALL_OPS) and \
                        insn.opcode not in ("jr", "jalr") and position == len(insn.operands) - 1:
                    continue                                   # branch target, even when it spells "a0"
                found = _OPERAND_REGISTER.match(operand)
                if not found or found.group(1) not in REGISTER_NUMBER:
                    continue
                colour = REGISTER_COLOUR.get(REGISTER_NUMBER[found.group(1)])
                owners = {holders[(n, colour)] for n in pending if colour and (n, colour) in holders}
                rows.append((position, found.group(1), next(iter(owners)) if len(owners) == 1 else None))
            result.operands[insn.index] = rows
        pending = []
    return result
