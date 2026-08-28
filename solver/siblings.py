"""Retrieve already-matched functions with similar assembly, as templates.

From the project's own DECOMPILATION_LEARNINGS.md, on what actually works:

    "Mirror matched siblings verbatim. When a function sits next to an
     already-matched near-twin (common in state-machine callback families),
     copying the sibling's source form -- including its struct field layout,
     parameter spelling, local-alias pattern, and statement grouping -- is the
     fastest path to a match. Field signedness drives register allocation even
     when the generated loads/stores look identical, so copy the *layout*, not
     just the body."

2,113 matched functions is a template library. The model does not have to
invent a struct layout when a sibling already proves one.

EVAL HONESTY: a sibling is a DIFFERENT function, so this is not leaking the
target's own source -- it is the context a human decompiler genuinely has. But
a very close twin (lock/unlock pairs, callback families) can carry most of the
answer, so runs using siblings must be reported as such and never compared
head-to-head against runs without them. Retrieving the target itself is barred
outright.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

SIM_RE = re.compile(r"^\s*\d+\.\s+(\w+)\s+\(score:\s*([\d.]+)\)")
CSRC_RE = re.compile(r"^\s*C source:\s*(\S+)")


def find(repo: Path, func: str, top: int = 3, min_score: float = 0.45,
         timeout: int = 300) -> list[tuple[str, float, Path]]:
    """(name, score, source_path) for matched functions resembling `func`."""
    try:
        proc = subprocess.run(
            ["bash", "-lc",
             f". .venv/bin/activate && python3 tools/find_similar_functions.py "
             f"{func} --top {top + 2}"],
            cwd=repo, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return []

    out: list[tuple[str, float, Path]] = []
    pending: tuple[str, float] | None = None
    for line in proc.stdout.splitlines():
        m = SIM_RE.match(line)
        if m:
            name, score = m.group(1), float(m.group(2))
            pending = (name, score) if name != func and score >= min_score else None
            continue
        c = CSRC_RE.match(line)
        if c and pending:
            out.append((pending[0], pending[1], Path(c.group(1))))
            pending = None
    return out[:top]


def extract_source(path: Path, func: str, max_lines: int = 70) -> str:
    """The sibling's function body, brace-balanced."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""

    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^[A-Za-z_][\w \*]*\b{re.escape(func)}\s*\(", line):
            start = i
            break
    if start is None:
        return ""

    body, depth, opened = [], 0, False
    for line in lines[start:start + max_lines]:
        body.append(line)
        depth += line.count("{") - line.count("}")
        if "{" in line:
            opened = True
        if opened and depth <= 0:
            break
    return "\n".join(body)


def context_block(repo: Path, func: str, top: int = 2) -> str:
    """A prompt block of matched sibling sources, or "" if none are close."""
    found = find(repo, func, top=top)
    if not found:
        return ""

    parts = ["\nALREADY-MATCHED SIMILAR FUNCTIONS (these compile byte-exact; "
             "mirror their structure, struct layouts and field signedness):"]
    for name, score, path in found:
        src = extract_source(path, name)
        if not src:
            continue
        parts.append(f"\n/* {name} -- similarity {score:.2f}, VERIFIED MATCH */")
        parts.append("```c")
        parts.append(src)
        parts.append("```")

    if len(parts) == 1:
        return ""
    parts.append("\nCopy the LAYOUT, not just the body: field widths and "
                 "signedness drive register allocation even when the loads and "
                 "stores look identical.\n")
    return "\n".join(parts)
