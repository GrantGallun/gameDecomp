"""m2c's `?` type placeholder, which IDO refuses for the whole translation unit.

m2c emits `?` where it cannot infer a type. Measured on the regenerated `_Litob` draft (132 lines):
cfe stops at line 12, `? lldiv(s32 *, s32, s32);`, with `Syntax Error` + `Empty declaration
specifiers` -- and the error list is truncated there, so nothing after it is ever judged. One
unresolved placeholder masks the entire file. 122 drafts in the tree carry one, and 1,962 logged
attempts died with that exact message.

This is not a codegen problem, it is a lexical one with a small enumerable answer set, so the fix is
deterministic: substitute a concrete type and let the object decide. Nothing here is a claim about the
program -- it is a claim about what IDO can parse, and the compiler is the judge.

Evidence first: `widths` maps a symbol name to the C type its observed accesses imply (`solver.globaldecl`
already derives those), and a placeholder whose name has evidence uses it. Otherwise the default is
`s32`, IDO's implicit int.

    from solver import m2c_placeholders
    code, names = m2c_placeholders.rewrite(code, widths)
    for label, variant in m2c_placeholders.variants(code, limit=2):
        ...
"""
from __future__ import annotations

import re

# A declaration whose type m2c could not infer. Shapes actually emitted:
#     ? name(args);            function prototype, unknown return type
#     extern ? name;           object, unknown type
#     ? sp30;                  LOCAL, unknown type -- and this is the one that dominates
#     ? *var_s3;               local pointer, unknown pointee
#     void f(s32 a, ? b);      PARAMETER, unknown type -- see below
# All of them are the same token in the same grammatical position -- a type specifier -- so one
# substitution covers the class.
#
# The first version of this pattern required `?` to be followed directly by an identifier, so it
# silently skipped `? *var_s3;` and the sweep reported "the error moved rather than cleared" on all
# six drafts it tried. The error had not moved: the same line was still there, unrewritten.
#
# THE SECOND VERSION WAS STILL WRONG, and it cost a whole measurement. `^[ \t]*` anchored the pattern to
# a line start, and `PARAM`'s lookahead required punctuation after the token, so `? b` in a parameter
# list matched NEITHER rule. Measured on the size-bucketed frame (eval/results/intake-20260921):
# 36 raw `?` tokens, 18 found, 2 drafts rewritten, 12 tokens left in a declaration position -- every one
# of them a named parameter:
#     u64 __ll_mul(s64 a0_unk0, ? a0_unk4, s64 a1_unk0, ? a1_unk4) {
#     void osSyncPrintf(s8 *fmt, ? arg1, ? arg2, ? arg3, ...) {
# `PARAM` is not redundant with this: it owns the NAMELESS shape, `void g(? *, s32);`, where there is no
# identifier to anchor on. The two rules are a union, not a hierarchy -- extending this one to
# `? name` and dropping `PARAM` would lose `(? *`, and replacing this one with `PARAM` would lose
# everything else. Both are asserted in tests/test_m2c_placeholders.py.
#
# The `?` must be followed by an identifier for this rule to fire at all, which is exactly what
# separates it from `PARAM`: a NAMED placeholder in a parameter list is this rule's, a NAMELESS one
# (`(? *`) is PARAM's, and neither rule silently substitutes for the other. Masking in `_masked` keeps a
# `?` in prose or in a string literal out of both.
#
# THE BOUNDARY IS PART OF THE RULE, not decoration. Dropping the `^` anchor to reach parameter lists also
# let the pattern match a ternary: `return a ? b : 0;` became `return a s32 b : 0;`. A type specifier can
# only stand at the start of a line, after a `(`, or after a `,`, so that is what the lookbehind says,
# and `tests/test_m2c_placeholders.py` asserts the ternary is left byte-identical.
DECL_LINE = re.compile(
    r"(?P<indent>(?<![^\n(,])[ \t]*)(?P<extern>extern[ \t]+)?\?"
    r"(?:[ \t]*(?P<stars>\*+))?[ \t]*(?P<name>[A-Za-z_]\w*)", re.M)
PARAM = re.compile(r"(?<=[(,])\s*\?(?=\s*[,)*])")
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
# Strings too, now that this runs inside `zero_token_harvest.repair_chain` on every draft rather than
# only in a sweep I chose to run. A `?` or a `,` inside a literal must not be read as a type specifier,
# and `printf("...?")` / `printf(", ?")` are ordinary C.
STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')

DEFAULT = "s32"
# Ordered by how often the codebase's own headers use them, so the common answer is tried first.
FALLBACKS = ("s32", "void", "u32", "f32")


def _masked(code: str) -> str:
    """The code with comments and string literals blanked, so a `?` in prose or in a literal is not
    read as a type. Both masks preserve length so offsets stay valid."""
    blank = lambda m: " " * len(m.group(0))                                # noqa: E731
    return STRING.sub(blank, COMMENT.sub(blank, code))


def placeholders(code: str) -> list[tuple[int, str]]:
    """[(offset, declared name)] for every `?` that cfe reads as a declaration specifier."""
    masked = _masked(code)
    found = [(m.start(), m.group("name")) for m in DECL_LINE.finditer(masked)]
    found += [(m.start(), "") for m in PARAM.finditer(masked)]
    return sorted(found)


def rewrite(code: str, widths: dict[str, str] | None = None, default: str = DEFAULT
            ) -> tuple[str, list[str]]:
    """Replace every placeholder with a concrete type. Returns (code, [names resolved]).

    Declines by returning the input unchanged when there is no placeholder, which is the common case
    and must not touch the source: a rewrite that fires everywhere would corrupt clean drafts.
    """
    if "?" not in code:
        return code, []
    widths = widths or {}
    named: list[str] = []
    # The rewrite is positional (`placeholders()` returns offsets into `code`), but `sub_decl` runs on
    # the ORIGINAL text so comment and string content is preserved byte for byte. Masking is only used
    # to decide WHERE the placeholders are.
    masked = _masked(code)
    if "?" not in masked:
        return code, []

    def sub_decl(m: re.Match) -> str:
        name = m.group("name")
        named.append(name)
        stars = (m.group("stars") or "").replace(" ", "")
        return f"{m.group('indent')}{m.group('extern') or ''}{widths.get(name, default)} {stars}{name}"

    out = DECL_LINE.sub(sub_decl, code)
    out = PARAM.sub(f" {default}", out)
    return out, named


def variants(code: str, limit: int = 2, default: str = DEFAULT
             ) -> list[tuple[str, str]]:
    """[(label, code)] that vary ONE placeholder at a time over `FALLBACKS`.

    A return type that only appears in a prototype cannot change codegen except at the call sites the
    function itself does not contain, so the first variant is usually enough; the enumeration exists
    for the cases where the object disagrees, and it is bounded so a draft with many placeholders
    cannot explode into a search.
    """
    out: list[tuple[str, str]] = []
    targets = [off for off, _name in placeholders(code)][:limit]
    for index, offset in enumerate(targets):
        for alt in FALLBACKS[1:]:
            trial = code[:offset] + alt + code[offset + 1:]
            out.append((f"placeholder{index}={alt}", trial))
    return out
