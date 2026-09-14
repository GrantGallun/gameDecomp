"""Block-aware, weighted alignment of target and candidate instructions.

``difflib.SequenceMatcher`` is designed for text similarity.  Repeated MIPS
opcodes make several textual alignments look equally plausible, while the
solver needs to know whether two instructions are safe to compare.  This
module first aligns basic blocks, then uses a domain-weighted dynamic program
inside paired blocks.  Ambiguous traceback steps are retained as evidence
quality rather than silently promoted into rewrite constraints.

The module is initially diagnostic.  Existing repairs can compare its output
against their current alignment before adopting it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Sequence, TypeVar

from solver import cfg


T = TypeVar("T")

REGISTER = re.compile(
    r"\$?\b(?:zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)\b|\$\d+")
IMMEDIATE = re.compile(r"(?<![\w.])(-?0x[0-9A-Fa-f]+|-?\d+)(?![\w.])")
RELOCATION = re.compile(r"%(?:hi|lo)\(([^)]+)\)")
MEMORY = re.compile(
    r"^(?P<op>[a-z][a-z0-9.]*)\s+(?P<reg>\$?\w+)\s*,\s*"
    r"(?P<offset>-?(?:0x)?[0-9A-Fa-f]+)\(\$?(?P<base>\w+)\)$")

LOADS = {"lb", "lbu", "lh", "lhu", "lw", "lwl", "lwr", "lwc1", "ld", "ldc1"}
STORES = {"sb", "sh", "sw", "swl", "swr", "swc1", "sd", "sdc1"}
WIDTH = {
    "lb": 1, "lbu": 1, "sb": 1, "lh": 2, "lhu": 2, "sh": 2,
    "lw": 4, "lwl": 4, "lwr": 4, "sw": 4, "swl": 4, "swr": 4,
    "lwc1": 4, "swc1": 4, "ld": 8, "sd": 8, "ldc1": 8, "sdc1": 8,
}


@dataclass(frozen=True)
class Step:
    target: cfg.Instruction | None
    candidate: cfg.Instruction | None
    cost: int
    ambiguous: bool = False
    target_block: int | None = None
    candidate_block: int | None = None

    @property
    def comparable(self) -> bool:
        """A substitution cheaper than deleting and reinserting both lines."""
        return self.target is not None and self.candidate is not None \
            and self.cost < 8 and not self.ambiguous


@dataclass
class Faults:
    offset: int = 0
    width: int = 0
    relocation: int = 0
    register: int = 0
    immediate: int = 0
    branch_topology: int = 0
    opcode_substitution: int = 0
    missing_target: int = 0
    extra_candidate: int = 0
    delay_slot: int = 0
    jump_table: int = 0
    ambiguous: int = 0

    @property
    def structural(self) -> int:
        # Categories are disjoint. Delay-slot is an annotation on one of them.
        return (self.branch_topology + self.opcode_substitution
                + self.missing_target + self.extra_candidate)


@dataclass
class Alignment:
    target_graph: cfg.ControlFlowGraph
    candidate_graph: cfg.ControlFlowGraph
    steps: list[Step]
    cost: int
    faults: Faults = field(default_factory=Faults)

    @property
    def high_confidence_pairs(self) -> list[tuple[str, str]]:
        return [(step.target.text, step.candidate.text) for step in self.steps
                if step.comparable and step.target.text != step.candidate.text]


def streams(diff: str) -> tuple[list[str], list[str]]:
    """Reconstruct both streams from a unified diff with shared context."""
    target: list[str] = []
    candidate: list[str] = []
    for line in diff.splitlines():
        if not line or line.startswith(("---", "+++", "@@")):
            continue
        body = line[1:].strip()
        if not body:
            continue
        if line[0] == " ":
            target.append(body)
            candidate.append(body)
        elif line[0] == "-":
            target.append(body)
        elif line[0] == "+":
            candidate.append(body)
    return target, candidate


def _opcode(insn: cfg.Instruction) -> str:
    return insn.opcode


def _kind(insn: cfg.Instruction) -> str:
    op = insn.opcode
    if op in LOADS:
        return "load"
    if op in STORES:
        return "store"
    if cfg.is_conditional_branch(op) or op in cfg.UNCONDITIONAL_OPS | cfg.INDIRECT_OPS:
        return "branch"
    if op in cfg.CALL_OPS:
        return "call"
    return "other"


def instruction_cost(left: cfg.Instruction, right: cfg.Instruction) -> int:
    if left.text == right.text:
        return 0
    if left.opcode == right.opcode:
        return 1
    if _kind(left) == _kind(right) and _kind(left) != "other":
        return 3
    return 6


def _sequence_align(left: Sequence[T], right: Sequence[T],
                    substitution: Callable[[T, T], int],
                    gap_left: Callable[[T], int],
                    gap_right: Callable[[T], int]) -> tuple[list[tuple[int | None, int | None, int, bool]], int]:
    """Global alignment with deterministic traceback and local tie receipts."""
    n, m = len(left), len(right)
    score = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = score[i - 1][0] + gap_left(left[i - 1])
    for j in range(1, m + 1):
        score[0][j] = score[0][j - 1] + gap_right(right[j - 1])
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            score[i][j] = min(
                score[i - 1][j - 1] + substitution(left[i - 1], right[j - 1]),
                score[i - 1][j] + gap_left(left[i - 1]),
                score[i][j - 1] + gap_right(right[j - 1]),
            )

    steps: list[tuple[int | None, int | None, int, bool]] = []
    i, j = n, m
    while i or j:
        choices: list[tuple[str, int]] = []
        if i and j:
            cost = substitution(left[i - 1], right[j - 1])
            if score[i][j] == score[i - 1][j - 1] + cost:
                choices.append(("pair", cost))
        if i:
            cost = gap_left(left[i - 1])
            if score[i][j] == score[i - 1][j] + cost:
                choices.append(("left", cost))
        if j:
            cost = gap_right(right[j - 1])
            if score[i][j] == score[i][j - 1] + cost:
                choices.append(("right", cost))
        ambiguous = len(choices) > 1
        # Prefer a plausible pair, then a target-only line, then candidate-only.
        choice, cost = choices[0]
        if choice == "pair":
            steps.append((i - 1, j - 1, cost, ambiguous))
            i -= 1
            j -= 1
        elif choice == "left":
            steps.append((i - 1, None, cost, ambiguous))
            i -= 1
        else:
            steps.append((None, j - 1, cost, ambiguous))
            j -= 1
    steps.reverse()
    return steps, score[n][m]


def _blocks(graph: cfg.ControlFlowGraph) -> list[cfg.BasicBlock]:
    return [graph.blocks[i] for i in sorted(graph.blocks)]


def _opcode_distance(left: cfg.BasicBlock, right: cfg.BasicBlock) -> int:
    rows, cost = _sequence_align(
        left.instructions, right.instructions,
        lambda a, b: 0 if _opcode(a) == _opcode(b) else 3,
        lambda _a: 2, lambda _b: 2)
    _ = rows
    return cost


def _block_gap(block: cfg.BasicBlock) -> int:
    return max(4, 4 * len(block.instructions))


def _delay_indices(graph: cfg.ControlFlowGraph) -> set[int]:
    return {block.delay_slot.index for block in graph.blocks.values()
            if block.delay_slot is not None}


def _classify(alignment: Alignment) -> Faults:
    faults = Faults()
    target_delays = _delay_indices(alignment.target_graph)
    candidate_delays = _delay_indices(alignment.candidate_graph)
    for step in alignment.steps:
        if step.ambiguous:
            faults.ambiguous += 1
        left, right = step.target, step.candidate
        if left is None:
            faults.extra_candidate += 1
            if right is not None and right.index in candidate_delays:
                faults.delay_slot += 1
            continue
        if right is None:
            faults.missing_target += 1
            if left.index in target_delays:
                faults.delay_slot += 1
            continue
        if left.text == right.text:
            continue
        if left.index in target_delays or right.index in candidate_delays:
            faults.delay_slot += 1

        ml, mr = MEMORY.match(left.text), MEMORY.match(right.text)
        if ml and mr:
            if WIDTH.get(ml.group("op")) != WIDTH.get(mr.group("op")):
                faults.width += 1
            elif ml.group("base") == mr.group("base") \
                    and ml.group("offset") != mr.group("offset"):
                if ml.group("base") == "sp":
                    faults.register += 1
                else:
                    faults.offset += 1
            else:
                faults.register += 1
            continue

        rel_left, rel_right = RELOCATION.findall(left.text), RELOCATION.findall(right.text)
        if rel_left or rel_right:
            if rel_left != rel_right:
                faults.relocation += 1
                names = " ".join(rel_left + rel_right).lower()
                if "jtbl" in names or ".rodata" in names or "jump" in names:
                    faults.jump_table += 1
                continue

        if _kind(left) == "branch" and _kind(right) == "branch":
            if left.opcode != right.opcode or left.target != right.target:
                faults.branch_topology += 1
                continue

        if left.opcode != right.opcode:
            faults.opcode_substitution += 1
            continue
        regs_left = tuple(x.lstrip("$") for x in REGISTER.findall(left.text))
        regs_right = tuple(x.lstrip("$") for x in REGISTER.findall(right.text))
        if regs_left != regs_right:
            faults.register += 1
        elif IMMEDIATE.findall(left.text) != IMMEDIATE.findall(right.text):
            faults.immediate += 1
        else:
            faults.opcode_substitution += 1
    return faults


def align_streams(target: Sequence[str], candidate: Sequence[str]) -> Alignment:
    target_graph = cfg.build("\n".join(target))
    candidate_graph = cfg.build("\n".join(candidate))
    target_blocks, candidate_blocks = _blocks(target_graph), _blocks(candidate_graph)
    block_steps, block_cost = _sequence_align(
        target_blocks, candidate_blocks, _opcode_distance,
        _block_gap, _block_gap)

    steps: list[Step] = []
    cost = block_cost
    for left_i, right_i, _outer_cost, outer_ambiguous in block_steps:
        if left_i is None:
            block = candidate_blocks[right_i]
            steps.extend(Step(None, insn, 4, outer_ambiguous, None, block.id)
                         for insn in block.instructions)
            continue
        if right_i is None:
            block = target_blocks[left_i]
            steps.extend(Step(insn, None, 4, outer_ambiguous, block.id, None)
                         for insn in block.instructions)
            continue
        left_block, right_block = target_blocks[left_i], candidate_blocks[right_i]
        inner, _inner_cost = _sequence_align(
            left_block.instructions, right_block.instructions,
            instruction_cost, lambda _a: 4, lambda _b: 4)
        for li, ri, inner_cost, inner_ambiguous in inner:
            steps.append(Step(
                left_block.instructions[li] if li is not None else None,
                right_block.instructions[ri] if ri is not None else None,
                inner_cost, outer_ambiguous or inner_ambiguous,
                left_block.id if li is not None else None,
                right_block.id if ri is not None else None))

    alignment = Alignment(target_graph, candidate_graph, steps,
                          sum(step.cost for step in steps))
    alignment.faults = _classify(alignment)
    return alignment


def align_diff(diff: str) -> Alignment:
    return align_streams(*streams(diff))


def safe_offset_pairs(diff: str) -> list[tuple[str, str]]:
    """Unambiguous memory pairs differing only in numeric offset.

    This is the high-consequence view used by layout repair.  A same-opcode
    pair with a changed destination register is useful for diagnosis but is
    not evidence that a field moved, so it is deliberately excluded here.
    """
    out: list[tuple[str, str]] = []
    for step in align_diff(diff).steps:
        if step.ambiguous or step.target is None or step.candidate is None:
            continue
        left, right = MEMORY.match(step.target.text), MEMORY.match(step.candidate.text)
        if not (left and right):
            continue
        if left.group("op") != right.group("op"):
            continue
        if left.group("reg").lstrip("$") != right.group("reg").lstrip("$"):
            continue
        if left.group("base") != right.group("base"):
            continue
        if left.group("offset") == right.group("offset"):
            continue
        out.append((step.target.text, step.candidate.text))
    return out


def safe_memory_pairs(diff: str) -> list[tuple[str, str]]:
    """Unambiguous memory pairs differing only in offset or access width."""
    out: list[tuple[str, str]] = []
    for step in align_diff(diff).steps:
        if step.ambiguous or step.target is None or step.candidate is None:
            continue
        left, right = MEMORY.match(step.target.text), MEMORY.match(step.candidate.text)
        if not (left and right):
            continue
        if left.group("reg").lstrip("$") != right.group("reg").lstrip("$"):
            continue
        if left.group("base") != right.group("base"):
            continue
        same_offset = left.group("offset") == right.group("offset")
        same_opcode = left.group("op") == right.group("op")
        same_direction = ((left.group("op") in LOADS and right.group("op") in LOADS)
                          or (left.group("op") in STORES and right.group("op") in STORES))
        if same_opcode and not same_offset:
            out.append((step.target.text, step.candidate.text))
        elif same_offset and not same_opcode and same_direction:
            out.append((step.target.text, step.candidate.text))
    return out
