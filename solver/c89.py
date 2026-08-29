"""Rewrite C99 into the C89 that IDO 5.3 accepts.

IDO 5.3 is a 1994 compiler. The model writes modern C, and the gap between the
two is the single largest failure mode in the solver: over the 119 failing
attempts of `inc_hard_v1`, 45 (37.8%) died on the FIRST syntax error being a
construct that is legal C99 and illegal C89.

    23  declaration after a statement
     9  stdint spelling (uint8_t, uintptr_t)
     6  inline
     5  declaration in a for-init
     2  __attribute__

None of it needs cooperation from the model, and none of it changes meaning --
which is the whole reason to do it here rather than in the prompt. Telling a
model to write C89 is a request it can ignore; rewriting the text is not.

The transforms are deliberately conservative. Every one either preserves
semantics exactly or declines to fire:

  decl-after-stmt   `s32 t = f();` becomes `s32 t;` at the top of the block and
                    `t = f();` where it stood. Hoisting the INITIALISER too
                    would be wrong -- it may depend on statements above it --
                    so only the declaration moves.
  for-init decl     same split, with the declaration hoisted to the enclosing
                    block and the assignment left in the for-init.
  stdint            uint8_t -> u8, and uintptr_t -> u32 (MIPS is ILP32 here).
  inline            deleted; `static` is left alone.
  __attribute__     deleted with its balanced parentheses.

Anything it cannot parse confidently, it leaves alone: a candidate that still
fails to compile is a far better outcome than one silently given new meaning.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- primitives

STRING_LIT = re.compile(r'"(?:\\.|[^"\\])*"' r"|'(?:\\.|[^'\\])*'")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"//[^\n]*")

# Type names this codebase actually uses. Deliberately not "any identifier":
# `foo bar = 1;` is indistinguishable from a call without a symbol table, and
# guessing wrong turns a compile error into wrong code.
TYPE_WORD = (
    r"(?:unsigned\s+|signed\s+|const\s+|volatile\s+|register\s+|static\s+)*"
    r"(?:void|char|short|int|long|float|double|"
    r"[su](?:8|16|32|64)|f32|f64|"
    r"struct\s+\w+|union\s+\w+|enum\s+\w+|"
    r"[A-Z]\w*)"
)
# The pointer stars belong to the DECLARATOR, not the type, and getting that
# wrong is not a missed fix but a corruption: `const struct S *p = ...;` failed
# to parse as a declaration, so it counted as a STATEMENT, and the next real
# declaration was hoisted past it. Measured on
# getRaceCourseSurfaceSpawnTransform, which compiled at 35.78 before the pass
# and not at all after it.
DECL_RE = re.compile(
    rf"^(?P<indent>[ \t]*)(?P<type>{TYPE_WORD})"
    rf"(?P<ptr>(?:\s*\*)+\s*|\s+)"
    rf"(?P<name>\w+)\s*(?P<rest>=[^;]*)?;[ \t]*$")
FOR_DECL_RE = re.compile(
    rf"^(?P<indent>[ \t]*)for\s*\(\s*(?P<type>{TYPE_WORD}(?:\s*\*)*)\s+"
    rf"(?P<name>\w+)\s*=\s*(?P<init>[^;]+);(?P<tail>.*)$")

LABEL_RE = re.compile(r"^\s*(case\b|default\s*:|\w+\s*:(?!:))")
PREPROC_RE = re.compile(r"^\s*#")

STDINT_RE = re.compile(r"\b(u?)int(8|16|32|64)_t\b")
UINTPTR_RE = re.compile(r"\b(u?)intptr_t\b")
INLINE_RE = re.compile(r"\b(?:__)?inline(?:__)?\b\s*")
BOOL_RE = re.compile(r"\b_Bool\b")


def _mask(src: str) -> str:
    """Source with comments and strings blanked, preserving line structure.

    Used only to decide WHERE things are; edits always apply to the original.
    """
    def blank(m: "re.Match[str]") -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    src = BLOCK_COMMENT.sub(blank, src)
    src = LINE_COMMENT.sub(blank, src)
    return STRING_LIT.sub(blank, src)


# ------------------------------------------------------------ simple rewrites

def fix_types(code: str) -> str:
    """C99 spellings -> the build's own scalar names."""
    code = UINTPTR_RE.sub(lambda m: "u32" if m.group(1) else "s32", code)
    code = STDINT_RE.sub(lambda m: ("u" if m.group(1) else "s") + m.group(2),
                         code)
    return BOOL_RE.sub("s32", code)


def strip_attributes(code: str) -> str:
    """Remove __attribute__((...)) including nested parens."""
    out, i = [], 0
    while True:
        j = code.find("__attribute__", i)
        if j < 0:
            out.append(code[i:])
            return "".join(out)
        out.append(code[i:j])
        k = code.find("(", j)
        if k < 0:                       # malformed; leave the rest untouched
            out.append(code[j:])
            return "".join(out)
        depth = 0
        while k < len(code):
            if code[k] == "(":
                depth += 1
            elif code[k] == ")":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        i = k


def strip_inline(code: str) -> str:
    """`static inline void f()` -> `static void f()`."""
    return INLINE_RE.sub("", code)


# ------------------------------------------------- declaration-after-statement

def _grp(original: str, m: "re.Match[str]", name: str) -> str:
    """A match group's text, read out of the ORIGINAL line.

    Matching happens on the masked copy so comments and strings cannot be
    mistaken for code, but the edit has to be built from real text. Masking
    replaces characters one-for-one, so spans index both identically.

    Doing this the naive way -- re-matching the pattern against the original --
    silently did nothing whenever a line carried a trailing comment, because
    `s32 quotient = diff / 0x6400;   /* truncates */` does not match a pattern
    anchored at `;$`. That was 5 of the 10 residual syntax errors after the
    first version of this pass.
    """
    s, e = m.span(name)
    return original[s:e] if s >= 0 else ""


def _splittable(type_text: str) -> bool:
    """Can `T x = v;` be split into `T x;` and `x = v;` without changing it?

    Not for `const`: the assignment becomes a write to a constant, which IDO
    rejects outright ("Change value for constant variable"). Not for `static`
    either -- a static's initialiser runs once at load time, an assignment runs
    on every call, so splitting it changes behaviour SILENTLY, which is worse.
    """
    return not re.search(r"\b(const|static)\b", type_text)


def _block_starts(lines: list[str]) -> dict[int, int]:
    """For each line, the index of the line after its enclosing '{'.

    Only function-body blocks matter, so depth 0 maps to itself -- a
    declaration at file scope is already legal wherever it sits.
    """
    stack: list[int] = []
    start_of: dict[int, int] = {}
    for i, l in enumerate(lines):
        opens = l.count("{")
        closes = l.count("}")
        # a line's block is the one it is inside BEFORE its own braces apply
        start_of[i] = stack[-1] if stack else -1
        for _ in range(closes):
            if stack:
                stack.pop()
        for _ in range(opens):
            stack.append(i + 1)
        if closes and opens:
            start_of[i] = stack[-1] if stack else -1
    return start_of


def hoist_declarations(code: str) -> str:
    """Split declarations that follow a statement into decl + assignment.

    `s32 t = p->x + 1;` in the middle of a block becomes `s32 t;` inserted at
    the top of that block and `t = p->x + 1;` left in place. The initialiser
    never moves, so an initialiser that depends on earlier statements stays
    correct.
    """
    lines = code.splitlines()
    mask = _mask(code).splitlines()
    start_of = _block_starts(mask)

    seen_stmt: dict[int, bool] = {}
    inserts: dict[int, list[str]] = {}
    edits: dict[int, str] = {}

    for i, ml in enumerate(mask):
        stripped = ml.strip()
        block = start_of.get(i, -1)
        if block < 0 or not stripped:
            continue
        if PREPROC_RE.match(ml) or LABEL_RE.match(ml):
            continue
        if stripped in ("{", "}") or stripped.startswith("}"):
            continue

        m = DECL_RE.match(ml)
        fm = FOR_DECL_RE.match(ml)

        if fm and seen_stmt.get(block) and _splittable(fm.group("type")):
            # for (s32 i = 0; ...) -> s32 i; ... for (i = 0; ...)
            ind = _grp(lines[i], fm, "indent")
            inserts.setdefault(block, []).append(
                f"{ind}{_grp(lines[i], fm, 'type')} "
                f"{_grp(lines[i], fm, 'name')};")
            edits[i] = (f"{ind}for ({_grp(lines[i], fm, 'name')} = "
                        f"{_grp(lines[i], fm, 'init').strip()};"
                        f"{_grp(lines[i], fm, 'tail')}")
            continue

        if m and not stripped.endswith(")"):
            if seen_stmt.get(block) and _splittable(m.group("type")):
                ind = _grp(lines[i], m, "indent")
                if m.group("rest"):
                    inserts.setdefault(block, []).append(
                        f"{ind}{_grp(lines[i], m, 'type')}"
                        f"{_grp(lines[i], m, 'ptr')}"
                        f"{_grp(lines[i], m, 'name')};")
                    # keep whatever followed the ';' -- a trailing comment is
                    # the common case and dropping it loses information
                    # the ';' sits just past the initialiser; everything after
                    # it is trailing text (usually a comment) and is kept
                    tail = lines[i][m.end("rest") + 1:]
                    edits[i] = (f"{ind}{_grp(lines[i], m, 'name')} "
                                f"{_grp(lines[i], m, 'rest').strip()};{tail}")
                else:
                    # a bare `s32 t;` after a statement: just move it up
                    inserts.setdefault(block, []).append(lines[i])
                    edits[i] = ""
            continue

        seen_stmt[block] = True

    if not inserts and not edits:
        return code

    out: list[str] = []
    for i, l in enumerate(lines):
        for decl in inserts.get(i, []):
            out.append(decl)
        if i in edits:
            if edits[i] != "":
                out.append(edits[i])
        else:
            out.append(l)
    # a block whose '{' is the last line cannot receive inserts; harmless
    return "\n".join(out) + ("\n" if code.endswith("\n") else "")


# ---------------------------------------------------------------- entry point

def to_c89(code: str) -> str:
    """Apply every repair. Safe to call on code that is already C89."""
    code = fix_types(code)
    code = strip_attributes(code)
    code = strip_inline(code)
    code = hoist_declarations(code)
    return code
