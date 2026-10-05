"""A local model writes a statement the target has and the candidate lacks. Proposal channel; the oracle decides.

A dropped statement leaves target-only instructions and nothing on the candidate side to attribute, so every
deterministic family that rewrites an existing line declines. `solver.missing_store` covers stores whose value the
target states outright; what remains (a call with many arguments, a computed value) is decompilation of a short
instruction run, which is the model's job. Measured on planted dropped statements
(eval/results/edit-capability-20261002): asked for a whole function, the model rewrote ~10 lines for a 1-line
problem and broke compilation; this module asks for the missing statement and an insertion line only.

The model sees our own candidate and the oracle's alignment of the target listing against it (target-only rows
marked), both derived from the binary, never reference source (`workspace.assert_uncontaminated`). Each answer
becomes up to three children (the stated line and its neighbours); the compiler decides.
"""
from __future__ import annotations

import json
import os
import re

ENDPOINT = os.environ.get("GAMEDECOMP_LLM_ENDPOINT", "")
MODEL = os.environ.get("GAMEDECOMP_LLM_MODEL", "gpt-oss:20b")
SCHEMA = {"type": "object", "properties": {"statements": {"type": "array", "maxItems": 3, "items": {
    "type": "object", "properties": {"after_line": {"type": "integer"}, "c": {"type": "string"}},
    "required": ["after_line", "c"]}}}, "required": ["statements"]}
TEMPERATURES = (0.2, 0.5, 0.8, 1.0)


def _definition(source: str, function: str) -> tuple[int, str]:
    from solver import repair_context
    m, end = repair_context.definition(source, function)
    return source.count("\n", 0, m.start()), source[m.start():end]


def marked_target(diff: str) -> tuple[str, int]:
    """The target listing with `>>` on rows the candidate has no counterpart for, and how many there are."""
    from solver import alignment
    rows, missing = [], 0
    for step in alignment.align_diff(diff).steps:
        if step.target is None:
            continue
        only = step.candidate is None
        missing += only
        rows.append(f"{'>>' if only else '  '} {step.target.text}")
    return "\n".join(rows), missing


def prompt(source: str, function: str, diff: str) -> str | None:
    listing, missing = marked_target(diff)
    if not missing:
        return None
    _offset, body = _definition(source, function)
    numbered = "\n".join(f"{i + 1:3d}| {l}" for i, l in enumerate(body.split("\n")))
    return ("You are matching a decompiled N64 function (IDO 5.3, MIPS). The C below compiles, but the original "
            "binary contains code this C does not produce: the target instructions marked `>>` have no counterpart "
            "in what the C compiles to. They come from one or more statements missing from the C.\n\n"
            f"Current function (line numbers for reference):\n```c\n{numbered}\n```\n\n"
            f"Target listing (relocations masked as %hi(R)/%lo(R); `>>` = missing from the C):\n```\n{listing[:6000]}\n```\n\n"
            "Write the missing statement(s) in the same style as the surrounding code (same field names, casts and "
            "helpers), and say which numbered line each goes after. Do not change any existing line. Answer only "
            'with JSON: {"statements": [{"after_line": <n>, "c": "<one C statement ending in ;>"}]}.')


def _insert(source: str, function: str, after_line: int, text: str) -> str | None:
    offset, body = _definition(source, function)
    lines = source.split("\n")
    first, last = offset, offset + body.count("\n")
    at = offset + after_line                      # 0-based index of the line after `after_line`
    if not first < at <= last:
        return None
    ref = lines[at - 1] if lines[at - 1].strip() not in ("{", "") else lines[min(at, last)]
    indent = ref[:len(ref) - len(ref.lstrip())] or "    "
    return "\n".join(lines[:at] + [indent + text.strip()] + lines[at:])


def variants(source: str, function: str, diff: str, *, repo=None, guard=None, endpoint: str = "",
             model: str = MODEL, temperatures=TEMPERATURES, cache_dir: str | None = None, generate=None):
    """`(label, candidate)` from model-proposed missing statements; nothing when the diff has no target-only row.

    `repo` runs `workspace.assert_uncontaminated` (reference-source leak). `guard(prompt)` is an extra caller check,
    e.g. a planted benchmark asserting its own answer line is absent. One of the two is required.
    """
    from solver import llm, workspace
    text = prompt(source, function, diff)
    if text is None:
        return
    if repo is None and guard is None:
        raise ValueError("missing_statement_llm needs a contamination check: pass repo= or guard=")
    if repo is not None:
        workspace.assert_uncontaminated(text, repo, function)
    if guard is not None:
        guard(text)
    generate = generate or llm.generate
    endpoint = endpoint or ENDPOINT or llm.host()
    seen = {source}
    for k, temperature in enumerate(temperatures):
        try:
            reply, _meta = generate(endpoint, model, text, timeout=300, num_predict=4000, think="low",
                                    temperature=temperature, seed=1000 + k, response_schema=SCHEMA,
                                    cache_dir=cache_dir, cache_namespace="missing-statement-llm-v1")
            statements = json.loads(reply).get("statements", [])
        except Exception:
            continue
        for item in statements:
            c = str(item.get("c", "")).strip()
            n = item.get("after_line")
            if not c.endswith(";") or not isinstance(n, int) or "\n" in c:
                continue
            for where in (n, n - 1, n + 1):
                child = _insert(source, function, where, c)
                if child and child not in seen:
                    seen.add(child)
                    yield f"missing_statement_llm[t={temperature}]: {c[:70]} @after L{where}", child
