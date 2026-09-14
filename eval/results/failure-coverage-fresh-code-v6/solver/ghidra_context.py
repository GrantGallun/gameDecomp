"""Compact, explicitly fallible prompt context from Ghidra evidence.

The target assembly and byte-exact build remain authoritative.  This module
deliberately omits Ghidra's instruction dump: the solver already has the real
assembly, while duplicating it would spend context without adding evidence.
The useful extra is Ghidra's independent attempt at recovered source shape.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re


SUPPORTED_SCHEMAS = frozenset({1, 2})
EXPECTED_LANGUAGE = "MIPS:BE:32:default"
WARNING_RE = re.compile(r"/\*\s*WARNING:\s*(.*?)\s*\*/")


def load(path: Path) -> dict:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("schema_version") not in SUPPORTED_SCHEMAS:
        raise ValueError("unsupported Ghidra evidence schema")
    language = evidence.get("program", {}).get("language")
    if language != EXPECTED_LANGUAGE:
        raise ValueError(
            f"expected {EXPECTED_LANGUAGE} evidence, got {language!r}")
    if not isinstance(evidence.get("function"), dict):
        raise ValueError("Ghidra evidence has no function record")
    return evidence


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, max(0, limit - 80))
    if cut < 0:
        cut = max(0, limit - 80)
    return text[:cut] + "\n/* GHIDRA CONTEXT TRUNCATED */"


def render(evidence: dict, function_name: str,
           max_decompiled_chars: int = 12_000) -> str:
    """Render the independent shape hypothesis without pretending it is C truth."""
    function = evidence.get("function", {})
    blocks = evidence.get("basic_blocks", [])
    destinations = [edge for block in blocks
                    for edge in block.get("destinations", [])]
    flow_counts = Counter(edge.get("flow", "UNKNOWN") for edge in destinations)
    computed_targets = sorted({edge.get("address", "") for edge in destinations
                               if edge.get("flow") == "COMPUTED_JUMP"})
    terminal_blocks = sum(not block.get("destinations") for block in blocks)

    calls = evidence.get("calls", [])
    call_targets = sorted({call.get("target", "") for call in calls})
    callers = evidence.get("callers", [])
    caller_entries = sorted({caller.get("entry", "") for caller in callers
                             if caller.get("entry")})
    data_counts = Counter(ref.get("target", "")
                          for ref in evidence.get("data_refs", []))
    common_data = ", ".join(
        f"{address} ({count} refs)" for address, count in data_counts.most_common(6))

    decompiler = evidence.get("decompiler", {})
    c_text = (decompiler.get("c") or "").replace("\r\n", "\n").strip()
    warnings = WARNING_RE.findall(c_text)
    c_text = WARNING_RE.sub("", c_text).strip()
    ghidra_name = function.get("ghidra_name")
    if ghidra_name and function_name:
        c_text = re.sub(rf"\b{re.escape(ghidra_name)}\b", function_name, c_text)
    c_text = _bounded(c_text, max_decompiled_chars)

    normalized_ir = evidence.get("normalized_ir", {})
    bsim_signature = evidence.get("bsim_signature", {})
    high_operations = normalized_ir.get("operations", [])
    high_opcode_counts = Counter(
        operation.get("opcode", "UNKNOWN") for operation in high_operations)
    high_blocks = normalized_ir.get("basic_blocks", [])
    raw_block_count = function.get("basic_block_count", len(blocks))
    semantic_block_count = len(high_blocks)
    try:
        omitted_block_count = max(0, int(raw_block_count) - semantic_block_count)
    except (TypeError, ValueError):
        omitted_block_count = 0
    important_opcodes = (
        "MULTIEQUAL", "CALL", "CALLIND", "CBRANCH", "BRANCHIND", "RETURN",
        "LOAD", "STORE", "PTRADD", "PTRSUB", "INT_SEXT", "INT_ZEXT",
    )
    opcode_summary = ", ".join(
        f"{opcode.lower()}={high_opcode_counts[opcode]}"
        for opcode in important_opcodes if high_opcode_counts[opcode])

    parameters = evidence.get("parameters", [])
    parameter_summary = ", ".join(
        f"{parameter.get('type', '?')} {parameter.get('name', '?')}"
        f"@{parameter.get('storage', '?')}"
        for parameter in parameters[:8])
    locals_ = evidence.get("locals", [])

    flow_summary = ", ".join(
        f"{name.lower()}={count}" for name, count in sorted(flow_counts.items()))
    lines = [
        "\nINDEPENDENT GHIDRA SHAPE HYPOTHESIS (binary-derived, not an oracle):",
        "- The target assembly above and byte-exact compiler result remain authoritative.",
        "- Ghidra can recover switches and nesting, but can choose wrong types, expressions,",
        "  boundaries, or omit blocks. Use this only as a candidate source shape.",
        (f"- Function {function.get('entry', '?')}..{function.get('end', '?')}: "
         f"{function.get('instruction_count', '?')} instructions, "
         f"{function.get('basic_block_count', '?')} basic blocks, "
         f"{len(destinations)} CFG edges ({flow_summary or 'none'}), "
         f"{terminal_blocks} terminal blocks."),
    ]
    if computed_targets:
        lines.append(
            f"- Recovered computed dispatch with {len(computed_targets)} destinations "
            "(strong switch/jump-table evidence).")
    if call_targets:
        lines.append(f"- Recovered call targets: {', '.join(call_targets[:12])}.")
    if caller_entries:
        lines.append(f"- Recovered callers: {', '.join(caller_entries[:12])}.")
    if common_data:
        lines.append(f"- Most-referenced data addresses: {common_data}.")
    signature = function.get("signature")
    if signature:
        lines.append(
            f"- Ghidra prototype hypothesis: {signature} "
            f"(calling convention {function.get('calling_convention', '?')}).")
    if parameter_summary:
        lines.append(f"- Parameter/storage hypotheses: {parameter_summary}.")
    if locals_:
        lines.append(
            f"- Ghidra committed {len(locals_)} local-variable/storage hypotheses; "
            "treat their types and names as provisional.")
    if normalized_ir.get("completed"):
        operation_count = normalized_ir.get("operation_count", len(high_operations))
        lines.append(
            f"- Normalized high P-code: {operation_count} operations, "
            f"{semantic_block_count} semantic CFG blocks"
            f" ({opcode_summary or 'no highlighted operations'}).")
        if normalized_ir.get("pcode_truncated"):
            lines.append("- Normalized P-code receipt was capped; counts exceed retained operations.")
        if omitted_block_count:
            lines.append(
                f"- STRUCTURAL WARNING: semantic CFG has {omitted_block_count} fewer block(s) "
                "than the raw instruction CFG; decompiler output is incomplete for exact shape.")
    elif normalized_ir:
        lines.append(
            "- Normalized high P-code unavailable: "
            f"{normalized_ir.get('error') or 'decompilation did not complete'}.")
    if bsim_signature.get("completed"):
        lines.append(
            f"- BSim semantic fingerprint: {bsim_signature.get('feature_count', 0)} "
            "features retained for cross-binary lookup (opaque to source generation).")
    for warning in warnings[:6]:
        lines.append(f"- DECOMPILER WARNING: {warning}; the pseudocode may omit code.")

    if decompiler.get("completed") and c_text:
        lines.extend([
            "\nRecovered pseudocode (rewrite its Ghidra types/names into valid project C):",
            "```c",
            c_text,
            "```",
        ])
    else:
        error = decompiler.get("error") or "decompilation did not complete"
        lines.append(f"- No pseudocode is available: {error}.")
    return "\n".join(lines) + "\n"


def from_file(path: Path, function_name: str,
              max_decompiled_chars: int = 12_000) -> str:
    return render(load(path), function_name, max_decompiled_chars)
