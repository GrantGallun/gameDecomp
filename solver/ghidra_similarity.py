"""Transparent triage over exported Ghidra evidence.

The BSim metrics are deliberately not labelled as Ghidra's official score.
The official score applies architecture-specific learned weights.  More
importantly, an experiment with the same N64 source compiled into two programs
showed that raw BSim features can change with binary context.  The semantic
bundle therefore also compares address-independent high P-code shape, opcode
order, instruction mnemonics, and CFG degree shape.

Every number in this module is a ranking hint, not matching evidence.  A
cross-project source hypothesis still has to compile and pass the target's
byte-exact oracle.
"""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import math
import re


_VARNODE_RE = re.compile(
    r"^\((?P<space>[^,]+),\s*0x(?P<offset>[0-9a-fA-F]+),\s*"
    r"(?P<size>\d+)\)$")


def _features(evidence: dict) -> tuple[int, Counter[str]]:
    signature = evidence.get("bsim_signature", {})
    if not signature.get("completed"):
        raise ValueError(
            "Ghidra evidence has no completed BSim signature: "
            f"{signature.get('error') or 'unknown error'}")
    settings = signature.get("settings")
    if not isinstance(settings, int):
        raise ValueError("Ghidra BSim signature has no integer settings value")
    features = signature.get("features")
    if not isinstance(features, list) or not all(
            isinstance(feature, str) for feature in features):
        raise ValueError("Ghidra BSim signature has invalid features")
    return settings, Counter(features)


def compare_bsim(left: dict, right: dict) -> dict[str, object]:
    """Compare two exported feature multisets using transparent proxy metrics."""
    left_settings, left_counts = _features(left)
    right_settings, right_counts = _features(right)
    if left_settings != right_settings:
        raise ValueError(
            f"BSim settings differ: {left_settings} vs {right_settings}")

    shared = sum((left_counts & right_counts).values())
    union = sum((left_counts | right_counts).values())
    left_total = sum(left_counts.values())
    right_total = sum(right_counts.values())
    dot = sum(count * right_counts[feature]
              for feature, count in left_counts.items())
    left_norm = math.sqrt(sum(count * count for count in left_counts.values()))
    right_norm = math.sqrt(sum(count * count for count in right_counts.values()))

    return {
        "metric": "unweighted_bsim_feature_multiset_proxy",
        "settings": left_settings,
        "left_entry": left.get("function", {}).get("entry"),
        "right_entry": right.get("function", {}).get("entry"),
        "left_feature_count": left_total,
        "right_feature_count": right_total,
        "shared_feature_occurrences": shared,
        "shared_unique_features": len(left_counts.keys() & right_counts.keys()),
        "multiset_jaccard": shared / union if union else 1.0,
        "dice": (2 * shared / (left_total + right_total)
                 if left_total + right_total else 1.0),
        "cosine": (dot / (left_norm * right_norm)
                   if left_norm and right_norm else 1.0),
        "left_containment": shared / left_total if left_total else 1.0,
        "right_containment": shared / right_total if right_total else 1.0,
        "identical_multiset": left_counts == right_counts,
    }


def _counter_metrics(left: list[str], right: list[str]) -> dict[str, object]:
    left_counts = Counter(left)
    right_counts = Counter(right)
    shared = sum((left_counts & right_counts).values())
    union = sum((left_counts | right_counts).values())
    left_total = len(left)
    right_total = len(right)
    return {
        "left_count": left_total,
        "right_count": right_total,
        "shared": shared,
        "jaccard": shared / union if union else 1.0,
        "dice": (2 * shared / (left_total + right_total)
                 if left_total + right_total else 1.0),
        "left_containment": shared / left_total if left_total else 1.0,
        "right_containment": shared / right_total if right_total else 1.0,
        "identical_multiset": left_counts == right_counts,
    }


def _ngrams(tokens: list[str], width: int = 3) -> list[str]:
    if len(tokens) < width:
        return ["\x1f".join(tokens)] if tokens else []
    return ["\x1f".join(tokens[index:index + width])
            for index in range(len(tokens) - width + 1)]


def _sequence_metrics(left: list[str], right: list[str]) -> dict[str, object]:
    positional = sum(a == b for a, b in zip(left, right))
    maximum = max(len(left), len(right))
    return {
        "left_count": len(left),
        "right_count": len(right),
        "positional_matches": positional,
        "positional_agreement": positional / maximum if maximum else 1.0,
        "sequence_ratio": SequenceMatcher(
            None, left, right, autojunk=False).ratio(),
        "identical_sequence": left == right,
    }


def _varnode_shape(value: object, *, keep_constant: bool) -> str:
    if value is None:
        return "none"
    if not isinstance(value, str):
        return "invalid"
    match = _VARNODE_RE.match(value)
    if not match:
        return "other"
    space = match.group("space").strip().lower()
    size = match.group("size")
    aliases = {"register": "reg", "unique": "tmp", "constant": "const"}
    space = aliases.get(space, space)
    if space == "const" and keep_constant:
        return f"const:{size}:0x{match.group('offset').lower()}"
    return f"{space}:{size}"


def _operation_tokens(evidence: dict, *, keep_constants: bool) -> list[str]:
    normalized = evidence.get("normalized_ir", {})
    if not normalized.get("completed"):
        raise ValueError(
            "Ghidra evidence has no completed normalized IR: "
            f"{normalized.get('error') or 'unknown error'}")
    operations = normalized.get("operations")
    if not isinstance(operations, list):
        raise ValueError("Ghidra normalized IR has invalid operations")
    tokens = []
    for operation in operations:
        if not isinstance(operation, dict) or not isinstance(
                operation.get("opcode"), str):
            raise ValueError("Ghidra normalized IR has an invalid operation")
        inputs = operation.get("inputs")
        if not isinstance(inputs, list):
            raise ValueError("Ghidra normalized IR operation has invalid inputs")
        output = _varnode_shape(
            operation.get("output"), keep_constant=keep_constants)
        shaped_inputs = ",".join(
            _varnode_shape(value, keep_constant=keep_constants)
            for value in inputs)
        tokens.append(f"{operation['opcode']}|{output}|{shaped_inputs}")
    return tokens


def _opcodes(evidence: dict) -> list[str]:
    # Validation is shared with the richer operation-shape path.
    _operation_tokens(evidence, keep_constants=False)
    return [str(operation["opcode"])
            for operation in evidence["normalized_ir"]["operations"]]


def _mnemonics(evidence: dict) -> list[str]:
    instructions = evidence.get("instructions")
    if not isinstance(instructions, list):
        raise ValueError("Ghidra evidence has invalid instructions")
    result = []
    for instruction in instructions:
        if not isinstance(instruction, dict) or not isinstance(
                instruction.get("mnemonic"), str):
            raise ValueError("Ghidra evidence has an invalid instruction")
        result.append(instruction["mnemonic"].lower())
    return result


def _cfg_tokens(evidence: dict) -> tuple[list[str], int]:
    blocks = evidence.get("normalized_ir", {}).get("basic_blocks")
    if not isinstance(blocks, list):
        raise ValueError("Ghidra normalized IR has invalid basic blocks")
    tokens = []
    edge_count = 0
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("Ghidra normalized IR has an invalid basic block")
        incoming = block.get("in")
        outgoing = block.get("out")
        if not isinstance(incoming, list) or not isinstance(outgoing, list):
            raise ValueError("Ghidra normalized IR block has invalid edges")
        tokens.append(f"in:{len(incoming)}|out:{len(outgoing)}")
        edge_count += len(outgoing)
    # Sorted degree pairs do not depend on Ghidra's block numbering.
    return sorted(tokens), edge_count


def compare_semantics(left: dict, right: dict) -> dict[str, object]:
    """Compare address-independent program shape exported by Ghidra.

    ``provenance_rank_score`` is intentionally a simple, inspectable blend.
    It ranks hypotheses; it must never be used as an exactness threshold.
    """
    left_shapes = _operation_tokens(left, keep_constants=False)
    right_shapes = _operation_tokens(right, keep_constants=False)
    left_literals = _operation_tokens(left, keep_constants=True)
    right_literals = _operation_tokens(right, keep_constants=True)
    left_opcodes = _opcodes(left)
    right_opcodes = _opcodes(right)
    left_mnemonics = _mnemonics(left)
    right_mnemonics = _mnemonics(right)
    left_cfg, left_edges = _cfg_tokens(left)
    right_cfg, right_edges = _cfg_tokens(right)

    operation_shape = _sequence_metrics(left_shapes, right_shapes)
    operation_shape["trigram"] = _counter_metrics(
        _ngrams(left_shapes), _ngrams(right_shapes))
    operation_literals = _sequence_metrics(left_literals, right_literals)
    operation_literals["trigram"] = _counter_metrics(
        _ngrams(left_literals), _ngrams(right_literals))
    opcode_sequence = _sequence_metrics(left_opcodes, right_opcodes)
    opcode_sequence["trigram"] = _counter_metrics(
        _ngrams(left_opcodes), _ngrams(right_opcodes))
    mnemonic_sequence = _sequence_metrics(left_mnemonics, right_mnemonics)
    mnemonic_sequence["trigram"] = _counter_metrics(
        _ngrams(left_mnemonics), _ngrams(right_mnemonics))
    cfg_shape = _counter_metrics(left_cfg, right_cfg)
    cfg_shape.update({
        "left_blocks": len(left_cfg),
        "right_blocks": len(right_cfg),
        "left_edges": left_edges,
        "right_edges": right_edges,
    })

    score = (
        0.50 * float(operation_shape["sequence_ratio"])
        + 0.20 * float(opcode_sequence["trigram"]["dice"])
        + 0.20 * float(mnemonic_sequence["trigram"]["dice"])
        + 0.10 * float(cfg_shape["dice"])
    )
    return {
        "metric": "address_independent_ghidra_semantic_proxy_v1",
        "role": "provenance ranking only; compile and byte-exact oracle required",
        "left_entry": left.get("function", {}).get("entry"),
        "right_entry": right.get("function", {}).get("entry"),
        "provenance_rank_score": score,
        "operation_shape": operation_shape,
        "operation_literals": operation_literals,
        "opcode_sequence": opcode_sequence,
        "mnemonic_sequence": mnemonic_sequence,
        "cfg_shape": cfg_shape,
    }


def compare(left: dict, right: dict) -> dict[str, object]:
    """Return both raw BSim overlap and address-independent semantics."""
    return {
        "schema_version": 2,
        "role": "provenance ranking only; never an exactness oracle",
        "bsim": compare_bsim(left, right),
        "semantic": compare_semantics(left, right),
    }
