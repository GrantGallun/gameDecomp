"""A local model as one more edit generator for near misses: small, located, semantics-preserving respellings.

The model sees only our own candidate and the oracle's diff of it (target vs candidate listing, both derived from the
binary), never reference source: `workspace.assert_uncontaminated` checks the prompt. It answers with
find/replace pairs, and each pair that applies once inside the function becomes one child. The compiler decides as
always. This is a proposal channel with no authority, the thesis's "the model proposes".

Why a model here and not only rules: near-miss residuals are spelling choices (compound assignment, operand order,
where a temporary lives, declaration order). A model reading the diff next to the source can name a respelling that no
generator family covers, and the plateau search can then combine it with deterministic edits.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ENDPOINT = os.environ.get("GAMEDECOMP_LLM_ENDPOINT", "http://172.28.32.1:11434")
MODEL = os.environ.get("GAMEDECOMP_LLM_MODEL", "gpt-oss:20b")
SCHEMA = {"type": "object", "properties": {"edits": {"type": "array", "maxItems": 8, "items": {
    "type": "object", "properties": {"find": {"type": "string"}, "replace": {"type": "string"},
                                     "why": {"type": "string"}},
    "required": ["find", "replace"]}}}, "required": ["edits"]}

LEVERS = """IDO 5.3 -O2 is deterministic: the same C always gives the same code, and different spellings of the same
computation often give different register choices or instruction order. Known levers:
- `x = x + y` vs `x += y` (compound assignment changes evaluation and register order)
- operand order of + * & | ^ == != (the operand evaluated first usually takes the lower temp register)
- a temporary variable vs the expression used inline, and the reverse (a call result kept in a variable or not)
- declaration order of locals, and statement order of independent statements
- casts and variable types (s16/u16/s32), `x[i]` vs `*(x + i)`, pointer vs index loops
- `if (a) {...} else {...}` arm order with the negated test, `!x` vs `x == 0`
"""


def _function_text(source: str, function: str) -> str:
    from solver import repair_context
    m, end = repair_context.definition(source, function)
    return source[m.start():end]


def _changed(diff: str, context: int = 2) -> str:
    """The differing lines of an oracle diff with a little context."""
    lines = (diff or "").splitlines()
    idx = [k for k, l in enumerate(lines) if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    keep = sorted({j for k in idx for j in range(max(0, k - context), min(len(lines), k + context + 1))})
    return "\n".join(lines[j] for j in keep)


def prompt(source: str, function: str, diff: str) -> str:
    body = _function_text(source, function)
    return (f"You are closing a matching decompilation that is almost byte-exact.\n\n{LEVERS}\n"
            f"The C function (our current candidate):\n```c\n{body}\n```\n\n"
            f"What still differs, as a unified diff of the compiled listings "
            f"('-' = target, '+' = this candidate), with context:\n```\n{_changed(diff)[:3500]}\n```\n\n"
            "Propose up to 8 small alternative spellings that keep the meaning exactly and could change the listing "
            "toward the target. Each edit is a JSON object: `find` is text copied EXACTLY from the function above "
            "(one statement or less, unique in the function), `replace` is its respelling. Prefer edits at the lines "
            "that produce the differing instructions. Answer only with JSON: {\"edits\": [...]}.")


def children(source: str, function: str, diff: str, repo: Path, *, cache_dir: str | None = None,
             seed: int = 0) -> list[tuple[str, str, str]]:
    """[(label, kind, child source)] from one model call; [] on any failure (a failure is logged by the caller)."""
    from solver import llm, workspace
    text_prompt = prompt(source, function, diff)
    workspace.assert_uncontaminated(text_prompt, repo, function)
    try:
        text, _meta = llm.generate(ENDPOINT, MODEL, text_prompt, timeout=240, num_predict=3000, think="low",
                                   temperature=0.4, seed=seed, response_schema=SCHEMA, cache_dir=cache_dir,
                                   cache_namespace="nearmiss-llm-v1")
        edits = json.loads(text).get("edits", [])
    except Exception:
        return []
    body = _function_text(source, function)
    start = source.index(body)
    out = []
    for e in edits:
        find, repl = str(e.get("find", "")), str(e.get("replace", ""))
        repl = re.sub(r"\s*//[^\n]*", "", re.sub(r"/\*.*?\*/", "", repl, flags=re.S))
        # a respelling that only adds comments or grouping parentheses is not an edit (gpt-oss:20b offered 6 of 8
        # like that on allocMenuRenderScratch, 2026-09-30)
        norm = lambda s: re.sub(r"[\s()]", "", s)
        if not find.strip() or norm(find) == norm(repl) or body.count(find) != 1:
            continue
        child = source[:start] + body.replace(find, repl, 1) + source[start + len(body):]
        out.append((f"llm:{find[:40]}->{repl[:40]}", "llm", child))
    return out
