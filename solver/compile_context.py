"""Separate a candidate's COMPILE CONTEXT from its BODY.

WHY
---
Measured on the campaign (eval/results/refinement-data-20260927/analysis/context_vs_code.out):
about half of all compile failures are context failures -- undefined or redeclared names, unknown
type names -- not code failures (41% of 40,692 deterministic, 49% of 1,778 model failures, plus
~10% type-shape errors). The architectural cause: every candidate carries its own context inline
(includes, externs, struct definitions), so every variant re-invents it, a failure cannot be
attributed to code or context, a model must write the context too, and no result can be reused
across jobs because nothing identifies "the same context".

THE SPLIT
---------
context  everything in the file except the target function's definition
body     the definition itself
A context is VALIDATED once by compiling it with a stub body; after that, a body that fails to
compile against it failed on its own terms. ``key`` identifies a context by the bytes the
compiler would actually read -- its text plus every header it includes, transitively -- so a
result cache keyed on (context key, body) is safe across jobs.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_INCLUDE = re.compile(r'(?m)^[ \t]*#\s*include\s*[<"]([^>"\n]+)[>"]')


@dataclass(frozen=True)
class Split:
    context: str      # the file minus the definition, with a marker where it stood
    body: str         # the definition, signature through closing brace
    marker: str = "/* @@BODY@@ */"

    def assemble(self, body: str | None = None) -> str:
        return self.context.replace(self.marker, (body if body is not None else self.body).strip(), 1)


def split(source: str, function: str) -> Split:
    """Raises ValueError unless the source holds exactly one ordinary definition of ``function``."""
    from solver import repair_context
    match, end = repair_context.definition(source, function)
    start = match.start()
    marker = Split.marker
    return Split(source[:start] + marker + source[end:], source[start:end], marker)


# The project's CC_CHECK include path (Makefile: -I. -Iinclude -Iinclude/PR -Isrc/ultra/audio
# -Isrc/ultra/libc). Resolving only under include/ missed SDK headers such as <os_internal.h>, so
# both the symbol table and the context key silently left those bytes out.
SEARCH_PATH = ("include", "include/PR", "src/ultra/audio", "src/ultra/libc", ".")


def _resolve(repo: Path, inc: str, parent: Path | None) -> Path | None:
    if parent is not None and (parent / inc).is_file():
        return (parent / inc).resolve()
    for base in SEARCH_PATH:
        candidate = Path(repo) / base / inc
        if candidate.is_file():
            return candidate.resolve()
    return None


def included_files(repo: Path, text: str, limit: int = 400) -> list[Path]:
    """Every project header ``text`` includes, transitively, in a stable order, resolved on the
    project's include path."""
    root = Path(repo).resolve()
    stack = [(inc, None) for inc in reversed(_INCLUDE.findall(text))]
    seen: list[Path] = []
    visited: set[Path] = set()
    while stack and len(visited) < limit:
        inc, parent = stack.pop()
        path = _resolve(repo, inc, parent)
        if path is None or path in visited or not path.is_relative_to(root):
            continue
        visited.add(path)
        seen.append(path)
        for child in reversed(_INCLUDE.findall(path.read_text(errors="replace"))):
            stack.append((child, path.parent))
    return seen


def key(repo: Path, context: str, recipe: str = "") -> str:
    """Identity of a compile context: its own text, the bytes of every header it pulls in, and the
    compiler recipe. Two candidates compiled under the same key saw identical inputs apart from
    their bodies -- which is what makes a cross-job result cache safe."""
    digest = hashlib.sha256()
    digest.update(context.encode())
    digest.update(b"\0recipe\0" + recipe.encode())
    for path in included_files(repo, context):
        digest.update(b"\0" + str(path.name).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def stub_body(body: str) -> str:
    """The definition's signature with an empty body: enough to validate the context alone."""
    brace = body.index("{")
    return body[:brace].rstrip() + " {\n}\n"


def extract_body(text: str, function: str) -> str:
    """Only the target function's definition from a model's answer. Anything else it wrote --
    typedefs, externs, helper functions -- is discarded: the context is not the model's to change.
    Returns "" when there is no single definition of the function."""
    from solver import llm
    cleaned = llm.THINK_RE.sub("", text or "")
    blocks = [b.strip() for b in llm.FENCE_RE.findall(cleaned)] or [cleaned]
    for block in reversed(blocks):
        try:
            return split(block, function).body
        except ValueError:
            continue
    return ""


BODY_PROMPT = """\
You are writing the body of one C function that the configured IDO 5.3 compiler (-O2) must compile
to the target MIPS instructions byte-for-byte. The COMPILE CONTEXT below -- includes, types,
declarations -- is fixed and already known to compile. Do not change it and do not repeat it.

Write ONLY the complete definition of `{function}` (signature and body), in one ```c block.
Rebuild its shape from the target assembly: real loops and if/else where the assembly has them,
array indexing and struct fields where it indexes by a stride, only the locals the register use
implies, and widths/signedness the loads and stores imply. Use only names the context declares.
C89: declarations at the top of each block; types s8 u8 s16 u16 s32 u32 f32 f64; no stdint, no
inline, no __asm__.

COMPILE CONTEXT (READ-ONLY; your function goes where /* @@BODY@@ */ is):
```c
{context}
```

TARGET ASSEMBLY (READ-ONLY):
```
{asm}
```

CURRENT DEFINITION (weighted progress score {score:.3f}; NOT percent bytes):
```c
{body}
```

INSTRUCTION DIFF (READ-ONLY; `-` target, `+` current):
```
{diff}
```
"""


def body_prompt(function: str, sp: Split, asm: str, score: float, diff: str) -> str:
    return BODY_PROMPT.format(function=function, context=sp.context[-12000:], asm=asm,
                              score=score, body=sp.body, diff=(diff or "")[:10000])


# --- declared-identifier check (monitor-guided decoding's idea, after generation) ---------------

C_KEYWORDS = frozenset("""
    auto break case char const continue default do double else enum extern float for goto if int
    long register return short signed sizeof static struct switch typedef union unsigned void
    volatile while NULL TRUE FALSE __attribute__ __asm__ asm inline defined""".split())
_IDENT = re.compile(r"[A-Za-z_]\w*")
_STRINGS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_DECLARED_IN_TEXT = (
    re.compile(r"}\s*(\w+)\s*;"),                                # typedef'd struct body name
    re.compile(r"\b(?:struct|union|enum)\s+(\w+)"),              # tags
    re.compile(r"\btypedef\b[^;{]*?\b(\w+)\s*(?:\[[^\]]*\])*\s*;"),  # typedef alias
    re.compile(r"\btypedef\b[^;]*\(\s*\*\s*(\w+)\s*\)"),         # typedef of a function pointer
    re.compile(r"^[ \t]*#\s*define\s+(\w+)", re.M),              # macros
    re.compile(r"(\w+)\s*\([^;{}]*\)\s*[;{]"),                   # functions (prototypes/defs)
    re.compile(r"\b(\w+)\s*(?:\[[^\]]*\])*\s*[;=,)]"),           # declarators, parameters
)


_NUMBER = re.compile(r"\b0[xX][0-9a-fA-F]+[uUlL]*|\b\d+(?:\.\d*)?(?:[eE][-+]?\d+)?[fFuUlL]*")


def _code(text: str) -> str:
    """Code with comments, string/char literals and NUMBERS neutralised. Without the numbers
    step `0x18` read as the identifier `x18` and `1U` as `U`: the first probe on 399 compiling
    campaign sources flagged 89% of them (analysis/lint_probe.out)."""
    return _NUMBER.sub("0", _STRINGS.sub('""', _COMMENTS.sub(" ", text or "")))


def _declared(text: str) -> set[str]:
    code = _code(text)
    names: set[str] = set()
    for pattern in _DECLARED_IN_TEXT:
        names.update(pattern.findall(code))
    for block in re.findall(r"\benum\b[^{;]*\{([^}]*)\}", code):   # enumerators
        names.update(re.findall(r"(\w+)\s*(?:=[^,]*)?(?:,|$)", block))
    return {n for n in names if n and not n[0].isdigit()}


def symbols(repo: Path, context: str) -> frozenset[str]:
    """Every name the compile context can supply: declared in the context text or in any header it
    includes, transitively. Deliberately over-inclusive -- a spurious extra symbol only weakens the
    check, while a missing one would reject valid code."""
    names = _declared(context)
    for path in included_files(repo, context):
        names |= _declared(path.read_text(errors="replace"))
    return frozenset(names)


_QUALIFIERS = frozenset({"const", "volatile", "static", "register", "unsigned", "signed", "extern"})
_NOT_TYPES = frozenset({"return", "goto", "case", "else", "do", "sizeof", "if", "while", "for",
                        "switch", "break", "continue", "default"})


def local_declarations(body: str) -> set[str]:
    """Names the body itself declares: parameters and locals. A declaration is the one place two
    identifiers stand side by side (`T x`, `T *x`, `struct S x`) -- expressions never do, apart from
    keywords like `return x` -- so each statement is read as a declaration only when it starts that
    way, and every top-level declarator (`s16 a, *b = 1, c[4];`) is collected. The loose patterns
    used for headers treat any `x;` or `f(...)` as a declaration, which silently hid every
    undeclared use in a body (caught by the test that the check fires)."""
    code = _code(body)
    names: set[str] = set()
    pieces = re.split(r"[;{}]", code)
    head = re.match(r"[^(]*\(([^)]*)\)", code)
    if head:                                              # the parameter list
        pieces += head.group(1).split(",")
    for piece in pieces:
        tokens = re.findall(r"[A-Za-z_]\w*|\*|\[|\]|=|,|\(|\)", piece)
        i = 0
        while i < len(tokens) and tokens[i] in _QUALIFIERS:
            i += 1
        if i < len(tokens) and tokens[i] in ("struct", "union", "enum"):
            i += 2
        elif i < len(tokens) and re.fullmatch(r"[A-Za-z_]\w*", tokens[i]) and tokens[i] not in _NOT_TYPES:
            i += 1
        else:
            continue
        depth, expect_name = 0, True
        for tok in tokens[i:]:
            if tok in "([":
                depth += 1
            elif tok in ")]":
                depth -= 1
            elif depth == 0 and tok == ",":
                expect_name = True
            elif depth == 0 and tok == "=":
                expect_name = False
            elif expect_name and tok not in ("*",) and re.fullmatch(r"[A-Za-z_]\w*", tok):
                if tok not in _QUALIFIERS:
                    names.add(tok)
                    expect_name = False
    return names


def undeclared(body: str, known: frozenset[str], limit: int = 12) -> list[dict]:
    """Identifiers the body USES that neither the context nor the body itself declares, each with
    the closest declared names. Member names after `.`/`->` and goto labels are not checked."""
    import difflib
    code = _code(body)
    local = local_declarations(body)
    labels = set(re.findall(r"^\s*(\w+)\s*:(?!:)", code, re.M))
    out: list[dict] = []
    seen: set[str] = set()
    for m in _IDENT.finditer(code):
        name = m.group(0)
        if name in seen or name in C_KEYWORDS or name in known or name in local or name in labels:
            continue
        before = code[:m.start()].rstrip()
        if before.endswith((".", "->")):
            continue                     # a member: the struct's shape is a layout question
        if code[m.end():].lstrip().startswith("("):
            # C89 implicitly declares an undeclared CALLEE (returning int) and IDO accepts it, so
            # a call cannot fail the compile: on the probe every remaining false positive in 399
            # compiling sources was a call. (It can still change codegen -- a separate question.)
            continue
        seen.add(name)
        out.append({"name": name,
                    "closest": difflib.get_close_matches(name, known, n=3, cutoff=0.6)})
        if len(out) >= limit:
            break
    return out


BODY_REPAIR_PROMPT = """\
Your definition of `{function}` does not compile against the fixed COMPILE CONTEXT below. Fix ONLY
what the compiler rejects; keep the function's structure and statements. Use only names the
context declares (or declare locals inside the function). Return ONLY the complete corrected
definition of `{function}` in one ```c block.

COMPILER ERRORS (clang's are the precise ones; IDO's follow):
```
{errors}
```
{undeclared}
COMPILE CONTEXT (READ-ONLY; your function goes where /* @@BODY@@ */ is):
```c
{context}
```

YOUR DEFINITION:
```c
{body}
```
"""


def repair_prompt(function: str, sp: Split, body: str, errors: str, flagged: list[dict]) -> str:
    lines = ""
    if flagged:
        rows = [f"- {f['name']}: not declared anywhere"
                + (f"; did you mean {', '.join(f['closest'])}?" if f["closest"] else "")
                for f in flagged]
        lines = "NAMES YOUR DEFINITION USES THAT NOTHING DECLARES:\n" + "\n".join(rows) + "\n"
    return BODY_REPAIR_PROMPT.format(function=function, errors=errors or "(no diagnostics)",
                                     undeclared=lines, context=sp.context[-12000:], body=body)
