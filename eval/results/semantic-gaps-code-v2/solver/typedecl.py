"""Declare the structs an m2c draft names but nothing defines.

The single largest reason a never-attempted function has no baseline is not
difficulty. It is this:

    s32 Fdrumsoff(PlayerCommandState *arg0, s32 arg1) {   <- line 7: Syntax Error
        arg0->pdrums = NULL;

m2c invents a type name for the pointer it sees and emits member accesses
through it, but common.h declares no such type, so the parameter list is a
syntax error and the function never reaches the oracle at all. Measured on the
69 functions that `tools/claude` had refused as decomp.me scratch IDs: 64 of
66 drafts failed to compile, 45 with this exact signature, and 28 of those have
binary evidence for the struct's offsets sitting in the evidence tier already.

A bare forward declaration does not help -- the bodies dereference members, so
they need a layout, not just a name. The layout is already known: the evidence
tier keys observed accesses by `param0`, `param1`, ... which is precisely the
parameter position, so no inference is required to join them.

CONTAMINATION: nothing here reads include/game/**. The offsets come from the
binary and the member names are m2c's own labels. That is the same posture as
solver/buildtypes.py -- names carry no information the draft did not already
contain, and every field lands where the binary says, not where a model
guessed. Contrast solver/project_headers.py, which supplies the decomp team's
reconstructed prototype and struct layout and is a different experiment.

The generated struct is a HYPOTHESIS about which name sits at which offset;
only the offsets are facts. It is meant to produce a compiling candidate with
a readable residual, which diffrepair can then correct -- getting from "does
not compile" to "compiles at 91%" is the move this unlocks, not exactness.
"""

from __future__ import annotations

import re

from solver import structgen, typepool

# `TYPE *name` / `TYPE **name` in a parameter list. Deliberately narrow: a
# parameter this does not match is left alone rather than guessed at.
POINTER_PARAM = re.compile(r"^\s*(?:const\s+)?([A-Za-z_]\w*)\s*(\*+)\s*"
                           r"([A-Za-z_]\w*)\s*$")
MEMBER_USE = re.compile(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)")
INCLUDE_LINE = re.compile(r"^[ \t]*#\s*include[^\n]*(?:\n|$)", re.M)

# `void` is a KEYWORD, not a typedef, so buildtypes -- which walks headers for
# declared type NAMES -- never contains it. The first version therefore treated
# `void *arg0` as an undeclared struct type and emitted
# `typedef struct { ... } void;`, which is a syntax error, for 8 of 35 plans
# including guMtxF2L, alCopy and alSavePull. The other keywords are here for
# the same reason: a header cannot declare them, so nothing else will exclude
# them.
PRIMITIVE_TYPES = frozenset({
    "void", "char", "short", "int", "long", "float", "double",
    "signed", "unsigned", "_Bool",
})

# A statement can look exactly like a declaration to a regex: `return var_a1;`
# parses as type `return`, name `var_a1`. A throwaway version of the scan below
# duly reported `return` as an undeclared type on six functions. Keywords are
# excluded explicitly rather than hoped away.
C_KEYWORDS = frozenset({
    "return", "if", "else", "while", "for", "do", "switch", "case",
    "default", "break", "continue", "goto", "sizeof", "typedef", "static",
    "extern", "const", "volatile", "register", "auto", "struct", "union",
    "enum", "inline",
})

# `T *name;` on its own line INSIDE a body. One pointer level only, matching
# pointer_parameters -- `**` is ambiguous about which level owns the members.
LOCAL_DECL = re.compile(
    r"^[ \t]+(?:const\s+)?([A-Za-z_]\w*)\s*\*\s*([A-Za-z_]\w*)\s*;", re.M)


def declared_in(code: str, name: str) -> bool:
    """Does `code` already declare `name` as a type?

    The anonymous form puts the name LAST -- `typedef struct { ... } T;` -- so
    a check for `typedef\\s+T` or `struct\\s+T` misses it entirely and this
    emits a duplicate typedef, turning a syntax error into a redeclaration
    error. Caught by a test; both spellings are matched now.
    """
    escaped = re.escape(name)
    return bool(
        re.search(r"\b(?:struct|union|enum)\s+" + escaped + r"\s*[{;]", code)
        or re.search(r"\btypedef\b[^;{}]*\b" + escaped + r"\s*;", code)
        or re.search(r"\}\s*\**\s*" + escaped + r"\s*;", code))


def definition_params(code: str, func: str) -> list[str] | None:
    """The parameter list of `func`'s definition, or None if not found."""
    match = re.search(r"^[A-Za-z_][^;\n]*?\b" + re.escape(func) +
                      r"\s*\(([^;{)]*)\)\s*\{", code, re.M)
    if not match:
        return None
    return [p.strip() for p in match.group(1).split(",") if p.strip()]


def pointer_parameters(code: str, func: str) -> list[tuple[int, str, str]]:
    """[(index, type_name, variable)] for pointer parameters, in order."""
    params = definition_params(code, func)
    if params is None:
        return []
    out = []
    for index, param in enumerate(params):
        match = POINTER_PARAM.match(param)
        if match and match.group(2) == "*":     # one level only; ** is unclear
            out.append((index, match.group(1), match.group(3)))
    return out


def pointer_locals(code: str, func: str) -> list[tuple[str, str]]:
    """[(type_name, variable)] for pointer LOCALS declared in the body.

    The largest single blocker on the leaf residual, 66 of 143 functions: the
    error routes correctly to typedecl and typedecl declines, because it only
    ever read the parameter list. m2c writes

        PlayerCommandState *var_v1;

    inside MusAsk and eleven siblings, and nothing declares the type.
    """
    body_at = code.find("{", code.find(func))
    if body_at == -1:
        return []
    out = []
    for type_name, var in LOCAL_DECL.findall(code[body_at:]):
        if type_name in C_KEYWORDS or type_name in PRIMITIVE_TYPES:
            continue
        out.append((type_name, var))
    return out


def members_used(code: str, variables: set[str]) -> list[str]:
    """Member names reached through `variables`, in first textual use order."""
    seen: list[str] = []
    for var, member in MEMBER_USE.findall(code):
        if var in variables and member not in seen:
            seen.append(member)
    return seen


def _merge(layouts: list[list[tuple[int, int, str]]]
           ) -> list[tuple[int, int, str]]:
    """Widest access wins per offset, exactly as structgen.layout does."""
    slot: dict[int, tuple[int, str]] = {}
    for fields in layouts:
        for off, width, ctype in fields:
            prev = slot.get(off)
            if prev is None or width > prev[0]:
                slot[off] = (width, ctype)
    return [(o, w, t) for o, (w, t) in sorted(slot.items())]


def plan(code: str, func: str, layout: dict[str, list[tuple[int, int, str]]],
         known_types: set[str],
         pool: dict[str, list[tuple[int, int, str]]] | None = None
         ) -> list[dict]:
    """One entry per undeclared struct type that evidence can describe.

    Declines -- returns nothing for that type -- when the draft names more
    members than the binary has offsets for. CLAUDE.md's rule is that unknown
    is the default; inventing a field to satisfy a name would be exactly the
    fabrication this project exists to prevent.
    """
    by_type: dict[str, list[tuple[int | None, str]]] = {}
    for index, type_name, var in pointer_parameters(code, func):
        if (type_name in known_types or type_name in PRIMITIVE_TYPES
                or declared_in(code, type_name)):
            continue
        by_type.setdefault(type_name, []).append((index, var))
    # Locals carry index None: the evidence tier keys accesses by param0/param1
    # -- the parameter POSITION -- so a local has no evidence key of its own and
    # its layout can only come from the cross-function pool, keyed by the type
    # name m2c gave it. PlayerCommandState is named by 43 drafts as a parameter,
    # so the pool describes it far better than any one function could.
    for type_name, var in pointer_locals(code, func):
        if (type_name in known_types or declared_in(code, type_name)):
            continue
        by_type.setdefault(type_name, []).append((None, var))

    plans = []
    for type_name, uses in by_type.items():
        fields = _merge([layout.get(f"param{i}") or []
                         for i, _ in uses if i is not None])
        names = members_used(code, {var for _, var in uses})
        pooled = (pool or {}).get(type_name) or []
        source = "function"
        if all(i is None for i, _ in uses):
            # Locals only: there is no per-function evidence to prefer.
            fields, source = pooled, "pooled"

        # One function's view of a shared struct is usually a fraction of it,
        # which is why the count test below rejected 13 of 42 drafts and why 8
        # more had no evidence at all. PlayerCommandState pools to 36 offsets
        # against 15 for its best single contributor.
        if pooled and (not fields or len(names) > len(fields)):
            fields, source = pooled, "pooled"
        if not fields:
            continue                       # no evidence: nothing to declare

        keep = typepool.named_fields(fields, names)
        if keep is None:
            # Positional zip is only defensible when the counts are close. Over
            # a pooled struct it would place two members among dozens of
            # offsets by source order alone -- a guess dressed as a layout.
            if source == "pooled" or len(names) > len(fields):
                continue
            keep = {off: name for (off, _w, _t), name in zip(fields, names)}
        plans.append({
            "type": type_name,
            # Parameter POSITIONS only. Locals carry index None and must not
            # leak into this list -- it is what callers use to look evidence
            # up by `param{i}`.
            "params": [i for i, _ in uses if i is not None],
            "locals": [v for i, v in uses if i is None],
            "offsets": [off for off, _w, _t in fields],
            "named": keep,
            "source": source,
            "declined": False,
            "text": structgen.render(type_name, fields, keep),
        })
    return plans


def apply(code: str, plans: list[dict]) -> str:
    """Insert the generated typedefs after the last #include."""
    if not plans:
        return code
    block = "\n".join(
        ["", "/* Offsets are binary facts; member names are m2c labels and",
         "   therefore hypotheses. Generated by solver/typedecl.py. */"] +
        [p["text"] for p in plans] + [""])
    includes = list(INCLUDE_LINE.finditer(code))
    at = includes[-1].end() if includes else 0
    return code[:at] + block + code[at:]


def synthesize(conn, func: str, code: str, known_types: set[str],
               pool: dict[str, list[tuple[int, int, str]]] | None = None
               ) -> tuple[str, list[dict]]:
    """Convenience: layout from the KB, plan, apply. Returns (code, plans)."""
    plans = plan(code, func, structgen.layout(conn, func), known_types, pool)
    return apply(code, plans), plans
