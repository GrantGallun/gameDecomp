"""Map a candidate C edit to the diff: does it touch a line the residual is attributed to?

The compiler's own line records (`source_attribution`) tie each differing target/candidate instruction to a C line.
Over 11,378 recorded search edges, an edit touching such a line improved 9.2% of the time and one touching none
3.6% (eval/results/edit-effect-atlas-20260923/locality.json). For the blind statement families the untouched side is
almost empty: stmt_order 0 of 358, commutative 0 of 658, stmt_move 7 of 762. Families that edit declarations
(local_type, decl_order, layout, frontend_type, evidence_site...) are exempt: a declaration compiles to no
instruction of its own, so it can never be a residual line.
"""
from __future__ import annotations

import difflib

LOCAL_FAMILIES = frozenset({"stmt_move", "stmt_order", "commutative"})


def residual_lines(source: str, diff: str, attribution: dict | None) -> set[int] | None:
    """C lines of the parent that compile to a differing aligned instruction; None when not source-bound."""
    from solver import alignment, evidence_site
    from solver.source_attribution import instructions_of, sha
    if not diff or not attribution or attribution.get("status") != "verified" \
            or attribution.get("source_sha256") != sha(source):
        return None
    where = {r["normalized_line"]: r.get("candidate_line") for r in instructions_of(attribution)}
    stream = evidence_site._candidate_stream_lines(diff)
    lines = set()
    for step in alignment.align_diff(diff).steps:
        c = step.candidate
        if c is None or (step.target is not None and step.target.text == c.text):
            continue
        if c.index < len(stream) and where.get(stream[c.index]):
            lines.add(where[stream[c.index]])
    return lines


def edited_lines(parent: str, child: str) -> set[int]:
    out = set()
    matcher = difflib.SequenceMatcher(a=parent.split("\n"), b=child.split("\n"), autojunk=False)
    for op, a0, a1, _b0, _b1 in matcher.get_opcodes():
        if op != "equal":
            out.update(range(a0 + 1, max(a1, a0 + 1) + 1))
    return out


def off_target(parent: str, child: str, kinds, faulty: set[int] | None) -> bool:
    """True when a blind statement edit touches no faulty line (and the faulty lines are known)."""
    if faulty is None or not faulty or not (set(kinds) <= LOCAL_FAMILIES):
        return False
    return not (edited_lines(parent, child) & faulty)
