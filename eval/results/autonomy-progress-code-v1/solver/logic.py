"""Register-independent evidence for a logic-first decompilation lane.

This module deliberately does *not* prove semantic equivalence.  It compares
observable assembly structure that should agree once a candidate has recovered
the broad program: calls, control-flow shape, non-stack memory effects, and
instruction selection.  Behavioral equivalence needs a separate differential
execution receipt; byte exactness remains the authoritative terminal oracle.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

from solver import cfg, dataflow, workspace


STAGE_ORDER = {
    "not_compiling": 0,
    "compiling_candidate": 1,
    "logic_shape_candidate": 2,
    "structural_candidate": 3,
    "byte_exact": 4,
}


@dataclass(frozen=True)
class Profile:
    instruction_count: int
    block_count: int
    reachable_blocks: int
    loop_count: int
    direct_call_sequence: tuple[str, ...]
    indirect_call_count: int
    memory_effects: tuple[str, ...]
    branch_shapes: tuple[str, ...]
    cfg_shapes: tuple[str, ...]
    opcodes: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Assessment:
    stage: str
    semantic_status: str
    exact: bool
    metrics: dict[str, float]
    gates: dict[str, bool]
    target: Profile | None
    candidate: Profile | None
    limitations: tuple[str, ...]

    def to_dict(self) -> dict:
        value = asdict(self)
        return value


def quality_key(assessment: Assessment) -> tuple:
    """Higher is better for logic-first search; bytes are intentionally absent."""
    metrics = assessment.metrics
    return (
        STAGE_ORDER.get(assessment.stage, -1),
        metrics.get("call_sequence", 0.0),
        metrics.get("memory_effects", 0.0),
        metrics.get("control_structure", 0.0),
        metrics.get("opcode_sequence", 0.0),
    )


def _term_shape(block: cfg.BasicBlock) -> str:
    term = block.terminator
    if term is None:
        return "fallthrough"
    if cfg.is_conditional_branch(term.opcode):
        return "conditional"
    if term.opcode in cfg.UNCONDITIONAL_OPS:
        return "jump"
    if term.opcode == "jr" and term.operands in {("ra",), ("$ra",)}:
        return "return"
    return "indirect"


def _memory_effect(access: dataflow.MemoryAccess) -> str | None:
    address = access.address
    if address is not None and address.kind == "address" \
            and address.name == "stack":
        return None
    rendered = (address.describe(precise=True)
                if address is not None else "unresolved")
    direction = "read" if access.is_load else "write"
    return f"{direction}:{access.width}:{rendered}"


def profile(assembly: str) -> Profile:
    result = dataflow.analyse(assembly)
    graph = result.graph
    reachable = graph.reachable()
    calls = []
    indirect = 0
    for site in sorted(result.callsites.values(), key=lambda item: item.instruction):
        if site.target:
            calls.append(site.target)
        else:
            calls.append("<indirect>")
            indirect += 1
    effects = [effect for access in sorted(
        result.accesses.values(), key=lambda item: item.instruction)
               if (effect := _memory_effect(access)) is not None]
    branches = []
    shapes = []
    for block_id in sorted(reachable):
        block = graph.blocks[block_id]
        term = _term_shape(block)
        if term != "fallthrough":
            branches.append(term)
        shapes.append(
            f"{len(block.instructions)}:{term}:"
            f"in{len(block.predecessors & reachable)}:"
            f"out{len(block.successors & reachable)}")
    return Profile(
        instruction_count=len(graph.instructions),
        block_count=len(graph.blocks),
        reachable_blocks=len(reachable),
        loop_count=len(graph.natural_loops()),
        direct_call_sequence=tuple(calls),
        indirect_call_count=indirect,
        memory_effects=tuple(effects),
        branch_shapes=tuple(branches),
        cfg_shapes=tuple(shapes),
        opcodes=tuple(instruction.opcode for instruction in graph.instructions),
    )


def _sequence(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left and not right:
        return 1.0
    return SequenceMatcher(a=left, b=right, autojunk=False).ratio()


def _multiset(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    a, b = Counter(left), Counter(right)
    if not a and not b:
        return 1.0
    intersection = sum((a & b).values())
    union = sum((a | b).values())
    return intersection / union if union else 1.0


def _count_similarity(left: int, right: int) -> float:
    if left == right == 0:
        return 1.0
    return min(left, right) / max(left, right)


def compare(target_assembly: str, candidate_assembly: str, *,
            attempt: workspace.Attempt) -> Assessment:
    """Classify a compiled candidate without calling it behaviorally proven."""
    if not attempt.compiled:
        return Assessment(
            stage="not_compiling", semantic_status="not_tested", exact=False,
            metrics={}, gates={}, target=None, candidate=None,
            limitations=("candidate did not compile",))
    target = profile(target_assembly)
    candidate = profile(candidate_assembly)
    metrics = {
        "call_sequence": _sequence(target.direct_call_sequence,
                                   candidate.direct_call_sequence),
        "memory_effects": _multiset(target.memory_effects,
                                    candidate.memory_effects),
        "branch_shapes": _multiset(target.branch_shapes,
                                   candidate.branch_shapes),
        "cfg_shapes": _multiset(target.cfg_shapes, candidate.cfg_shapes),
        "loop_count": _count_similarity(target.loop_count,
                                         candidate.loop_count),
        "instruction_count": _count_similarity(target.instruction_count,
                                                candidate.instruction_count),
        "opcode_sequence": _sequence(target.opcodes, candidate.opcodes),
    }
    control = (
        metrics["branch_shapes"] + metrics["cfg_shapes"] +
        metrics["loop_count"] + metrics["instruction_count"]
    ) / 4.0
    metrics["control_structure"] = control
    gates = {
        "calls_agree": target.direct_call_sequence == candidate.direct_call_sequence,
        "logic_control": control >= 0.75,
        "logic_effects": metrics["memory_effects"] >= 0.75,
        "logic_opcodes": metrics["opcode_sequence"] >= 0.65,
        "structural_control": control >= 0.95,
        "structural_effects": metrics["memory_effects"] >= 0.95,
        "structural_opcodes": metrics["opcode_sequence"] >= 0.90,
    }
    logic_shape = all(gates[key] for key in (
        "calls_agree", "logic_control", "logic_effects", "logic_opcodes"))
    structural = all(gates[key] for key in (
        "calls_agree", "structural_control", "structural_effects",
        "structural_opcodes"))
    if attempt.exact:
        stage = "byte_exact"
        semantic = "implied_by_byte_exact_target_object"
    elif structural:
        stage = "structural_candidate"
        semantic = "not_tested"
    elif logic_shape:
        stage = "logic_shape_candidate"
        semantic = "not_tested"
    else:
        stage = "compiling_candidate"
        semantic = "not_tested"
    return Assessment(
        stage=stage, semantic_status=semantic, exact=attempt.exact,
        metrics={key: round(value, 6) for key, value in metrics.items()},
        gates=gates, target=target, candidate=candidate,
        limitations=(
            "assembly-shape agreement is not a semantic-equivalence proof",
            "behavioral differential execution has not been run",
            "stack traffic and register allocation are intentionally ignored "
            "by the memory-effect comparison",
        ))
