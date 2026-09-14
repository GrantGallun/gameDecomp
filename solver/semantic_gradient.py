"""Translate dynamic memory divergences into typed source-level search axes.

The differential runner can prove that a target value came from (for example)
``lh player+0x2f6`` while the candidate used ``lh player+0x2f0``.  A language
model still has to guess which C lvalue emitted the latter instruction.  This
module closes that gap for the common isolated-candidate forms used by the
project: struct members, explicit offset macros, and raw byte-offset lvalues.

The result is deliberately a *gradient*, not an asserted repair.  Each variant
changes one typed access (or one small compatible set), is compiled, and must
improve the ordinary unintervened semantic suite before a controller accepts
it.  No target source is consulted.
"""

from __future__ import annotations

from dataclasses import dataclass
import difflib
import itertools
import re

from solver import cfg, source_layout


_MEMORY_REF = re.compile(
    r"\b(?P<op>lbu|lhu|lb|lh|lw)\s+"
    r"(?P<region>[&A-Za-z_][\w.&]*)(?:\+0x(?P<offset>[0-9a-f]+))?"
    r"/(?P<width>[1248])\b",
    re.I,
)
_POINTER = re.compile(
    r"^(?P<region>[&A-Za-z_][\w.&]*)(?:\+0x(?P<offset>[0-9a-f]+))?$",
    re.I,
)
_STRUCT_ACCESS = re.compile(
    r"\b(?P<base>[A-Za-z_]\w*)\s*->\s*(?P<field>[A-Za-z_]\w*)"
)
_MACRO_DEFINITION = re.compile(
    r"(?m)^[ \t]*#define[ \t]+(?P<name>[A-Za-z_]\w*)"
    r"\((?P<param>[A-Za-z_]\w*)\)[ \t]+(?P<body>[^\r\n]+)"
)
_RAW_ACCESS = re.compile(
    r"\(\s*\*\s*\(\s*(?P<type>[A-Za-z_]\w*)\s*\*\s*\)\s*"
    r"\(\s*\(\s*(?:char|u8)\s*\*\s*\)\s*"
    r"\(?\s*(?P<base>[A-Za-z_]\w*)\s*\)?\s*\+\s*"
    r"(?P<offset>0x[0-9a-fA-F]+|[0-9]+)\s*\)\s*\)"
)
_ASSIGNMENT = re.compile(
    r"^\s*(?P<op>\+\+|--|<<=|>>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|=(?!=))"
)
_PREFIX_UPDATE = re.compile(r"(?:\+\+|--)\s*$")

_TYPE_INFO = {
    "char": (1, True), "signed char": (1, True),
    "unsigned char": (1, False), "s8": (1, True), "u8": (1, False),
    "short": (2, True), "signed short": (2, True),
    "unsigned short": (2, False), "s16": (2, True), "u16": (2, False),
    "int": (4, True), "signed int": (4, True),
    "unsigned int": (4, False), "long": (4, True),
    "signed long": (4, True), "unsigned long": (4, False),
    "s32": (4, True), "u32": (4, False), "float": (4, True),
}
_LOAD_TYPES = {
    "lb": "s8", "lbu": "u8", "lh": "s16", "lhu": "u16", "lw": "s32",
}


@dataclass(frozen=True)
class AccessConstraint:
    """One observed candidate access that should use a target location/type."""

    role: str
    region: str
    candidate_offset: int
    target_offset: int
    width: int
    target_type: str
    support: int = 1
    evidence: str = ""


@dataclass(frozen=True)
class AccessSite:
    start: int
    end: int
    text: str
    base: str
    offset: int
    width: int
    signed: bool
    role: str
    form: str


@dataclass(frozen=True)
class GradientVariant:
    source: str
    label: str
    edits: tuple[str, ...]


@dataclass(frozen=True)
class ExecutedLoad:
    op: str
    region: str
    offset: int
    width: int
    instruction: int
    trace_position: int


@dataclass(frozen=True)
class MissingReadConstraint:
    region: str
    target_offset: int
    width: int
    target_type: str
    support: int = 1
    evidence: str = ""


@dataclass(frozen=True)
class ValueSite:
    start: int
    end: int
    name: str
    line: int
    output_base: str


@dataclass(frozen=True)
class ValueNode:
    """One node in a concrete dynamic register-definition DAG."""

    kind: str
    op: str
    token: str = ""
    region: str = ""
    offset: int = 0
    width: int = 0
    instruction: int = -1
    trace_position: int = -1
    write_version: int = -1
    children: tuple["ValueNode", ...] = ()

    def signature(self) -> tuple:
        if self.kind == "memory":
            return (self.kind, self.op, self.region, self.offset, self.width,
                    self.write_version)
        if self.kind in {"entry", "constant", "unknown"}:
            return (self.kind, self.token)
        return (self.kind, self.op,
                tuple(child.signature() for child in self.children))

    def summary(self) -> str:
        if self.kind == "memory":
            version = ("initial" if self.write_version < 0 else
                       f"after-write#{self.write_version}")
            return (f"{self.op} {self.region}+0x{self.offset:x}/"
                    f"{self.width} ({version})")
        if self.kind in {"entry", "constant", "unknown"}:
            return f"{self.kind} {self.token}"
        return f"{self.op}({', '.join(child.summary() for child in self.children)})"


@dataclass(frozen=True)
class ValueMismatch:
    kind: str
    path: tuple[int, ...]
    target: ValueNode
    candidate: ValueNode
    support: int = 1

    @property
    def evidence(self) -> str:
        path = ".".join(str(part) for part in self.path) or "root"
        return (f"value path {path}: target {self.target.summary()}; "
                f"candidate {self.candidate.summary()}")


def _pointer(text: str) -> tuple[str, int] | None:
    match = _POINTER.fullmatch(text)
    if match is None:
        return None
    return match.group("region"), int(match.group("offset") or "0", 16)


def _memory_refs(provenance: str) -> list[tuple[str, str, int, int]]:
    return [
        (match.group("op").lower(), match.group("region"),
         int(match.group("offset") or "0", 16), int(match.group("width")))
        for match in _MEMORY_REF.finditer(provenance)
    ]


_REGISTER_NAMES = frozenset(set(
    "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 t8 t9 "
    "s0 s1 s2 s3 s4 s5 s6 s7 s8 k0 k1 gp sp fp ra lo hi".split()))
_DESTINATION_FIRST = frozenset({
    "move", "li", "lui", "addiu", "addi", "addu", "add", "subu", "sub",
    "or", "and", "xor", "ori", "andi", "xori", "sll", "srl", "sra",
    "negu", "slti", "sltiu", "slt", "sltu", "mflo", "mfhi",
    "lb", "lbu", "lh", "lhu", "lw",
})
_COMMUTATIVE = frozenset({"add", "or", "and", "xor", "mult", "multu"})


def _instruction(text: str) -> tuple[str, tuple[str, ...]]:
    match = cfg.INSTRUCTION.match(cfg.clean_line(text))
    if match is None:
        return "", ()
    return match.group(1).lower(), cfg.split_operands(match.group(2))


def _register(token: str) -> str | None:
    name = token.strip().lstrip("$")
    return name if name in _REGISTER_NAMES else None


def _constant_node(token: str) -> ValueNode:
    try:
        value = int(token, 0)
        normalized = f"{value:#x}"
    except ValueError:
        normalized = token.strip()
    return ValueNode("constant", "constant", normalized)


def _load_node(run, event, op: str) -> ValueNode:
    effect = re.search(
        r"\bload\s+([&A-Za-z_][\w.&]*)"
        r"(?:\+0x([0-9a-f]+))?/([1248])=", event.effect, re.I)
    if effect is None:
        return ValueNode(
            "unknown", op, event.text, instruction=event.instruction,
            trace_position=event.ordinal)
    region = effect.group(1)
    offset = int(effect.group(2) or "0", 16)
    width = int(effect.group(3))
    version = -1
    address = region if offset == 0 else f"{region}+0x{offset:x}"
    for write in run.writes:
        if write.trace_position >= event.ordinal:
            break
        if write.address == address and write.width == width:
            version = write.ordinal
    return ValueNode(
        "memory", op, region=region, offset=offset, width=width,
        instruction=event.instruction, trace_position=event.ordinal,
        write_version=version)


def _last_definition(run, register: str, before: int):
    for index in range(min(before,len(run.trace))-1,-1,-1):
        event = run.trace[index]
        if any(name == register for name, _value, _origin in event.writes):
            return event
    return None


def _resolve_register(run, register: str, before: int,
                      memo: dict[tuple[str, int], ValueNode],
                      visiting: set[tuple[str, int]]) -> ValueNode:
    key = (register, before)
    if key in memo:
        return memo[key]
    if key in visiting:
        return ValueNode("unknown", "cycle", register)
    if len(visiting) >= 64 or len(memo) >= 4096:
        return ValueNode("unknown", "diagnostic-traversal-budget", register)
    visiting.add(key)
    event = _last_definition(run, register, before)
    if event is None:
        node = ValueNode("entry", "entry", register)
    else:
        op, operands = _instruction(event.text)
        if op in _LOAD_TYPES:
            node = _load_node(run, event, op)
        elif op == "move" and len(operands) == 2:
            source = _register(operands[1])
            node = (_resolve_register(run, source, event.ordinal, memo, visiting)
                    if source else _constant_node(operands[1]))
        elif op in {"mflo", "mfhi"}:
            special = "lo" if op == "mflo" else "hi"
            node = _resolve_register(
                run, special, event.ordinal, memo, visiting)
        elif op == "li" and len(operands) == 2:
            node = _constant_node(operands[1])
        else:
            if op in {"mult", "multu"}:
                source_operands = operands
            elif op in {"div", "divu"}:
                source_operands = operands[-2:]
            elif op in _DESTINATION_FIRST:
                source_operands = operands[1:]
            else:
                source_operands = tuple(
                    name for name, _value, _origin in event.reads)
            children = []
            for operand in source_operands:
                source = _register(operand)
                children.append(
                    _resolve_register(run, source, event.ordinal, memo, visiting)
                    if source else _constant_node(operand))
            node = ValueNode(
                "operation", op or "unknown", instruction=event.instruction,
                trace_position=event.ordinal, children=tuple(children))
    visiting.remove(key)
    memo[key] = node
    return node


def value_dag(run, write) -> ValueNode:
    """Reconstruct the dynamic value feeding one persistent store."""
    if not 0 <= write.trace_position < len(run.trace):
        return ValueNode("unknown", "missing-store-trace", write.address)
    event = run.trace[write.trace_position]
    op, operands = _instruction(event.text)
    if op not in {"sb", "sh", "sw"} or not operands:
        return ValueNode("unknown", "not-a-store", event.text)
    source = _register(operands[0])
    if source is None:
        return _constant_node(operands[0])
    return _resolve_register(run, source, event.ordinal, {}, set())


def _normalized_op(op: str) -> str:
    return {
        "addu": "add", "addiu": "add", "addi": "add",
        "subu": "sub", "multu": "mult",
    }.get(op, op)


def align_value_dags(target: ValueNode, candidate: ValueNode,
                     path: tuple[int, ...] = ()) -> tuple[ValueMismatch, ...]:
    """Align equal operation shapes and expose their differing leaves."""
    if target.signature() == candidate.signature():
        return ()
    if target.kind == "memory" and candidate.kind == "memory":
        kind = ("stale-memory-version"
                if (target.region, target.offset, target.width) ==
                   (candidate.region, candidate.offset, candidate.width)
                else "memory-binding")
        return (ValueMismatch(kind, path, target, candidate),)
    if target.kind == "memory" and candidate.kind == "entry":
        return (ValueMismatch(
            "entry-to-memory", path, target, candidate),)
    if target.kind == "entry" and candidate.kind == "memory":
        return (ValueMismatch(
            "memory-to-entry", path, target, candidate),)
    if target.kind == "operation" and candidate.kind == "operation" and \
            _normalized_op(target.op) == _normalized_op(candidate.op) and \
            len(target.children) == len(candidate.children):
        direct = tuple(
            mismatch
            for index, (left, right) in enumerate(zip(
                target.children, candidate.children))
            for mismatch in align_value_dags(left, right, path + (index,))
        )
        if _normalized_op(target.op) not in _COMMUTATIVE or \
                len(target.children) != 2:
            return direct
        swapped = tuple(
            mismatch
            for index, (left, right) in enumerate(zip(
                target.children, reversed(candidate.children)))
            for mismatch in align_value_dags(left, right, path + (index,))
        )
        direct_distance = sum(
            value_distance_nodes(left, right)
            for left, right in zip(target.children, candidate.children))
        swapped_distance = sum(
            value_distance_nodes(left, right)
            for left, right in zip(target.children,
                                   reversed(candidate.children)))
        return swapped if swapped_distance < direct_distance else direct
    return (ValueMismatch("operation-shape", path, target, candidate),)


def value_mismatches_from_result(result) -> tuple[ValueMismatch, ...]:
    target_write, candidate_write = _first_aligned_write_pair(result)
    if target_write is None or candidate_write is None:
        return ()
    return align_value_dags(
        value_dag(result.target, target_write),
        value_dag(result.candidate, candidate_write))


def infer_value_mismatches(results) -> tuple[ValueMismatch, ...]:
    """Aggregate stable dynamic value-leaf differences across test cases."""
    counts: dict[tuple, int] = {}
    examples: dict[tuple, ValueMismatch] = {}
    for result in results:
        for mismatch in value_mismatches_from_result(result):
            key = (mismatch.kind, mismatch.path,
                   mismatch.target.signature(), mismatch.candidate.signature())
            counts[key] = counts.get(key, 0) + 1
            examples.setdefault(key, mismatch)
    ordered = sorted(counts, key=lambda key: (-counts[key], key[0], key[1]))
    return tuple(ValueMismatch(
        examples[key].kind, examples[key].path, examples[key].target,
        examples[key].candidate, counts[key]) for key in ordered)


def value_mismatch_dict(mismatch: ValueMismatch) -> dict:
    return {
        "kind": mismatch.kind,
        "path": list(mismatch.path),
        "target": mismatch.target.summary(),
        "candidate": mismatch.candidate.summary(),
        "support": mismatch.support,
        "evidence": mismatch.evidence,
    }


def _value_tree_lines(node: ValueNode, prefix: str = "", *,
                      max_depth: int = 18) -> list[str]:
    if max_depth <= 0:
        return [prefix + "..."]
    if node.kind == "operation":
        lines = [prefix + node.op]
        for index, child in enumerate(node.children):
            lines.append(prefix + f"  [{index}]")
            lines.extend(_value_tree_lines(
                child, prefix + "    ", max_depth=max_depth - 1))
        return lines
    return [prefix + node.summary()]


def render_operation_gradient(results, source: str, *,
                              max_clusters: int = 8) -> str:
    """Render path-conditioned value-DAG differences for a repair agent.

    The text intentionally carries several mismatch shapes at once.  A single
    trace can make a branch-controlled expression look unconditional; support
    counts across cases expose which shapes are common and which occur only on
    boundary paths.
    """
    mismatches = infer_value_mismatches(results)
    output = divergent_output(results)
    lines = [
        "DYNAMIC OPERATION-DAG GRADIENT",
        ("These are executed value-definition trees for the first differing "
         "persistent write. They are path-conditioned observations, not "
         "finished target C. Support is the number of cases with that exact "
         "tree mismatch."),
    ]
    if output is not None:
        lines.append(
            f"Candidate source output under repair: byte offset 0x{output[0]:x}, "
            f"width {output[1]}.")
        sites = dependency_sites(source, *output)
        source_lines = source.splitlines()
        relevant = sorted({site.line for site in sites})
        if relevant:
            lines.append("Candidate C def-use cone (exact current lines):")
            for number in relevant[:32]:
                if 1 <= number <= len(source_lines):
                    lines.append(f"  {number}: {source_lines[number - 1]}")
    if not mismatches:
        lines.append("No alignable differing write-value DAG was available.")
        return "\n".join(lines)
    for index, mismatch in enumerate(mismatches[:max_clusters], 1):
        path = ".".join(str(part) for part in mismatch.path) or "root"
        lines.extend([
            "",
            (f"CLUSTER {index}: kind={mismatch.kind}; support="
             f"{mismatch.support}/{len(results)}; value-path={path}"),
            "TARGET EXECUTED VALUE TREE:",
        ])
        lines.extend("  " + row for row in _value_tree_lines(mismatch.target))
        lines.append("CANDIDATE EXECUTED VALUE TREE:")
        lines.extend(
            "  " + row for row in _value_tree_lines(mismatch.candidate))
    lines.extend([
        "",
        ("Repair implication: preserve the already aligned outer tree, then "
         "rewrite the smallest C def-use subexpression corresponding to the "
         "first mismatching value path. If clusters disagree, infer the "
         "branch condition that selects their target trees; do not collapse "
         "them into one unconditional expression."),
    ])
    return "\n".join(lines)


def _node_size(node: ValueNode) -> int:
    return 1 + sum(_node_size(child) for child in node.children)


def value_distance_nodes(target: ValueNode, candidate: ValueNode) -> int:
    """Structural leaf distance used only to rank a causally valid beam."""
    if target.signature() == candidate.signature():
        return 0
    if target.kind == "operation" and candidate.kind == "operation" and \
            _normalized_op(target.op) == _normalized_op(candidate.op) and \
            len(target.children) == len(candidate.children):
        direct = sum(value_distance_nodes(left, right)
                     for left, right in zip(target.children,
                                            candidate.children))
        if _normalized_op(target.op) in _COMMUTATIVE and \
                len(target.children) == 2:
            swapped = sum(value_distance_nodes(left, right)
                          for left, right in zip(
                              target.children, reversed(candidate.children)))
            return min(direct, swapped)
        return direct
    if target.kind != "operation" and candidate.kind != "operation":
        return 1
    return 1 + _node_size(target) + _node_size(candidate)


def value_distance(result) -> int:
    target_write, candidate_write = _first_aligned_write_pair(result)
    if target_write is None or candidate_write is None:
        return 0
    return value_distance_nodes(
        value_dag(result.target, target_write),
        value_dag(result.candidate, candidate_write))


def suite_value_distance(results) -> int:
    return sum(value_distance(result) for result in results)


def constraints_from_write_pair(target, candidate) -> tuple[AccessConstraint, ...]:
    """Infer address and input-dependency axes from paired dynamic writes."""
    if target is None or candidate is None:
        return ()
    target_pointer = _pointer(target.address)
    candidate_pointer = _pointer(candidate.address)
    if target_pointer is None or candidate_pointer is None:
        return ()
    target_region, target_offset = target_pointer
    candidate_region, candidate_offset = candidate_pointer
    constraints: list[AccessConstraint] = []
    if target_region == candidate_region and target.width == candidate.width and \
            target_offset != candidate_offset:
        constraints.append(AccessConstraint(
            "write", candidate_region, candidate_offset, target_offset,
            target.width, f"s{target.width * 8}", evidence=(
                f"candidate writes {candidate.address}/{candidate.width}; "
                f"target writes {target.address}/{target.width}")))

    target_refs = _memory_refs(target.value_provenance)
    candidate_refs = _memory_refs(candidate.value_provenance)
    if len(target_refs) == len(candidate_refs):
        for target_ref, candidate_ref in zip(target_refs, candidate_refs):
            target_op, target_region, target_offset, target_width = target_ref
            _candidate_op, candidate_region, candidate_offset, candidate_width = \
                candidate_ref
            if target_region != candidate_region or \
                    target_width != candidate_width or \
                    target_offset == candidate_offset:
                continue
            constraints.append(AccessConstraint(
                "read", candidate_region, candidate_offset, target_offset,
                target_width, _LOAD_TYPES[target_op], evidence=(
                    f"candidate value reads {candidate_region}+"
                    f"0x{candidate_offset:x}/{candidate_width}; target value "
                    f"reads {target_region}+0x{target_offset:x}/{target_width}")))
    return tuple(dict.fromkeys(constraints))


def _phase_loads(run, write) -> tuple[ExecutedLoad, ...]:
    """Loads in the executed prefix ending at this persistent write.

    Do not cut the prefix at the prior store.  IDO may legally hoist an input
    load above that store on one side while leaving it below on the other; an
    interval comparison would then erase the dependency instead of aligning
    it.  Multiset sequence alignment removes earlier shared loads while
    retaining hoisted target/candidate-only inputs.
    """
    out: list[ExecutedLoad] = []
    for event in run.trace[:write.trace_position + 1]:
        match = re.match(r"\s*(lbu|lhu|lb|lh|lw)\b", event.text, re.I)
        effect = re.search(
            r"\bload\s+([&A-Za-z_][\w.&]*)"
            r"(?:\+0x([0-9a-f]+))?/([1248])=", event.effect, re.I)
        if match is None or effect is None:
            continue
        out.append(ExecutedLoad(
            match.group(1).lower(), effect.group(1),
            int(effect.group(2) or "0", 16), int(effect.group(3)),
            event.instruction, event.ordinal))
    return tuple(out)


def phase_load_delta(result) -> tuple[tuple[ExecutedLoad, ...],
                                      tuple[ExecutedLoad, ...]]:
    """Return target-only and candidate-only loads for the bad write phase."""
    target_write, candidate_write = _first_aligned_write_pair(result)
    if target_write is None or candidate_write is None:
        return (), ()
    target = list(_phase_loads(result.target, target_write))
    candidate = list(_phase_loads(result.candidate, candidate_write))
    target_keys = [(row.op, row.region, row.offset, row.width) for row in target]
    candidate_keys = [
        (row.op, row.region, row.offset, row.width) for row in candidate
    ]
    matcher = difflib.SequenceMatcher(
        None, target_keys, candidate_keys, autojunk=False)
    target_only: list[ExecutedLoad] = []
    candidate_only: list[ExecutedLoad] = []
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        target_only.extend(target[left_start:left_end])
        candidate_only.extend(candidate[right_start:right_end])
    return tuple(target_only), tuple(candidate_only)


def constraints_from_result(result) -> tuple[AccessConstraint, ...]:
    """Combine direct provenance and phase-level load-set gradients."""
    target_write, candidate_write = _first_aligned_write_pair(result)
    constraints = list(constraints_from_write_pair(
        target_write, candidate_write))
    target_only, candidate_only = phase_load_delta(result)
    for candidate in candidate_only:
        for target in target_only:
            if candidate.region != target.region or \
                    candidate.width != target.width or \
                    candidate.op != target.op or \
                    candidate.offset == target.offset:
                continue
            constraints.append(AccessConstraint(
                "read", candidate.region, candidate.offset, target.offset,
                target.width, _LOAD_TYPES[target.op], evidence=(
                    f"candidate phase has extra {candidate.op} "
                    f"{candidate.region}+0x{candidate.offset:x}/"
                    f"{candidate.width}; target phase instead loads "
                    f"{target.region}+0x{target.offset:x}/{target.width}")))
    return tuple(dict.fromkeys(constraints))


def _first_aligned_write_pair(result):
    left_ids = [
        (row.address, row.width, row.value) for row in result.target.writes
    ]
    right_ids = [
        (row.address, row.width, row.value) for row in result.candidate.writes
    ]
    matcher = difflib.SequenceMatcher(None, left_ids, right_ids, autojunk=False)
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        left = result.target.writes[left_start] if left_start < left_end else None
        right = (result.candidate.writes[right_start]
                 if right_start < right_end else None)
        return left, right
    return None, None


def infer_constraints(results) -> tuple[AccessConstraint, ...]:
    """Aggregate source-free access constraints across concrete test cases."""
    counts: dict[tuple, int] = {}
    examples: dict[tuple, AccessConstraint] = {}
    for result in results:
        for constraint in constraints_from_result(result):
            key = (
                constraint.role, constraint.region,
                constraint.candidate_offset, constraint.target_offset,
                constraint.width, constraint.target_type,
            )
            counts[key] = counts.get(key, 0) + 1
            examples.setdefault(key, constraint)
    ordered = sorted(
        counts, key=lambda key: (-counts[key], key[0], key[2], key[3]))
    return tuple(AccessConstraint(
        examples[key].role, examples[key].region,
        examples[key].candidate_offset, examples[key].target_offset,
        examples[key].width, examples[key].target_type, counts[key],
        examples[key].evidence) for key in ordered)


def infer_missing_reads(results) -> tuple[MissingReadConstraint, ...]:
    """Target loads absent from the candidate prefix of the divergent write."""
    counts: dict[tuple, int] = {}
    examples: dict[tuple, ExecutedLoad] = {}
    for result in results:
        target_only, _candidate_only = phase_load_delta(result)
        for row in target_only:
            key = (row.region, row.offset, row.width, _LOAD_TYPES[row.op])
            counts[key] = counts.get(key, 0) + 1
            examples.setdefault(key, row)
    ordered = sorted(counts, key=lambda key: (-counts[key], key[1], key[2]))
    return tuple(MissingReadConstraint(
        key[0], key[1], key[2], key[3], counts[key],
        f"target divergent-write prefix executes {examples[key].op} "
        f"{key[0]}+0x{key[1]:x}/{key[2]} with no aligned candidate load")
        for key in ordered)


def _type_info(type_name: str) -> tuple[int, bool] | None:
    canonical = " ".join(type_name.replace("const", "").split())
    return _TYPE_INFO.get(canonical)


def _role(source: str, start: int, end: int) -> str:
    line_start = source.rfind("\n", 0, start) + 1
    line_end = source.find("\n", end)
    if line_end < 0:
        line_end = len(source)
    prefix = source[line_start:start]
    suffix = source[end:line_end]
    if _PREFIX_UPDATE.search(prefix):
        return "readwrite"
    assignment = _ASSIGNMENT.match(suffix)
    if assignment is None:
        return "read"
    return "write" if assignment.group("op") == "=" else "readwrite"


def access_sites(source: str) -> tuple[AccessSite, ...]:
    """Locate typed candidate lvalues whose compiled offsets are knowable."""
    sites: list[AccessSite] = []
    occupied: set[tuple[int, int]] = set()
    fields: dict[str, list] = {}
    for layout in source_layout.layouts(source):
        for field in layout.fields:
            fields.setdefault(field.name, []).append(field)
    for match in _STRUCT_ACCESS.finditer(source):
        choices = fields.get(match.group("field"), [])
        if len(choices) != 1:
            continue
        field = choices[0]
        info = _type_info(field.type_name)
        if info is None or info[0] != field.size:
            continue
        sites.append(AccessSite(
            match.start(), match.end(), match.group(0), match.group("base"),
            field.offset, field.size, info[1],
            _role(source, match.start(), match.end()), "struct-member"))
        occupied.add((match.start(), match.end()))

    definitions: dict[str, tuple[int, bool, int, int]] = {}
    definition_ranges: list[tuple[int, int]] = []
    for match in _MACRO_DEFINITION.finditer(source):
        definition_ranges.append((match.start(), match.end()))
        cast = re.search(
            r"\(\s*\*\s*\(\s*([A-Za-z_]\w*)\s*\*\s*\)",
            match.group("body"))
        offsets = list(re.finditer(
            r"\+\s*(0x[0-9a-fA-F]+|[0-9]+)", match.group("body")))
        if cast is None or not offsets:
            continue
        info = _type_info(cast.group(1))
        if info is None:
            continue
        definitions[match.group("name")] = (
            info[0], info[1], int(offsets[-1].group(1), 0), match.start())
    for name, (width, signed, offset, _definition_start) in definitions.items():
        invocation = re.compile(
            rf"\b{re.escape(name)}\s*\(\s*(?P<base>[A-Za-z_]\w*)\s*\)")
        for match in invocation.finditer(source):
            if any(start <= match.start() < end
                   for start, end in definition_ranges):
                continue
            sites.append(AccessSite(
                match.start(), match.end(), match.group(0), match.group("base"),
                offset, width, signed,
                _role(source, match.start(), match.end()), "offset-macro"))
            occupied.add((match.start(), match.end()))

    for match in _RAW_ACCESS.finditer(source):
        if any(start <= match.start() < end
               for start, end in definition_ranges):
            continue
        if (match.start(), match.end()) in occupied:
            continue
        info = _type_info(match.group("type"))
        if info is None:
            continue
        sites.append(AccessSite(
            match.start(), match.end(), match.group(0), match.group("base"),
            int(match.group("offset"), 0), info[0], info[1],
            _role(source, match.start(), match.end()), "raw-offset"))
    return tuple(sorted(sites, key=lambda site: site.start))


_DECLARATION = re.compile(
    r"\b(?:s8|u8|s16|u16|s32|u32|char|short|int|long|float|double|void|"
    r"struct\s+[A-Za-z_]\w*|[A-Z][A-Za-z_]\w*)\s*\**\s*"
    r"(?P<name>[A-Za-z_]\w*)\b"
)
_LOCAL_ASSIGNMENT = re.compile(
    r"(?P<lhs>\b[A-Za-z_]\w*)\s*=\s*(?P<rhs>[^;{}]*);", re.S)
_IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")


def _identifier_sites(text: str, absolute_start: int, declared: set[str],
                      access_ranges: tuple[tuple[int, int], ...],
                      output_base: str) -> list[ValueSite]:
    out: list[ValueSite] = []
    for match in _IDENTIFIER.finditer(text):
        name = match.group(0)
        start, end = absolute_start + match.start(), absolute_start + match.end()
        if name not in declared or name == output_base:
            continue
        if any(left <= start < right for left, right in access_ranges):
            continue
        suffix = text[match.end():]
        if re.match(r"\s*\(", suffix):
            continue
        out.append(ValueSite(
            start, end, name, 0, output_base))
    return out


def dependency_sites(source: str, output_offset: int,
                     output_width: int) -> tuple[ValueSite, ...]:
    """Over-approximate scalar RHS sites feeding a divergent memory output.

    This is intentionally a small C89 def-use slicer, not a C parser.  Every
    emitted site remains only a compile-and-verify choice.  Ambiguous branches
    add alternatives; they never become semantic claims.
    """
    accesses = access_sites(source)
    outputs = [site for site in accesses
               if site.offset == output_offset and site.width == output_width
               and site.role in {"write", "readwrite"}]
    if not outputs:
        return ()
    # Prefer the latest source store when repeated writes share an address;
    # dynamic ordinal-to-source correlation is a later, stronger gradient.
    output = outputs[-1]
    semicolon = source.find(";", output.end)
    if semicolon < 0:
        return ()
    equals = re.search(r"=(?!=)", source[output.end:semicolon])
    if equals is None:
        return ()
    rhs_start = output.end + equals.end()
    rhs_end = semicolon
    declared = {match.group("name") for match in _DECLARATION.finditer(source)}
    access_ranges = tuple((site.start, site.end) for site in accesses)

    definitions: dict[str, list[tuple[int, int]]] = {}
    for match in _LOCAL_ASSIGNMENT.finditer(source[:output.start]):
        lhs_start = match.start("lhs")
        if source[max(0, lhs_start - 2):lhs_start] == "->":
            continue
        lhs = match.group("lhs")
        if lhs not in declared:
            continue
        definitions.setdefault(lhs, []).append(
            (match.start("rhs"), match.end("rhs")))

    root = _identifier_sites(
        source[rhs_start:rhs_end], rhs_start, declared, access_ranges,
        output.base)
    found: dict[tuple[int, int], ValueSite] = {
        (site.start, site.end): site for site in root
    }
    pending = [site.name for site in root]
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        for start, end in definitions.get(name, []):
            for site in _identifier_sites(
                    source[start:end], start, declared, access_ranges,
                    output.base):
                key = (site.start, site.end)
                if key not in found:
                    found[key] = site
                    pending.append(site.name)
    return tuple(ValueSite(
        site.start, site.end, site.name,
        source.count("\n", 0, site.start) + 1, site.output_base)
        for site in sorted(found.values(), key=lambda row: row.start))


def divergent_output(results) -> tuple[int, int] | None:
    """Stable candidate output location for the first aligned bad write."""
    locations = []
    for result in results:
        target, candidate = _first_aligned_write_pair(result)
        if target is None or candidate is None:
            continue
        pointer = _pointer(candidate.address)
        if pointer is None:
            continue
        locations.append((pointer[1], candidate.width))
    if not locations or len(set(locations)) != 1:
        return None
    return locations[0]


def _replacement(site: AccessSite, constraint: AccessConstraint) -> str:
    type_name = constraint.target_type
    if constraint.role == "write":
        type_name = ("s" if site.signed else "u") + str(site.width * 8)
    return (
        f"(*({type_name} *)((char *)({site.base}) + "
        f"0x{constraint.target_offset:x}))")


def _compatible(site: AccessSite, constraint: AccessConstraint) -> bool:
    if site.offset != constraint.candidate_offset or \
            site.width != constraint.width:
        return False
    if constraint.role == "write":
        return site.role in {"write", "readwrite"}
    return site.role in {"read", "readwrite"}


def _apply(source: str, edits: tuple[tuple[AccessSite, AccessConstraint], ...]
           ) -> GradientVariant:
    rendered = []
    out = source
    for site, constraint in sorted(edits, key=lambda item: item[0].start,
                                   reverse=True):
        replacement = _replacement(site, constraint)
        out = out[:site.start] + replacement + out[site.end:]
        line = source.count("\n", 0, site.start) + 1
        rendered.append(
            f"line {line} {constraint.role} {site.form}: "
            f"0x{site.offset:x}->0x{constraint.target_offset:x}/"
            f"{constraint.width}")
    rendered.reverse()
    return GradientVariant(out, "; ".join(rendered), tuple(rendered))


def rebinding_variants(source: str,
                       constraints: tuple[AccessConstraint, ...], *,
                       max_variants: int = 32,
                       max_joint_constraints: int = 3
                       ) -> tuple[GradientVariant, ...]:
    """Enumerate bounded typed lvalue rebindings for a controller to verify."""
    sites = access_sites(source)
    choices: list[tuple[AccessConstraint, tuple[AccessSite, ...]]] = []
    for constraint in constraints:
        matching = tuple(site for site in sites if _compatible(site, constraint))
        if matching:
            choices.append((constraint, matching))

    variants: list[GradientVariant] = []
    seen: set[str] = {source}

    def add(edits: tuple[tuple[AccessSite, AccessConstraint], ...]) -> None:
        if len(variants) >= max_variants:
            return
        spans = [(site.start, site.end) for site, _constraint in edits]
        if len(set(spans)) != len(spans):
            return
        variant = _apply(source, edits)
        if variant.source in seen:
            return
        seen.add(variant.source)
        variants.append(variant)

    for constraint, matching in choices:
        for site in matching:
            add(((site, constraint),))

    limit = min(max_joint_constraints, len(choices))
    for size in range(2, limit + 1):
        for selected in itertools.combinations(choices, size):
            for selected_sites in itertools.product(
                    *(matching for _constraint, matching in selected)):
                edits = tuple(
                    (site, selected[index][0])
                    for index, site in enumerate(selected_sites))
                add(edits)
                if len(variants) >= max_variants:
                    return tuple(variants)
    return tuple(variants)


def dependency_injection_variants(
        source: str, missing: tuple[MissingReadConstraint, ...],
        output_offset: int, output_width: int, *, max_variants: int = 64
        ) -> tuple[GradientVariant, ...]:
    """Substitute target-only memory dependencies into the C def-use cone."""
    sites = dependency_sites(source, output_offset, output_width)
    variants: list[GradientVariant] = []
    seen = {source}
    for constraint in missing:
        for site in sites:
            replacement = (
                f"(*({constraint.target_type} *)((char *)({site.output_base}) + "
                f"0x{constraint.target_offset:x}))")
            out = source[:site.start] + replacement + source[site.end:]
            if out in seen:
                continue
            seen.add(out)
            label = (
                f"line {site.line} value `{site.name}` -> target-only "
                f"{constraint.region}+0x{constraint.target_offset:x}/"
                f"{constraint.width}")
            variants.append(GradientVariant(out, label, (label,)))
            if len(variants) >= max_variants:
                return tuple(variants)
    return tuple(variants)


def _local_access_bindings(source: str) -> dict[str, tuple[int, int]]:
    """Simple locals initialized or assigned from one known memory access."""
    accesses = access_sites(source)
    declared = {match.group("name") for match in _DECLARATION.finditer(source)}
    bindings: dict[str, tuple[int, int]] = {}
    for match in _LOCAL_ASSIGNMENT.finditer(source):
        lhs = match.group("lhs")
        if lhs not in declared:
            continue
        contained = [site for site in accesses
                     if match.start("rhs") <= site.start and
                     site.end <= match.end("rhs") and site.role == "read"]
        if len(contained) == 1:
            bindings[lhs] = (contained[0].offset, contained[0].width)
    return bindings


def _replace_value_sites(source: str, sites: tuple[ValueSite, ...],
                         target: ValueNode, label_prefix: str
                         ) -> GradientVariant:
    target_type = _LOAD_TYPES.get(target.op, f"s{target.width * 8}")
    replacement = (
        f"(*({target_type} *)((char *)({sites[0].output_base}) + "
        f"0x{target.offset:x}))")
    out = source
    labels = []
    for site in sorted(sites, key=lambda row: row.start, reverse=True):
        out = out[:site.start] + replacement + out[site.end:]
        labels.append(
            f"line {site.line} value `{site.name}` -> {target.summary()}")
    labels.reverse()
    return GradientVariant(
        out, label_prefix + "; " + "; ".join(labels), tuple(labels))


def dag_guided_variants(
        source: str, mismatches: tuple[ValueMismatch, ...],
        output_offset: int, output_width: int, *, max_variants: int = 96,
        max_joint_occurrences: int = 2) -> tuple[GradientVariant, ...]:
    """Turn aligned value leaves into bounded source def-use substitutions."""
    sites = dependency_sites(source, output_offset, output_width)
    bindings = _local_access_bindings(source)
    groups: dict[tuple, list[ValueMismatch]] = {}
    for mismatch in mismatches:
        if mismatch.target.kind != "memory":
            continue
        if mismatch.kind == "entry-to-memory":
            source_name = mismatch.candidate.token
        elif mismatch.kind == "stale-memory-version":
            matching_names = sorted(
                name for name, binding in bindings.items()
                if binding == (mismatch.candidate.offset,
                               mismatch.candidate.width))
            if not matching_names:
                continue
            # One dynamic leaf may correspond to several source aliases. Keep
            # each as a separate group and let compilation/causal distance rank.
            for source_name in matching_names:
                key = (mismatch.kind, mismatch.target.signature(), source_name)
                groups.setdefault(key, []).append(mismatch)
            continue
        else:
            continue
        key = (mismatch.kind, mismatch.target.signature(), source_name)
        groups.setdefault(key, []).append(mismatch)

    variants: list[GradientVariant] = []
    seen = {source}
    for (kind, _target_signature, source_name), rows in groups.items():
        target = rows[0].target
        matching = tuple(site for site in sites if site.name == source_name)
        if not matching:
            continue
        maximum = min(max_joint_occurrences, len(matching), len(rows))
        # Always retain single-site probes. Multiple aligned occurrences of the
        # same leaf also justify bounded joint replacements (Lean's final a1).
        for size in range(1, maximum + 1):
            for selected in itertools.combinations(matching, size):
                variant = _replace_value_sites(
                    source, selected, target,
                    f"DAG {kind} path-count={len(rows)}")
                if variant.source in seen:
                    continue
                seen.add(variant.source)
                variants.append(variant)
                if len(variants) >= max_variants:
                    return tuple(variants)
    return tuple(variants)
