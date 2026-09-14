"""Evidence-only context for isolated repair output/feedback experiments.

Consumes supplied artifacts only; does not load project or reference sources.
The actual semantic report uses feedback[].input.name and causal_feedback;
operation_gradient is an aggregate rendered string, not case-indexed evidence.
"""
from __future__ import annotations

from copy import deepcopy
import json


def build_feedback(*, semantic_report: dict, source: str,
                   target_assembly: str, header_context: str | dict = "",
                   abi: dict | None = None, mode: str = "full") -> str:
    """Render common evidence without prescribing patch or full-source output.

    Compact keeps the complete first counterexample (including its executed
    windows, operand provenance and verified prefix) but omits other cases and
    the cross-case gradient. That gradient has no case identifiers, so selecting
    a purported associated cluster would manufacture an association. All other
    report fields, including unknown future uncertainty fields, are preserved.
    No source, assembly, ABI or header truncation differs between modes.
    """
    if mode not in {"full", "compact"}:
        raise ValueError("mode must be 'full' or 'compact'")
    packet = deepcopy(semantic_report)
    if mode == "compact":
        failures = packet.get("feedback", [])
        if not isinstance(failures, list):
            raise ValueError("semantic_report.feedback must be a list")
        # Without a complete causal counterexample, do not suppress potentially
        # essential aggregate evidence. This includes semantic-pass exactness.
        primary = failures[0] if failures else None
        if isinstance(primary, dict) and primary.get("causal_feedback"):
            packet["feedback"] = [primary]
            gradient = packet.pop("operation_gradient", None)
            packet["context_selection"] = {
                "retained_counterexample": primary.get("input", {}).get("name"),
                "omitted_counterexamples": max(0, len(failures) - 1),
                "aggregate_operation_gradient_omitted": bool(gradient),
                "reason": "First counterexample retains its full causal evidence; cross-case gradient is not case-indexed.",
                "scope": "Omitted observations remain validation obligations; selection does not change pass status or uncertainty.",
            }
        else:
            packet["context_selection"] = {
                "compact_fallback": "full evidence: no complete causal counterexample supplied"
            }
    headers = (header_context if isinstance(header_context, str)
               else json.dumps(header_context, ensure_ascii=False, sort_keys=True))
    return (
        "SEMANTIC EVIDENCE (finite executed cases; not universal equivalence):\n"
        + json.dumps(packet, ensure_ascii=False, sort_keys=True)
        + "\nPUBLIC ABI (read-only):\n"
        + json.dumps(abi or {}, ensure_ascii=False, sort_keys=True)
        + "\nCURRENT C:\n" + source
        + "\nTARGET ASSEMBLY (read-only):\n" + target_assembly
        + "\nPROJECT HEADER CONTEXT (read-only):\n" + headers
    )
