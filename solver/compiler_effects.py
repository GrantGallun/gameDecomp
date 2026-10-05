"""Opt-in, measured C-edit -> assembly-state forecasts.

This is a small experiment, not a compiler or a verifier. Source observations
and learned compiler hypotheses are separate. The state loses immediates and
long-range order; a zero state distance does NOT mean an exact object. Callers
must bind a compiler/flags domain, declare development groups, freeze forecasts
before evaluation outcomes, and compile candidates through the ordinary gates.
"""
from __future__ import annotations

from collections import Counter
import difflib
import hashlib
import json
import math
import re

from solver import c89, edit_locality, regalloc_mutations, regalloc_signature
from solver.source_attribution import instructions_of

SCHEMA = "compiler-edit-effects-v1"
FAMILIES = frozenset({"pure_inline", "local_web_merge", "stmt_move", "stmt_order"})
IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
CALL = re.compile(r"\b(?!(?:if|while|for|switch|sizeof)\b)[A-Za-z_]\w*\s*\(")
MEMORY = re.compile(r"(?P<offset>-?(?:0x[0-9a-fA-F]+|\d+))\(\$?sp\)$")


def _number(value):
    try:
        return int(value, 0)
    except ValueError:
        return int(value, 10)


def assembly_state(asm: str) -> dict[str, float]:
    """Symbol/address-free instruction, register, stack and adjacency counts."""
    instructions = regalloc_signature.parse(asm)
    state = Counter(instructions=len(instructions), frame_bytes=0)
    signatures = []
    for inst in instructions:
        state[f"opcode:{inst.mnemonic}"] += 1
        regs = inst.registers()
        for position, reg in regs:
            state[f"register:{position}:{reg}"] += 1
        signatures.append(inst.mnemonic + "[" + ",".join(f"{p}:{r}" for p, r in regs) + "]")
        for operand in inst.operands:
            if match := MEMORY.fullmatch(operand):
                state[f"stack:{inst.mnemonic}:{_number(match['offset'])}"] += 1
        if inst.mnemonic in {"addiu", "addi", "daddiu"} and len(inst.operands) == 3:
            if tuple(x.lstrip("$") for x in inst.operands[:2]) == ("sp", "sp"):
                try:
                    state["frame_bytes"] = max(state["frame_bytes"], -_number(inst.operands[2]))
                except ValueError:
                    pass
    for left, right in zip(signatures, signatures[1:]):
        state[f"order:{left}|{right}"] += 1
    return {k: float(v) for k, v in sorted(state.items())}


def _weight(key):
    if key == "instructions":
        return 4.
    if key == "frame_bytes":
        return .25
    return {"opcode": 1., "register": .5, "stack": 1., "order": .25}.get(key.split(":")[0], 1.)


def state_distance(left: dict, right: dict) -> float:
    return sum(_weight(k) * abs(left.get(k, 0) - right.get(k, 0)) for k in left.keys() | right.keys())


def features(parent: str, child: str, function: str, family: str, diff: str,
             attribution: dict | None, parent_asm: str) -> dict:
    """Names/hashes stay in evidence, never in the numeric model input.

    These lexical measurements describe a proposal, not proven C semantics.
    Verified line attribution supplies instruction context, not local-to-LR
    identity. Missing/stale attribution is explicit, never a negative label.
    """
    if family not in FAMILIES:
        raise ValueError(f"unsupported effect family: {family}")
    begin, end = regalloc_mutations._body(parent, function)
    cb, ce = regalloc_mutations._body(child, function)
    body = c89._mask(parent)[begin:end]
    child_body = c89._mask(child)[cb:ce]
    lines = body.splitlines()
    changed = edit_locality.edited_lines(parent, child)
    local_offset = parent[:begin].count("\n")
    touched_indexes = [i for i in range(len(lines)) if i + 1 + local_offset in changed]
    touched = "\n".join(lines[i] for i in touched_indexes)
    span = "\n".join(lines[min(touched_indexes):max(touched_indexes) + 1]) if touched_indexes else ""
    declarations = list(regalloc_mutations.PURE_DECL.finditer(body))
    child_names = set(IDENT.findall(child_body))
    removed = [d for d in declarations if d["name"] not in child_names]
    uses, crossings, live_span = 0, 0, 0
    for decl in removed:
        positions = [m.start() for m in re.finditer(rf"\b{re.escape(decl['name'])}\b", body)
                     if m.start() >= decl.end()]
        uses += len(positions)
        if positions:
            region = body[decl.end():positions[-1]]
            crossings += len(CALL.findall(region))
            live_span += region.count("\n")
    assignments = re.findall(r"\b([A-Za-z_]\w*)\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)", span)
    identifiers = Counter(IDENT.findall(span))
    numeric = {
        "parent_lines": len(lines), "edited_lines": len(changed),
        "edit_span_lines": len(span.splitlines()), "parent_declarations": len(declarations),
        "removed_locals": len(removed), "removed_local_reads": uses,
        "removed_local_crossed_calls": crossings, "removed_local_span": live_span,
        "edited_calls": len(CALL.findall(touched)), "crossed_calls": len(CALL.findall(span)),
        "crossed_def_uses": sum(max(0, identifiers[name] - 1) for name in set(assignments)),
        "edited_memory_ops": len(re.findall(r"->|\[|\*", touched)),
        "edited_arithmetic_ops": len(re.findall(r"[-+*/%&|^]|<<|>>", touched)),
        "edited_branches": len(re.findall(r"\b(?:if|for|while|switch|goto)\b", touched)),
        "source_line_delta": len(child_body.splitlines()) - len(lines),
    }
    # Counts are independent of source spellings and target outcome labels.
    for key, value in assembly_state(parent_asm).items():
        if not key.startswith(("order:", "stack:")):
            numeric[f"parent:{key}"] = value
    bound = bool(attribution and attribution.get("status") == "verified" and
                 attribution.get("source_sha256") == hashlib.sha256(parent.encode()).hexdigest())
    faulty = edit_locality.residual_lines(parent, diff, attribution) if bound else None
    numeric["source_mapping_known"] = int(bound)
    numeric["residual_mapping_known"] = int(faulty is not None)
    if faulty is not None:
        numeric["residual_lines"] = len(faulty)
        numeric["touched_residual_lines"] = len(changed & faulty)
    if bound:
        for row in instructions_of(attribution):
            if row.get("candidate_line") in changed:
                for instruction in regalloc_signature.parse(row.get("instruction", "")):
                    key = f"touched_opcode:{instruction.mnemonic}"
                    numeric[key] = numeric.get(key, 0) + 1
    hypotheses = []
    if removed:
        hypotheses.append({"effect": "named_local_removed", "status": "inferred_source", "count": len(removed)})
    if family == "local_web_merge":
        hypotheses.append({"effect": "branch_local_identity_merged", "status": "inferred_source"})
    if family in {"stmt_move", "stmt_order"}:
        hypotheses.append({"effect": "source_precedence_changed", "status": "inferred_source"})
    hypotheses.append({"effect": "register_stack_and_schedule_may_change", "status": "hypothesized_compiler"})
    return {"schema": SCHEMA, "domain": "unspecified", "family": family,
            "numeric": {k: float(v) for k, v in sorted(numeric.items())},
            "evidence": {"source_sha256": hashlib.sha256(parent.encode()).hexdigest(),
                         "child_sha256": hashlib.sha256(child.encode()).hexdigest(),
                         "source_mapping": "verified" if bound else "unknown"},
            "hypotheses": hypotheses}


def _finite(data):
    if not isinstance(data, dict) or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                                         for v in data.values()):
        raise ValueError("effect vectors must contain only finite numbers")
    return dict(sorted(data.items()))


def fit(rows: list[dict], development_groups) -> dict:
    """Explicit group allowlist; failures never masquerade as no-change effects."""
    allowed = sorted(set(development_groups))
    examples = []
    for row in rows:
        if row["group"] not in allowed:
            raise ValueError("training row outside declared development groups")
        feature = row["features"]
        before = _finite(row["before"])
        after = _finite(row["after"]) if row.get("compiled") and row.get("after") is not None else None
        delta = ({k: after.get(k, 0) - before.get(k, 0) for k in before.keys() | after.keys()}
                 if after is not None else None)
        examples.append({"group": row["group"], "family": feature["family"],
                         "domain": feature.get("domain", "unspecified"),
                         "numeric": _finite(feature["numeric"]), "delta": delta})
    examples.sort(key=lambda row: json.dumps(row, sort_keys=True))
    keys = sorted({k for ex in examples for k in ex["numeric"]})
    scales = {k: max(1., max((abs(_log(ex["numeric"].get(k, 0))) for ex in examples), default=0.)) for k in keys}
    return {"schema": SCHEMA, "development_groups": allowed, "examples": examples, "scales": scales,
            "training_eligible": False, "minimum_groups": 2, "maximum_groups": 5, "distance_limit": .6}


def _log(value):
    return math.copysign(math.log1p(abs(value)), value)


def _context_distance(left, right, scales):
    keys = left.keys() | right.keys()
    if not keys:
        return 0.
    return sum(min(1., abs(_log(left.get(k, 0)) - _log(right.get(k, 0))) / scales.get(k, 1.))
               for k in keys) / len(keys)


def predict(model: dict, feature: dict, parent_state: dict, target_state: dict) -> dict:
    """Group-balanced nearest contexts predict signed state changes, with abstention."""
    if model.get("schema") != SCHEMA:
        raise ValueError("unsupported compiler effect model")
    numeric = _finite(feature["numeric"])
    _finite(parent_state)
    _finite(target_state)
    groups = {}
    failures = 0
    for ex in model["examples"]:
        if ex["family"] != feature["family"] or ex["domain"] != feature.get("domain", "unspecified"):
            continue
        distance = _context_distance(numeric, ex["numeric"], model["scales"])
        if distance > model["distance_limit"]:
            continue
        if ex["delta"] is None:
            failures += 1
            continue
        groups.setdefault(ex["group"], []).append((distance, ex["delta"]))
    # Average ties within a group, then one vote per group. Duplicating attempts
    # cannot turn one function/TU into purported independent transfer evidence.
    nearest = []
    for group, values in groups.items():
        best = min(d for d, _ in values)
        ties = [v for d, v in values if abs(d - best) < 1e-12]
        delta = {k: sum(v.get(k, 0) for v in ties) / len(ties) for k in set().union(*ties)}
        nearest.append((best, group, delta))
    nearest.sort(key=lambda x: (x[0], x[1]))
    nearest = nearest[:model["maximum_groups"]]
    enough = len(nearest) >= model["minimum_groups"]
    delta = {}
    if enough:
        total = sum(1 / (.05 + distance) for distance, _, _ in nearest)
        for distance, _, change in nearest:
            weight = (1 / (.05 + distance)) / total
            for key, value in change.items():
                delta[key] = delta.get(key, 0) + weight * value
    predicted = {k: max(0., parent_state.get(k, 0) + delta.get(k, 0))
                 for k in parent_state.keys() | delta.keys()}
    return {"status": "predicted" if enough else "abstain", "delta": dict(sorted(delta.items())),
            "predicted_state": dict(sorted(predicted.items())), "support_groups": len(nearest),
            "distance": nearest[0][0] if nearest else None, "compile_failures": failures,
            "estimated_progress": state_distance(parent_state, target_state) - state_distance(predicted, target_state),
            "unknowns": ["lossy state does not certify exact instructions or semantics"] +
                        ([] if enough else ["insufficient independent development contexts"])}


def rank(model: dict, proposals: list[dict], parent_state: dict, target_state: dict) -> list[dict]:
    """Return every candidate; preserve an original-order exploration slot in four."""
    if len({p["ordinal"] for p in proposals}) != len(proposals):
        raise ValueError("duplicate proposal ordinal")
    rows = [dict(p, forecast=predict(model, p["features"], parent_state, target_state)) for p in proposals]
    rows.sort(key=lambda p: (-p["forecast"]["estimated_progress"], p["ordinal"]))
    result = []
    while rows:
        if len(result) % 4 == 3:
            index = min(range(len(rows)), key=lambda i: rows[i]["ordinal"])
        else:
            index = 0
        result.append(rows.pop(index))
    return result
