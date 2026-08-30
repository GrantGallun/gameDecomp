"""Type names the build already provides, derived from common.h's include closure.

87 of 1562 non-compiling attempts (5.6%) die on `redeclaration of X`, and the
names are overwhelmingly types the build genuinely defines -- Vec3i 21, Gfx 7,
Mtx 2, plus the scalar typedefs. On clampRacePlayerVectorXZHalfSpeed it killed
4 of 4 draws, so for some functions it is a wall rather than a long tail.

The set is DERIVED by walking the headers, never hardcoded from a frequency
list. Hardcoding would strip a type the candidate legitimately invented the
moment the model happened to reuse a popular name, turning a clean compile
error into wrong code -- and the frequency list is a description of past
failures, not of what the build defines.

CONTAMINATION: common.h reaches game/math/geometry.h, which the decomp team
reconstructed. Only NAMES are read here, and only to decide what not to
redeclare. No definition enters a prompt or a candidate: the candidate compiles
against the real header either way, so stripping its duplicate adds no
information it did not already have. This is compile repair, not knowledge
injection.
"""

from __future__ import annotations

import re
from pathlib import Path

INCLUDE_RE = re.compile(r'^[ \t]*#\s*include\s*[<"]([^>"]+)[>"]', re.M)

# `typedef ... Name;`, `typedef ... Name[...];`, `struct Name {`, and the
# `} Name;` tail of an anonymous typedef.
TYPEDEF_TAIL = re.compile(r"^\s*}\s*\**\s*(\w+)\s*(?:\[[^\]]*\])?\s*;", re.M)
TYPEDEF_ONELINE = re.compile(
    r"^\s*typedef\s+[\w\s\*]+?\s\**(\w+)\s*(?:\[[^\]]*\])?\s*;", re.M)
TAGGED = re.compile(r"^\s*(?:typedef\s+)?(?:struct|union|enum)\s+(\w+)",
                    re.M)


def _resolve(name: str, roots: list[Path]) -> Path | None:
    for r in roots:
        p = r / name
        if p.is_file():
            return p
    return None


def closure(repo: Path, entry: str = "include/common.h",
            max_files: int = 400) -> set[Path]:
    """Every header reachable from `entry`, following includes that resolve."""
    repo = Path(repo).expanduser()
    roots = [repo / "include", repo / "src", repo]
    start = repo / entry
    if not start.is_file():
        return set()

    seen: set[Path] = set()
    stack = [start]
    while stack and len(seen) < max_files:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        try:
            text = cur.read_text(errors="replace")
        except OSError:
            continue
        for inc in INCLUDE_RE.findall(text):
            nxt = _resolve(inc, roots + [cur.parent])
            if nxt and nxt not in seen:
                stack.append(nxt)
    return seen


def type_names(repo: Path, entry: str = "include/common.h") -> set[str]:
    """Names the build already declares. Empty set means "do not strip anything".

    Returning an empty set on failure matters: a caller that strips
    redeclarations must do nothing at all rather than guess, because a wrong
    strip removes a type the candidate needs.
    """
    names: set[str] = set()
    for path in closure(repo, entry):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for pat in (TYPEDEF_TAIL, TYPEDEF_ONELINE, TAGGED):
            names.update(pat.findall(text))
    # keywords the patterns can pick up from malformed matches
    names -= {"struct", "union", "enum", "typedef", "const", "static",
              "unsigned", "signed", "void", "if", "return"}
    return {n for n in names if n and not n[0].isdigit()}


# A redeclaration in the candidate: a typedef or tagged definition WITH a body.
# A bare `struct Foo;` forward declaration is harmless and is left alone.
def _spans(code: str) -> list[tuple[int, int, str]]:
    """(start, end, name) for each type definition with a body."""
    out: list[tuple[int, int, str]] = []
    for m in re.finditer(r"\b(?:typedef\s+)?(?:struct|union|enum)\b"
                         r"(?:\s+(\w+))?\s*\{", code):
        depth, i = 0, m.end() - 1
        while i < len(code):
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if i >= len(code):
            break
        tail = re.match(r"\}\s*\**\s*(\w+)?\s*(?:\[[^\]]*\])?\s*;", code[i:])
        if not tail:
            continue
        end = i + tail.end()
        name = m.group(1) or tail.group(1)
        if name:
            out.append((m.start(), end, name))
    return out


def strip_redeclarations(code: str, known: set[str]) -> tuple[str, list[str]]:
    """Remove type definitions the build already provides. Returns (code, removed).

    Only definitions whose name the build declares are touched, and a scalar
    typedef of a name the build owns (`typedef int s32;`) is removed too, since
    IDO rejects it as a redeclaration rather than accepting it as identical.
    """
    if not known:
        return code, []

    removed: list[str] = []
    out = code
    for start, end, name in sorted(_spans(code), reverse=True):
        if name in known:
            out = out[:start] + out[end:]
            removed.append(name)

    def drop_scalar(m: "re.Match[str]") -> str:
        nm = m.group(1)
        if nm in known:
            removed.append(nm)
            return ""
        return m.group(0)

    out = re.sub(r"^[ \t]*typedef\s+(?:unsigned\s+|signed\s+)?"
                 r"(?:char|short|int|long|float|double)[\w\s\*]*?"
                 r"\b(\w+)\s*;[ \t]*\n?", drop_scalar, out, flags=re.M)
    return out, removed
