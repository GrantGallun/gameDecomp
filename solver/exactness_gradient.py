"""Deterministic compiler-residual evidence for byte-exact repair.

The differential debugger answers whether two functions behave alike on the
executed cases.  This module answers a different, static question: what small
set of compiler value-allocation decisions explains the remaining object
diff?  It deliberately stops before claiming which C spelling produced those
decisions.  Source-shape causes are probe candidates until the compiler oracle
accepts them.

No finished/reference C enters this module.  Inputs are the current candidate
source, the target/candidate assembly diff, and prior experiment receipts.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import sqlite3
from typing import Iterable, Mapping, Any

from solver import allocdiff, c89, diffrepair, signals, uopt


_REGISTER = re.compile(
    r"\$?\b(?:zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)\b"
)
_IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")
@dataclass(frozen=True)
class RegisterCorrespondence:
    target: str
    candidate: str
    occurrences: int
    instruction_lines: tuple[int, ...]
    examples: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RegisterCycle:
    """A bijective cycle in aligned target -> candidate physical registers."""

    registers: tuple[str, ...]
    edge_occurrences: tuple[int, ...]


@dataclass(frozen=True)
class AllocationAnchor:
    line: int
    target_register: str
    candidate_register: str
    affected_pairs: tuple[tuple[str, str], ...]
    limitation: str = (
        "post-allocation web identity only; the compiler's pre-colouring "
        "coalesced webs are not observable here"
    )


@dataclass(frozen=True)
class ValueEpoch:
    ordinal: int
    start_line: int
    end_line: int
    initializer: str
    initializer_class: str
    brace_depth: int
    use_lines: tuple[int, ...]
    mutation_lines: tuple[int, ...]
    control_use_lines: tuple[int, ...]


@dataclass(frozen=True)
class EpochProbeCandidate:
    local: str
    type_text: str
    declaration_line: int
    score: int
    epochs: tuple[ValueEpoch, ...]
    reason: str = (
        "one C local has multiple lexically distinct overwrite epochs; test "
        "whether splitting compatible CFG value webs changes allocation"
    )


@dataclass(frozen=True)
class ExperimentFamily:
    family: str
    attempted: int
    compiled: int
    accepted: int
    best_score: float | None


@dataclass(frozen=True)
class SourceExperiment:
    label: str
    source: str


@dataclass(frozen=True)
class ExactnessGradient:
    residual_classification: str
    faults: dict[str, int]
    entry_abi_parameters: tuple[str, ...]
    register_correspondences: tuple[RegisterCorrespondence, ...]
    register_cycles: tuple[RegisterCycle, ...]
    allocation_anchor: AllocationAnchor | None
    post_allocation_mismatched_webs: int | None
    epoch_probe_candidates: tuple[EpochProbeCandidate, ...]
    experiment_families: tuple[ExperimentFamily, ...]
    suggested_experiments: tuple[str, ...]
    rejected_observations: tuple[str, ...]
    inference_boundary: str = (
        "register correspondences and lexical source epochs are mechanical; "
        "their causal C interpretation is a hypothesis until compile, exact "
        "object comparison, and semantic replay accept it"
    )

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        lines = [
            "DETERMINISTIC EXACTNESS GRADIENT",
            f"Residual classification: {self.residual_classification}",
            "Changed-line heuristic fault counts (secondary): " + ", ".join(
                f"{kind}={count}" for kind, count in self.faults.items()
                if count
            ) if any(self.faults.values()) else
            "Changed-line heuristic fault counts (secondary): none",
        ]
        if self.entry_abi_parameters:
            lines.append("Entry ABI parameter map (physical register -> C parameter):")
            lines.extend(f"- {item}" for item in self.entry_abi_parameters)
        if self.register_cycles:
            lines.append("Register correspondence cycles (target -> candidate):")
            for cycle in self.register_cycles:
                closed = cycle.registers + cycle.registers[:1]
                edges = ", ".join(
                    f"{closed[index]}->{closed[index + 1]} "
                    f"x{cycle.edge_occurrences[index]}"
                    for index in range(len(cycle.registers))
                )
                lines.append(f"- {edges}")
        if self.register_correspondences:
            lines.append(
                "Aligned opcode examples for register correspondences "
                "(target -> candidate):"
                if self.register_cycles else
                "Register correspondences (target -> candidate):")
            for edge in self.register_correspondences[:10]:
                lines.append(
                    f"- {edge.target}->{edge.candidate} x{edge.occurrences} "
                    f"at aligned instruction lines "
                    f"{','.join(str(line) for line in edge.instruction_lines[:8])}"
                )
                for target, candidate in edge.examples[:2]:
                    lines.append(
                        f"  target `{target}` | candidate `{candidate}`")
        if self.allocation_anchor is not None:
            anchor = self.allocation_anchor
            lines.append(
                "Post-allocation anchor: aligned line "
                f"{anchor.line}, {anchor.target_register}->"
                f"{anchor.candidate_register}; {anchor.limitation}."
            )
            for target, candidate in anchor.affected_pairs[:6]:
                lines.append(
                    f"  target `{target}` | candidate `{candidate}`")
        if self.epoch_probe_candidates:
            lines.append(
                "Source overwrite epochs (mechanical probe candidates, not "
                "claims about original C):"
            )
            for candidate in self.epoch_probe_candidates[:8]:
                rendered = []
                for epoch in candidate.epochs:
                    mutations = (
                        ", mutations " + ",".join(
                            str(line) for line in epoch.mutation_lines)
                        if epoch.mutation_lines else ""
                    )
                    uses = (
                        ", uses " + ",".join(
                            str(line) for line in epoch.use_lines[:12]) +
                        (",..." if len(epoch.use_lines) > 12 else "")
                        if epoch.use_lines else ""
                    )
                    controls = (
                        ", control uses " + ",".join(
                            str(line) for line in epoch.control_use_lines[:8])
                        if epoch.control_use_lines else ""
                    )
                    rendered.append(
                        f"E{epoch.ordinal}@L{epoch.start_line}-"
                        f"{epoch.end_line}/depth{epoch.brace_depth}: "
                        f"`{epoch.initializer}` [{epoch.initializer_class}]"
                        f"{mutations}{uses}{controls}"
                    )
                lines.append(
                    f"- `{candidate.type_text} {candidate.local}` "
                    f"(declaration L{candidate.declaration_line}): "
                    + "; ".join(rendered)
                )
        if self.experiment_families:
            lines.append("Prior compiler experiments in this run:")
            for family in self.experiment_families:
                score = (
                    f", best weighted score={family.best_score:.3f}"
                    if family.best_score is not None else ""
                )
                lines.append(
                    f"- {family.family}: attempted={family.attempted}, "
                    f"compiled={family.compiled}, accepted={family.accepted}"
                    f"{score}"
                )
        if self.suggested_experiments:
            lines.append("Unverified source-shape experiments to test next:")
            lines.extend(f"- {item}" for item in self.suggested_experiments)
        if self.rejected_observations:
            lines.append("Recent rejected observations:")
            lines.extend(f"- {item}" for item in self.rejected_observations[-6:])
        lines.extend([
            "Inference boundary: " + self.inference_boundary + ".",
            "Do not equate a C local's spelling with a physical MIPS register.",
        ])
        return "\n".join(lines)


def _registers(instruction: str) -> tuple[str, ...]:
    return tuple(match.group(0).lstrip("$")
                 for match in _REGISTER.finditer(instruction))


def _erase_registers(instruction: str) -> str:
    return _REGISTER.sub("REG", instruction.strip())


def _classification(diff: str) -> str:
    target, candidate = diffrepair._streams(diff)
    if target and len(target) == len(candidate):
        target_erased = [_erase_registers(row) for row in target]
        candidate_erased = [_erase_registers(row) for row in candidate]
        if target_erased == candidate_erased and target != candidate:
            return "register-operand-only/full-stream"
        if sorted(target_erased) == sorted(candidate_erased):
            return "register-or-local-order-only/full-stream"
    sig = signals.analyse(diff)
    nonzero = [
        name for name, count in (
            ("structural", sig.structural),
            ("layout", sig.layout),
            ("relocation", sig.reloc),
            ("register-allocation", sig.regalloc),
            ("ordering", sig.ordering),
            ("immediate", sig.immediate),
        ) if count
    ]
    return "mixed:" + ",".join(nonzero) if nonzero else "no-static-residual"


def register_correspondences(diff: str) -> tuple[RegisterCorrespondence, ...]:
    """Physical-register correspondences from aligned register-erased rows."""
    target, candidate = diffrepair._streams(diff)
    if not target or len(target) != len(candidate):
        return ()
    counts: Counter[tuple[str, str]] = Counter()
    locations: dict[tuple[str, str], list[int]] = defaultdict(list)
    examples: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for line, (left, right) in enumerate(zip(target, candidate), start=1):
        if _erase_registers(left) != _erase_registers(right):
            continue
        left_registers, right_registers = _registers(left), _registers(right)
        if len(left_registers) != len(right_registers):
            continue
        for target_register, candidate_register in zip(
                left_registers, right_registers):
            if target_register == candidate_register or \
                    target_register not in uopt.COLOR_INDEX or \
                    candidate_register not in uopt.COLOR_INDEX:
                continue
            key = target_register, candidate_register
            counts[key] += 1
            locations[key].append(line)
            pair = (left, right)
            if pair not in examples[key] and len(examples[key]) < 2:
                examples[key].append(pair)
    rows = [
        RegisterCorrespondence(target, candidate, count,
                               tuple(locations[(target, candidate)]),
                               tuple(examples[(target, candidate)]))
        for (target, candidate), count in counts.items()
    ]
    rows.sort(key=lambda row: (-row.occurrences, row.target, row.candidate))
    return tuple(rows)


def entry_abi_parameters(source: str) -> tuple[str, ...]:
    """Map the first four simple C parameters to their o32 entry registers."""
    masked = c89._mask(source)
    headers = re.finditer(
        r"\b(?P<name>[A-Za-z_]\w*)\s*\((?P<parameters>[^()]*)\)\s*\{",
        masked, re.S)
    header = next((row for row in headers if row.group("name") not in {
        "if", "for", "while", "switch"}), None)
    if header is None:
        return ()
    raw_parameters = [part.strip()
                      for part in header.group("parameters").split(",")]
    if raw_parameters == ["void"]:
        return ()
    rows = []
    for register, parameter in zip(("a0", "a1", "a2", "a3"),
                                   raw_parameters):
        names = _IDENTIFIER.findall(parameter)
        if not names:
            return ()
        rows.append(
            f"{register} = C parameter `{names[-1]}` (`{' '.join(parameter.split())}`)"
        )
    return tuple(rows)


def register_cycles(
        correspondences: tuple[RegisterCorrespondence, ...]
        ) -> tuple[RegisterCycle, ...]:
    """Return only unambiguous bijective cycles; ambiguous edges stay rows."""
    outgoing: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, set[str]] = defaultdict(set)
    weights = {}
    for row in correspondences:
        outgoing[row.target].add(row.candidate)
        incoming[row.candidate].add(row.target)
        weights[(row.target, row.candidate)] = row.occurrences
    mapping = {
        target: next(iter(candidates))
        for target, candidates in outgoing.items()
        if len(candidates) == 1 and
        len(incoming[next(iter(candidates))]) == 1
    }
    cycles: list[RegisterCycle] = []
    consumed: set[str] = set()
    for start in sorted(mapping):
        if start in consumed:
            continue
        path: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current in mapping and current not in positions:
            positions[current] = len(path)
            path.append(current)
            current = mapping[current]
        if current not in positions:
            consumed.update(path)
            continue
        cycle = path[positions[current]:]
        consumed.update(path)
        if len(cycle) < 2:
            continue
        # Canonical rotation makes output stable.
        rotations = [cycle[index:] + cycle[:index]
                     for index in range(len(cycle))]
        canonical = min(rotations)
        edge_counts = tuple(
            weights[(canonical[index],
                     canonical[(index + 1) % len(canonical)])]
            for index in range(len(canonical))
        )
        cycles.append(RegisterCycle(tuple(canonical), edge_counts))
    cycles.sort(key=lambda row: (-len(row.registers), row.registers))
    return tuple(cycles)


def _initializer_class(expression: str) -> str:
    expression = " ".join(expression.split())
    stripped = expression.strip("() ")
    if stripped in {"0", "0x0", "NULL"}:
        return "zero"
    if re.fullmatch(r"-?(?:0x[0-9A-Fa-f]+|\d+)", stripped):
        return "literal"
    if re.fullmatch(r"[A-Za-z_]\w*", stripped):
        return "identifier"
    if re.search(r"\w+\s*\(", stripped):
        return "call-or-cast"
    if "->" in stripped or "[" in stripped or "*" in stripped:
        return "memory-or-pointer-expression"
    if re.search(r"[+\-/%|&<>]", stripped):
        return "computed-expression"
    return "other-expression"


def _line_depths(masked_lines: list[str]) -> list[int]:
    depth = 0
    out = []
    for line in masked_lines:
        out.append(depth)
        depth += line.count("{") - line.count("}")
        depth = max(depth, 0)
    return out


def epoch_probe_candidates(
        source: str, limit: int = 8) -> tuple[EpochProbeCandidate, ...]:
    """Find lexical overwrite epochs worth testing as distinct compiler webs.

    This is intentionally not a CFG proof.  Back edges can join the first and
    last lexical epoch, so the output is routed to compile-and-replay probes,
    never applied as an evidence-backed rewrite.
    """
    masked_lines = c89._mask(source).splitlines()
    depths = _line_depths(masked_lines)
    declarations: dict[str, tuple[str, int, str]] = {}
    for index, masked in enumerate(masked_lines):
        match = c89.DECL_RE.match(masked)
        if match is None or match.group("type").strip().startswith(
                ("extern ", "static ")):
            continue
        name = match.group("name")
        type_text = " ".join(match.group("type").split())
        if match.group("ptr").strip():
            type_text += " *"
        rest = (match.group("rest") or "").lstrip("=").strip()
        declarations[name] = (type_text, index + 1, rest)

    candidates: list[EpochProbeCandidate] = []
    for name, (type_text, declaration_line, declaration_init) in \
            declarations.items():
        escaped = re.escape(name)
        assignment = re.compile(
            # `*cursor = value` writes through cursor; it does not begin a
            # new cursor value epoch.  Treating it as a local overwrite made
            # output pointers look like four unrelated values on the exact
            # residual that motivated this module.
            rf"(?<![\w.>*])\b{escaped}\b\s*=\s*(?!=)(?P<rhs>[^;]+);"
        )
        token = re.compile(rf"\b{escaped}\b")
        mutation = re.compile(
            rf"(?:\+\+\s*\b{escaped}\b|--\s*\b{escaped}\b|"
            rf"\b{escaped}\b\s*(?:\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=))"
        )
        writes: list[tuple[int, str, int]] = []
        if declaration_init:
            writes.append((declaration_line, declaration_init,
                           depths[declaration_line - 1]))
        for index, masked in enumerate(masked_lines):
            declaration = c89.DECL_RE.match(masked)
            for match in assignment.finditer(masked):
                if declaration is not None and \
                        declaration.group("name") == name:
                    continue
                writes.append((index + 1, match.group("rhs").strip(),
                               depths[index]))
        writes = sorted(set(writes))
        if len(writes) < 2:
            continue
        epochs: list[ValueEpoch] = []
        for ordinal, (start, initializer, depth) in enumerate(writes, start=1):
            end = (writes[ordinal][0] - 1
                   if ordinal < len(writes) else len(masked_lines))
            uses = tuple(
                line for line in range(start, end + 1)
                if token.search(masked_lines[line - 1])
            )
            mutations = tuple(
                line for line in range(start, end + 1)
                if mutation.search(masked_lines[line - 1])
            )
            controls = tuple(
                line for line in uses
                if re.search(r"\b(?:if|for|while|switch)\s*\(",
                             masked_lines[line - 1])
            )
            epochs.append(ValueEpoch(
                ordinal, start, end, " ".join(initializer.split()),
                _initializer_class(initializer), depth, uses, mutations,
                controls))
        classes = {epoch.initializer_class for epoch in epochs}
        expressions = {epoch.initializer for epoch in epochs}
        depth_count = len({epoch.brace_depth for epoch in epochs})
        if len(classes) < 2 and len(expressions) < 2:
            continue
        score = (len(epochs) * 3 + len(classes) * 2 + depth_count +
                 sum(bool(epoch.mutation_lines) for epoch in epochs) +
                 sum(bool(epoch.control_use_lines) for epoch in epochs))
        candidates.append(EpochProbeCandidate(
            name, type_text, declaration_line, score, tuple(epochs)))
    candidates.sort(key=lambda row: (-row.score, row.local))
    return tuple(candidates[:max(0, limit)])


def _structured_loop_ranges(
        masked_lines: list[str]) -> tuple[tuple[int, int], ...]:
    """One-based brace ranges for ordinary structured for/while loops."""
    stack: list[tuple[int, bool]] = []
    loops: list[tuple[int, int]] = []
    pending_loop = False
    for line_number, line in enumerate(masked_lines, start=1):
        if re.search(r"\b(?:for|while)\s*\(", line):
            pending_loop = True
        for character in line:
            if character == "{":
                stack.append((line_number, pending_loop))
                pending_loop = False
            elif character == "}" and stack:
                start, is_loop = stack.pop()
                if is_loop:
                    loops.append((start, line_number))
        # A braceless loop is intentionally unsupported by this conservative
        # source experiment generator.
        if pending_loop and ";" in line:
            pending_loop = False
    return tuple(sorted(loops))


def _structured_block_ranges(
        masked_lines: list[str]) -> tuple[tuple[int, int], ...]:
    """One-based ranges for every braced source block."""
    stack: list[int] = []
    ranges: list[tuple[int, int]] = []
    for line_number, line in enumerate(masked_lines, start=1):
        for character in line:
            if character == "{":
                stack.append(line_number)
            elif character == "}" and stack:
                ranges.append((stack.pop(), line_number))
    return tuple(sorted(ranges))


def _replace_code_identifier(
        original: str, masked: str, name: str, replacement: str, *,
        first_only: bool = False) -> str:
    matches = list(re.finditer(rf"\b{re.escape(name)}\b", masked))
    if first_only:
        matches = matches[:1]
    for match in reversed(matches):
        original = (original[:match.start()] + replacement +
                    original[match.end():])
    return original


def split_epoch_experiments(
        source: str, limit: int = 16) -> tuple[SourceExperiment, ...]:
    """Conservative zero-token scalar value-web split experiments.

    A candidate begins with an assignment inside a structured loop and every
    later lexical use stays at that assignment's brace depth or deeper. This
    avoids the common conditional-definition escape. It is still a source-
    shape hypothesis, so callers must compile and replay semantics.
    Self-dependent pointer epochs are deliberately excluded.
    """
    lines = source.splitlines()
    masked_lines = c89._mask(source).splitlines()
    depths = _line_depths(masked_lines)
    loops = _structured_loop_ranges(masked_lines)
    blocks = _structured_block_ranges(masked_lines)
    occupied = set(_IDENTIFIER.findall(c89._mask(source)))
    experiments: list[SourceExperiment] = []
    for candidate in epoch_probe_candidates(source, limit=32):
        if "*" in candidate.type_text:
            continue
        for epoch in candidate.epochs:
            if epoch.start_line == candidate.declaration_line:
                continue
            later_uses = tuple(
                line for line in epoch.use_lines if line > epoch.start_line)
            if not later_uses:
                continue
            if not any(start < epoch.start_line < end for start, end in loops):
                continue
            enclosing_blocks = [
                (start, end) for start, end in blocks
                if start < epoch.start_line < end
            ]
            if not enclosing_blocks:
                continue
            assignment_block = max(enclosing_blocks, key=lambda row: row[0])
            if any(not (assignment_block[0] < line < assignment_block[1])
                   for line in later_uses):
                continue
            if any(depths[line - 1] < epoch.brace_depth
                   for line in later_uses):
                continue
            start_masked = masked_lines[epoch.start_line - 1]
            assignment = re.search(
                rf"(?<![\w.>*])\b{re.escape(candidate.local)}\b\s*=\s*(?!=)",
                start_masked)
            if assignment is None:
                continue
            new_name = f"{candidate.local}_epoch{epoch.ordinal}"
            suffix = 2
            while new_name in occupied:
                new_name = f"{candidate.local}_epoch{epoch.ordinal}_{suffix}"
                suffix += 1

            edited = list(lines)
            # Rename only the defining LHS. A recurrence such as
            # `cursor = cursor + 1` must still read the prior value web.
            line = edited[epoch.start_line - 1]
            name_start = assignment.start()
            edited[epoch.start_line - 1] = (
                line[:name_start] + new_name +
                line[name_start + len(candidate.local):])
            for line_number in range(epoch.start_line + 1,
                                     epoch.end_line + 1):
                edited[line_number - 1] = _replace_code_identifier(
                    edited[line_number - 1], masked_lines[line_number - 1],
                    candidate.local, new_name)

            declaration = lines[candidate.declaration_line - 1]
            indent = declaration[:len(declaration) - len(declaration.lstrip())]
            edited.insert(
                candidate.declaration_line,
                f"{indent}{candidate.type_text} {new_name};")
            rendered = "\n".join(edited)
            if source.endswith("\n"):
                rendered += "\n"
            experiments.append(SourceExperiment(
                "split value epoch "
                f"{candidate.local} E{epoch.ordinal} "
                f"L{epoch.start_line}-{epoch.end_line}", rendered))
            occupied.add(new_name)
            if len(experiments) >= max(0, limit):
                return tuple(experiments)
    return tuple(experiments)


def _history_family(label: str) -> str:
    lowered = label.lower()
    if "statement" in lowered or "order" in lowered:
        return "statement-order"
    if "materialize assignment web" in lowered or "transparent" in lowered:
        return "transparent-copy/materialized-web"
    if "declaration" in lowered or "initializer" in lowered:
        return "declaration/initializer"
    if "register-" in lowered:
        return "register-qualifier"
    if "split" in lowered and "epoch" in lowered:
        return "split-value-epoch"
    if "pointer" in lowered:
        return "pointer-shape"
    if "scope" in lowered:
        return "scope"
    return "other"


def experiment_families(
        history: Iterable[Mapping[str, Any]]) -> tuple[ExperimentFamily, ...]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in history:
        label = str(row.get("label") or row.get("action") or "").strip()
        if not label:
            continue
        family = _history_family(label)
        item = grouped.setdefault(family, {
            "attempted": 0, "compiled": 0, "accepted": 0, "scores": []})
        item["attempted"] += 1
        attempt = row.get("attempt") or {}
        if isinstance(attempt, Mapping) and attempt.get("compiled"):
            item["compiled"] += 1
        if row.get("accepted_for_next_round"):
            item["accepted"] += 1
        score = attempt.get("score") if isinstance(attempt, Mapping) else None
        if isinstance(score, (int, float)):
            item["scores"].append(float(score))
    rows = [
        ExperimentFamily(
            family, values["attempted"], values["compiled"],
            values["accepted"],
            max(values["scores"]) if values["scores"] else None)
        for family, values in grouped.items()
    ]
    rows.sort(key=lambda row: row.family)
    return tuple(rows)


def receipt_history(conn, parent_attempt_id: int | None, source: str, *,
                    max_depth: int = 32,
                    max_rows: int = 512) -> tuple[dict[str, Any], ...]:
    """Recover deterministic experiments made from same-source ancestors.

    Restarting the repair CLI must not erase the negative result of an earlier
    compiler search.  Walk only ancestors whose source hash equals the current
    candidate, then read their deterministic exactness children.  The source
    equality gate prevents experiments against an older semantic candidate
    from being presented as evidence about the current one.
    """
    if conn is None or parent_attempt_id is None:
        return ()
    wanted_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    lineage: list[int] = []
    current: int | None = int(parent_attempt_id)
    try:
        while current is not None and len(lineage) < max_depth:
            row = conn.execute(
                "SELECT parent_attempt_id, source_sha256, source_code "
                "FROM attempts WHERE id=?", (current,)).fetchone()
            if row is None:
                break
            source_hash = row[1] or hashlib.sha256(
                str(row[2]).encode("utf-8")).hexdigest()
            if source_hash != wanted_hash:
                break
            lineage.append(current)
            current = int(row[0]) if row[0] is not None else None

        lineage_ids = set(lineage)
        history: list[dict[str, Any]] = []
        seen_children: set[int] = set()
        for parent in lineage:
            rows = conn.execute(
                "SELECT a.id, e.action, a.compiled, a.score, a.exact, "
                "a.source_sha256 "
                "FROM attempt_edges e JOIN attempts a "
                "ON a.id=e.child_attempt_id "
                "WHERE e.parent_attempt_id=? AND "
                "e.relation='deterministic-exactness-search' "
                "ORDER BY a.id", (parent,)).fetchall()
            for child_id, action, compiled, score, exact, source_hash in rows:
                child_id = int(child_id)
                if child_id in seen_children:
                    continue
                seen_children.add(child_id)
                history.append({
                    "receipt_id": child_id,
                    "label": str(action or ""),
                    "source_sha256": str(source_hash or ""),
                    "attempt": {
                        "compiled": bool(compiled),
                        "score": float(score) if score is not None else None,
                        "exact": bool(exact),
                    },
                    "accepted_for_next_round": child_id in lineage_ids,
                    "history_source": "same-source-attempt-ancestry",
                })
                if len(history) >= max_rows:
                    return tuple(history)
        return tuple(history)
    except sqlite3.DatabaseError:
        # A missing/migrating receipt table is explicit evidence debt, but it
        # must not kill compilation.  Only expected SQLite shape failures are
        # declined; programming errors still surface in tests.
        return ()


def _suggestions(
        cycles: tuple[RegisterCycle, ...],
        epochs: tuple[EpochProbeCandidate, ...],
        history: tuple[ExperimentFamily, ...]) -> tuple[str, ...]:
    if not cycles:
        return ()
    exhausted = {
        row.family for row in history
        if row.attempted and row.compiled == row.attempted and not row.accepted
    }
    suggestions = []
    if epochs and "split-value-epoch" not in exhausted:
        names = ", ".join(f"`{row.local}`" for row in epochs[:4])
        suggestions.append(
            "split one CFG-compatible overwrite epoch from a multi-epoch "
            f"local ({names}); test candidates independently"
        )
    if "declaration/initializer" not in exhausted:
        suggestions.append(
            "separate declaration from initialization or narrow one local's "
            "scope without changing any value use"
        )
    if "transparent-copy/materialized-web" not in exhausted:
        suggestions.append(
            "materialize or inline exactly one value web and verify whether "
            "the correspondence cycle changes"
        )
    if "statement-order" not in exhausted:
        suggestions.append(
            "permute only proven-independent statements in the cycle's "
            "def/use blocks"
        )
    suggestions.append(
        "if local lifetime probes are inert, test staged pointer/store "
        "expressions and equivalent CFG spelling as separate families"
    )
    return tuple(suggestions)


def build(diff: str, source: str, *,
          history: Iterable[Mapping[str, Any]] = (),
          rejected: Iterable[str] = ()) -> ExactnessGradient:
    sig = signals.analyse(diff or "")
    faults = {
        "structural": sig.structural,
        "layout": sig.layout,
        "offset": sig.offset,
        "width": sig.width,
        "relocation": sig.reloc,
        "register_allocation": sig.regalloc,
        "ordering": sig.ordering,
        "immediate": sig.immediate,
    }
    correspondences = register_correspondences(diff or "")
    cycles = register_cycles(correspondences)
    anchor = None
    mismatched_webs = None
    if allocdiff.applicable(diff or ""):
        mismatches = allocdiff.mismatches(diff or "")
        mismatched_webs = len(mismatches)
        raw_anchor = allocdiff.anchor(diff or "")
        if raw_anchor is not None:
            anchor = AllocationAnchor(
                raw_anchor.line + 1, raw_anchor.target_reg,
                raw_anchor.cand_reg,
                tuple(allocdiff.web_instructions(
                    diff or "", raw_anchor.line)))
    epoch_candidates = epoch_probe_candidates(source)
    families = experiment_families(history)
    return ExactnessGradient(
        _classification(diff or ""), faults, entry_abi_parameters(source),
        correspondences, cycles,
        anchor, mismatched_webs, epoch_candidates, families,
        _suggestions(cycles, epoch_candidates, families),
        tuple(str(item) for item in rejected if str(item).strip()),
    )


def movement(before: ExactnessGradient,
             after: ExactnessGradient) -> dict[str, Any]:
    """Compact causal delta for one compile experiment receipt."""
    before_cycles = [cycle.registers for cycle in before.register_cycles]
    after_cycles = [cycle.registers for cycle in after.register_cycles]
    return {
        "classification_before": before.residual_classification,
        "classification_after": after.residual_classification,
        "register_edges_before": len(before.register_correspondences),
        "register_edges_after": len(after.register_correspondences),
        "cycles_before": before_cycles,
        "cycles_after": after_cycles,
        "cycle_changed": before_cycles != after_cycles,
        "register_faults_before": before.faults.get(
            "register_allocation", 0),
        "register_faults_after": after.faults.get(
            "register_allocation", 0),
    }


def render_json(gradient: ExactnessGradient) -> str:
    return json.dumps(gradient.to_dict(), indent=2, sort_keys=True)
