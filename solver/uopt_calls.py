"""Where the calls are in uopt's flow graph, and which live ranges cross them.

The uopt trace (solver/uopt_trace.py) records every colouring decision but never marks a
call site, and it cannot say which band a range belongs in: whether it must survive a
call. ugen's tree dump can. `cc -Wc,-d,-e,PATH` writes it; its "Tree dump after Build"
section is ugen's input, uopt's re-emitted u-code, statement by statement, with `cup`
and `icuf` calls, labels and jumps.

Matching the two graphs rests on three measured facts (SBK1 census, 2026-09-14):

    uopt ends a block after every call. `lab 20 ... icuf ... tjp 20` is one u-code block
        and two uopt nodes. Splitting u-code blocks after calls as well as at labels and
        jumps makes the structures comparable.
    uopt has empty unlabeled nodes the u-code no longer shows: after a call or statement
        that falls into a label, and on loop exits. They are matched as pass-throughs.
    uopt prints its flow graph in its own node order (not program order), so matching
        walks both graphs from the entry: labelled successors pair by label, the one
        unlabelled successor pairs with the fall-through.

`match` returns None on any inconsistency; a procedure it cannot map is reported, never
guessed. On SBK1 it mapped 1,788 of 1,967 procedures with no call left in an unmatched
block.

`-d` is NOT code-neutral: it renumbered ugen temporaries in 27 of 193 SBK1 TUs. Take the
dump in a compile of its own and never use that compile's object.

THE BAND (`predict_band`) is a measured approximation, not a rule: callee-saved when the
range crosses calls whose loop-weighted count (10 ** (loopdepth - 1) per crossed call
node) is at least 3. 94.4% of 8,712 in-sample integer decisions against 77.3% for always
caller-saved; the residue is a cost trade-off (value kind, reloads, an already-open
callee register) the trace does not expose. It must not steer anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from solver.uopt_trace import LiveRange, _TIMING

CALLS = {"cup", "icuf", "rcuf"}
CONDITIONAL = {"fjp", "tjp"}
NO_FALLTHROUGH = {"ujp", "ret", "xjp", "end"}
BAND_WEIGHT_THRESHOLD = 3

_STATEMENT = re.compile(r"^\s*\d+\t  (\w+)(.*)$")      # top level: a tab and exactly two spaces
_FLOW_HEADER = re.compile(r"^\s*flow graph for (\S+):")
_NODE = re.compile(r"^\s+(\d+)\s+(\d+)\s*$")
_SUCCESSOR = re.compile(r"^suc::::\s+(\d+)\s+(\d+)")
_LOOP_DEPTH = re.compile(r"^% % % node\s+(\d+) loopdepth\s+(\d+)")


@dataclass
class Block:
    label: int
    ops: list[tuple[str, int | None]] = field(default_factory=list)
    ends_call: bool = False
    successors: list[int] = field(default_factory=list)


def flow_graphs(level5: str) -> dict[str, dict[int, tuple[int, list[int]]]]:
    """procedure -> {node: (label, successors)} from `-zdbug:5`; the last printing wins."""
    graphs: dict[str, dict[int, tuple[int, list[int]]]] = {}
    current = node = None
    for line in level5.splitlines():
        header = _FLOW_HEADER.match(line)
        if header:
            current, node = {}, None
            graphs[header.group(1)] = current
            continue
        if current is None:
            continue
        if (found := _NODE.match(line)):
            node = int(found.group(1))
            current[node] = (int(found.group(2)), [])
        elif (successor := _SUCCESSOR.match(line)) and node is not None:
            current[node][1].append(int(successor.group(1)))
        elif not line.startswith("pre::::"):
            current = None
    return graphs


def flow_graph_order(level5: str) -> list[str]:
    return list(dict.fromkeys(_FLOW_HEADER.match(line).group(1) for line in level5.splitlines()
                              if _FLOW_HEADER.match(line)))


def loop_depths(level5: str) -> dict[str, dict[int, int]]:
    depths: dict[str, dict[int, int]] = {}
    procedure = None
    for line in level5.splitlines():
        if (timing := _TIMING.search(line)):
            procedure = timing.group(1)
            depths.setdefault(procedure, {})
        elif procedure and (found := _LOOP_DEPTH.match(line)):
            depths[procedure][int(found.group(1))] = int(found.group(2))
    return depths


def ugen_procedures(dump: str) -> list[list[tuple[str, int | None]]]:
    """Top-level statements of each "Tree dump after Build" section, in procedure order."""
    procedures: list[list[tuple[str, int | None]]] = []
    active = False
    for line in dump.splitlines():
        if line.startswith("Tree dump after"):
            active = line.startswith("Tree dump after Build")
            if active:
                procedures.append([])
            continue
        if active and (statement := _STATEMENT.match(line)):
            label = re.search(r"i1=(\d+)", statement.group(2))
            procedures[-1].append((statement.group(1), int(label.group(1)) if label else None))
    return procedures


def blocks_of(statements: list[tuple[str, int | None]]) -> list[Block]:
    """Blocks split at labels, after conditional and unconditional jumps, and after calls."""
    blocks: list[Block] = []
    current: Block | None = None
    for op, label in statements:
        if current is None:
            if op == "ent":
                current = Block(0)
            continue
        if op == "lab":
            if current.ops:
                blocks.append(current)
            current = Block(label or 0, [(op, label)])
            continue
        current.ops.append((op, label))
        if op in CALLS or op in CONDITIONAL or op in NO_FALLTHROUGH:
            current.ends_call = op in CALLS
            blocks.append(current)
            current = Block(0)
    if current is not None and current.ops:
        blocks.append(current)
    by_label = {block.label: index for index, block in enumerate(blocks) if block.label}
    for index, block in enumerate(blocks):
        op, target = block.ops[-1]
        if op not in NO_FALLTHROUGH and index + 1 < len(blocks):
            block.successors.append(index + 1)
        if op in CONDITIONAL | {"ujp"} and target in by_label:
            block.successors.append(by_label[target])
    return blocks


def match(graph: dict[int, tuple[int, list[int]]], blocks: list[Block]) -> dict[int, int | None] | None:
    """uopt node -> u-code block index, None for a uopt-only empty node; None if inconsistent."""
    if 0 not in graph or not blocks:
        return None
    pairs: dict[int, object] = {0: 0}
    work = [(0, tuple(blocks[0].successors))]
    while work:
        node, successors = work.pop()
        labelled = {blocks[s].label: s for s in successors if blocks[s].label}
        plain = [s for s in successors if not blocks[s].label]
        plain_nodes = [n for n in graph[node][1] if not graph[n][0]]
        for next_node in graph[node][1]:
            label = graph[next_node][0]
            if label:
                if label not in labelled:
                    return None
                target = labelled[label]
                onward = tuple(blocks[target].successors)
            elif len(plain_nodes) == 1 and len(plain) == 1:
                target = plain[0]
                onward = tuple(blocks[target].successors)
            elif len(plain_nodes) == 1 and not plain:
                target = ("empty", successors)             # pass-through: same successors onward
                onward = successors
            else:
                return None
            if next_node in pairs:
                if pairs[next_node] != target:
                    return None
                continue
            pairs[next_node] = target
            work.append((next_node, onward))
    if len(pairs) != len(graph):
        return None
    return {node: (target if isinstance(target, int) else None) for node, target in pairs.items()}


def call_nodes(graph, blocks) -> list[int] | None:
    pairs = match(graph, blocks)
    if pairs is None:
        return None
    return sorted(node for node, index in pairs.items() if index is not None and blocks[index].ends_call)


def crossed_calls(record: LiveRange, calls: list[int]) -> list[int]:
    """Call nodes the range is live across: live through the node, or live into and out of it."""
    crossed = []
    for node in calls:
        if node in record.default_blocks:
            crossed.append(node)
            continue
        flags = record.block_flags.get(node)
        if flags is not None and not flags[0] and not flags[1]:       # not first-is-store, not dead-out
            crossed.append(node)
    return crossed


def call_weight(crossed: list[int], depths: dict[int, int]) -> int:
    return sum(10 ** (depths.get(node, 1) - 1) for node in crossed)


def predict_band(record: LiveRange, calls: list[int], depths: dict[int, int]) -> str:
    """int_callee or int_caller: the measured approximation described in the module docstring."""
    weight = call_weight(crossed_calls(record, calls), depths)
    return "int_callee" if weight >= BAND_WEIGHT_THRESHOLD else "int_caller"
