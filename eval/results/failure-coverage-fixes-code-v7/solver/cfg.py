"""Small, dependency-free control-flow graph analysis for MIPS assembly.

The solver historically treated a function as a flat instruction sequence.
That is sufficient for local offset repair, but it cannot express joins,
loops, dominance, or structured regions.  This module is deliberately
read-only: it recovers graph facts from assembly and makes no source claims.

MIPS delay slots are part of the basic block containing the transfer.  Calls
do not terminate an intraprocedural block, while conditional branches,
unconditional jumps, and ``jr`` do.  Branch-likely instructions are marked so
dataflow clients can conservatively account for their annulled delay slot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


COMMENT = re.compile(r"/\*.*?\*/")
LABEL = re.compile(r"^([.$A-Za-z_][\w.$]*):(?:\s*(.*))?$")
INSTRUCTION = re.compile(r"^([A-Za-z][\w.]*)\s*(.*)$")

CALL_OPS = {"jal", "jalr", "bal"}
UNCONDITIONAL_OPS = {"b", "j"}
INDIRECT_OPS = {"jr"}
LIKELY_OPS = {
    "beql", "bnel", "beqzl", "bnezl", "bgezl", "bgtzl", "blezl",
    "bltzl", "bc1tl", "bc1fl",
}


def clean_line(line: str) -> str:
    """Remove address/encoding comments while retaining instruction text."""
    return re.sub(r"\s{2,}", " ", COMMENT.sub("", line).strip())


def split_operands(text: str) -> tuple[str, ...]:
    """Split operands without breaking relocation or memory parentheses."""
    out: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "," and depth == 0:
            out.append("".join(current).strip())
            current = []
            continue
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        current.append(char)
    if current or text:
        out.append("".join(current).strip())
    return tuple(x for x in out if x)


def is_conditional_branch(opcode: str) -> bool:
    op = opcode.lower()
    if op in CALL_OPS | UNCONDITIONAL_OPS | INDIRECT_OPS or op == "break":
        return False
    return op.startswith("b")


def is_control_transfer(opcode: str) -> bool:
    op = opcode.lower()
    return is_conditional_branch(op) or op in UNCONDITIONAL_OPS | INDIRECT_OPS


def has_delay_slot(opcode: str) -> bool:
    op = opcode.lower()
    return is_control_transfer(op) or op in CALL_OPS


@dataclass(frozen=True)
class Instruction:
    index: int
    text: str
    opcode: str
    operands: tuple[str, ...]
    labels: tuple[str, ...] = ()

    @property
    def target(self) -> str | None:
        if not (is_conditional_branch(self.opcode)
                or self.opcode in UNCONDITIONAL_OPS):
            return None
        if not self.operands:
            return None
        return self.operands[-1].lstrip("$")

    @property
    def branch_likely(self) -> bool:
        return self.opcode in LIKELY_OPS


@dataclass
class BasicBlock:
    id: int
    start: int
    end: int
    instructions: tuple[Instruction, ...]
    labels: tuple[str, ...] = ()
    successors: set[int] = field(default_factory=set)
    predecessors: set[int] = field(default_factory=set)
    terminator: Instruction | None = None
    delay_slot: Instruction | None = None
    unknown_successor: bool = False


@dataclass(frozen=True)
class NaturalLoop:
    header: int
    latch: int
    nodes: frozenset[int]


@dataclass(frozen=True)
class SESERegion:
    entry: int
    exit: int
    nodes: frozenset[int]


@dataclass
class ControlFlowGraph:
    instructions: tuple[Instruction, ...]
    blocks: dict[int, BasicBlock]
    entry: int | None
    label_to_instruction: dict[str, int]
    instruction_to_block: dict[int, int]

    def reachable(self) -> set[int]:
        if self.entry is None:
            return set()
        seen: set[int] = set()
        todo = [self.entry]
        while todo:
            block = todo.pop()
            if block in seen:
                continue
            seen.add(block)
            todo.extend(sorted(self.blocks[block].successors - seen,
                               reverse=True))
        return seen

    def reverse_postorder(self) -> list[int]:
        if self.entry is None:
            return []
        seen: set[int] = set()
        post: list[int] = []

        def visit(block: int) -> None:
            if block in seen:
                return
            seen.add(block)
            for succ in sorted(self.blocks[block].successors):
                visit(succ)
            post.append(block)

        visit(self.entry)
        return list(reversed(post))

    def dominators(self) -> dict[int, frozenset[int]]:
        nodes = self.reachable()
        if self.entry is None:
            return {}
        dom: dict[int, set[int]] = {
            node: ({node} if node == self.entry else set(nodes))
            for node in nodes
        }
        changed = True
        order = self.reverse_postorder()
        while changed:
            changed = False
            for node in order:
                if node == self.entry:
                    continue
                preds = self.blocks[node].predecessors & nodes
                common = (set.intersection(*(dom[p] for p in preds))
                          if preds else set())
                update = {node} | common
                if update != dom[node]:
                    dom[node] = update
                    changed = True
        return {node: frozenset(value) for node, value in dom.items()}

    def immediate_dominators(self) -> dict[int, int | None]:
        dom = self.dominators()
        out: dict[int, int | None] = {}
        for node, values in dom.items():
            strict = values - {node}
            out[node] = (max(strict, key=lambda d: len(dom[d]))
                         if strict else None)
        return out

    def postdominators(self) -> dict[int, frozenset[int]]:
        """Post-dominators over reachable nodes with implicit shared exit."""
        nodes = self.reachable()
        if not nodes:
            return {}
        exits = {n for n in nodes if not (self.blocks[n].successors & nodes)}
        post: dict[int, set[int]] = {
            node: ({node} if node in exits else set(nodes)) for node in nodes
        }
        changed = True
        order = list(reversed(self.reverse_postorder()))
        while changed:
            changed = False
            for node in order:
                if node in exits:
                    continue
                succ = self.blocks[node].successors & nodes
                common = (set.intersection(*(post[s] for s in succ))
                          if succ else set())
                update = {node} | common
                if update != post[node]:
                    post[node] = update
                    changed = True
        return {node: frozenset(value) for node, value in post.items()}

    def immediate_postdominators(self) -> dict[int, int | None]:
        post = self.postdominators()
        out: dict[int, int | None] = {}
        for node, values in post.items():
            strict = values - {node}
            out[node] = (max(strict, key=lambda d: len(post[d]))
                         if strict else None)
        return out

    def strongly_connected_components(self) -> list[frozenset[int]]:
        """Tarjan SCCs in deterministic discovery order."""
        nodes = self.reachable()
        index = 0
        indices: dict[int, int] = {}
        low: dict[int, int] = {}
        stack: list[int] = []
        on_stack: set[int] = set()
        out: list[frozenset[int]] = []

        def visit(node: int) -> None:
            nonlocal index
            indices[node] = low[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)
            for succ in sorted(self.blocks[node].successors & nodes):
                if succ not in indices:
                    visit(succ)
                    low[node] = min(low[node], low[succ])
                elif succ in on_stack:
                    low[node] = min(low[node], indices[succ])
            if low[node] == indices[node]:
                component: set[int] = set()
                while True:
                    item = stack.pop()
                    on_stack.remove(item)
                    component.add(item)
                    if item == node:
                        break
                out.append(frozenset(component))

        for node in self.reverse_postorder():
            if node not in indices:
                visit(node)
        return out

    def natural_loops(self) -> list[NaturalLoop]:
        """Recover loops from back edges whose head dominates their tail."""
        dom = self.dominators()
        loops: list[NaturalLoop] = []
        for latch in sorted(dom):
            for header in sorted(self.blocks[latch].successors):
                if header not in dom.get(latch, ()):
                    continue
                nodes = {header, latch}
                todo = [latch]
                while todo:
                    node = todo.pop()
                    for pred in self.blocks[node].predecessors:
                        if pred not in nodes:
                            nodes.add(pred)
                            if pred != header:
                                todo.append(pred)
                loops.append(NaturalLoop(header, latch, frozenset(nodes)))
        return loops

    def sese_regions(self) -> list[SESERegion]:
        """Conservative single-entry/single-exit (hammock) candidates.

        A region is emitted only when all external incoming edges enter at one
        block and all external outgoing edges leave through one block.  This is
        intentionally narrower than a full program-structure tree: false
        negatives cost an opportunity, while a false region would mislead a
        structural generator.
        """
        dom = self.dominators()
        post = self.postdominators()
        nodes = self.reachable()
        regions: set[tuple[int, int, frozenset[int]]] = set()
        for entry in sorted(nodes):
            for exit_ in sorted(post.get(entry, ()) - {entry}):
                inside = frozenset(
                    n for n in nodes
                    if entry in dom.get(n, ()) and exit_ in post.get(n, ()))
                if len(inside) < 2 or entry not in inside or exit_ not in inside:
                    continue
                incoming = {(p, n) for n in inside
                            for p in self.blocks[n].predecessors if p not in inside}
                outgoing = {(n, s) for n in inside
                            for s in self.blocks[n].successors if s not in inside}
                if any(n != entry for _p, n in incoming):
                    continue
                if any(n != exit_ for n, _s in outgoing):
                    continue
                regions.add((entry, exit_, inside))
        return [SESERegion(e, x, ns) for e, x, ns in sorted(
            regions, key=lambda item: (len(item[2]), item[0], item[1]))]


def parse_assembly(asm: str) -> tuple[tuple[Instruction, ...], dict[str, int]]:
    pending: list[str] = []
    rows: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    trailing_labels: list[str] = []
    for raw in asm.splitlines():
        line = clean_line(raw)
        if not line:
            continue
        if line.startswith("glabel "):
            pending.append(line.split(None, 1)[1].strip())
            continue
        label = LABEL.match(line)
        if label:
            pending.append(label.group(1))
            line = (label.group(2) or "").strip()
            if not line:
                continue
        match = INSTRUCTION.match(line)
        if not match:
            continue
        opcode = match.group(1).lower()
        operands = split_operands(match.group(2).strip())
        rows.append((line, opcode, operands, tuple(pending)))
        pending = []
    trailing_labels.extend(pending)

    instructions = tuple(
        Instruction(i, text, opcode, operands, labels)
        for i, (text, opcode, operands, labels) in enumerate(rows))
    label_to_instruction = {
        label: insn.index for insn in instructions for label in insn.labels
    }
    # A trailing label names the function exit, not an instruction.  Keep it
    # absent rather than inventing an addressable node.
    _ = trailing_labels
    return instructions, label_to_instruction


def build(asm: str) -> ControlFlowGraph:
    instructions, labels = parse_assembly(asm)
    if not instructions:
        return ControlFlowGraph((), {}, None, labels, {})

    # Workspace-normalized object dumps spell branch destinations as hex byte
    # offsets without labels. Interpret them ONLY with an explicit format marker;
    # raw assembly may instead contain absolute addresses or unresolved symbols.
    bases = re.findall(r'(?m)^# MIPS_DIFF_NUMERIC_BRANCH_BASE\s+(0x[0-9a-fA-F]+|\d+)\s*$', asm)
    if bases:
        values = {int(value, 0) for value in bases}
        if len(values) != 1:
            raise ValueError('conflicting numeric branch address conventions')
        base = values.pop()
        if base % 4 or not 0 <= base <= 0xffffffff:
            raise ValueError('invalid numeric branch address base')
        for instruction in instructions:
            if not (is_conditional_branch(instruction.opcode) or instruction.opcode in UNCONDITIONAL_OPS):
                continue
            token = instruction.target or ''
            if token in labels or not re.fullmatch(r'(?:0x)?[0-9a-fA-F]+', token):
                continue
            offset = int(token, 16) - base
            if offset >= 0 and offset % 4 == 0 and offset // 4 < len(instructions):
                labels[token] = offset // 4

    leaders = {0}
    n = len(instructions)
    for insn in instructions:
        if not is_control_transfer(insn.opcode):
            continue
        target_index = labels.get(insn.target or "")
        if target_index is not None:
            leaders.add(target_index)
        end = min(n - 1, insn.index + 1) if has_delay_slot(insn.opcode) \
            else insn.index
        if end + 1 < n:
            leaders.add(end + 1)

    starts = sorted(leaders)
    blocks: dict[int, BasicBlock] = {}
    instruction_to_block: dict[int, int] = {}
    for block_id, start in enumerate(starts):
        end = starts[block_id + 1] - 1 if block_id + 1 < len(starts) else n - 1
        members = instructions[start:end + 1]
        block_labels = tuple(label for insn in members for label in insn.labels
                             if insn.index == start)
        block = BasicBlock(block_id, start, end, members, block_labels)
        for insn in members:
            instruction_to_block[insn.index] = block_id
            if is_control_transfer(insn.opcode) and block.terminator is None:
                block.terminator = insn
                if insn.index + 1 <= end:
                    block.delay_slot = instructions[insn.index + 1]
        blocks[block_id] = block

    for block_id, block in blocks.items():
        term = block.terminator
        fallthrough = block_id + 1 if block_id + 1 in blocks else None
        if term is None:
            if fallthrough is not None:
                block.successors.add(fallthrough)
        elif is_conditional_branch(term.opcode):
            target_index = labels.get(term.target or "")
            if target_index is None:
                block.unknown_successor = True
            else:
                block.successors.add(instruction_to_block[target_index])
            if fallthrough is not None:
                block.successors.add(fallthrough)
        elif term.opcode in UNCONDITIONAL_OPS:
            target_index = labels.get(term.target or "")
            if target_index is None:
                block.unknown_successor = True
            else:
                block.successors.add(instruction_to_block[target_index])
        elif term.opcode in INDIRECT_OPS:
            block.unknown_successor = term.operands != ("$ra",) \
                and term.operands != ("ra",)

    for block_id, block in blocks.items():
        for succ in block.successors:
            blocks[succ].predecessors.add(block_id)

    return ControlFlowGraph(instructions, blocks, 0, labels,
                            instruction_to_block)
