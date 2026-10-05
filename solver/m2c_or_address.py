"""`*(ptr | n)` -- invalid C, where the assembly performs an integer OR and uses the result as an address.

THE SHAPE, from `nonmatchings/osEPiRawReadIo/target.s`:

    lw   $t0, 0xC($a0)              ; the pointer member
    or   $t1, $t0, $a1              ; t1 = t0 | arg1
    or   $t2, $t1, $at              ; t2 = t1 | physBase
    lw   $t3, %lo(D_A0000000)($t2)  ; LOAD THROUGH t2 AS AN ADDRESS

and what m2c writes for it:

    *arg2 = *(arg0->unkC | arg1 | (s32) &D_A0000000);      /* Dereferenced a non-pointer */

The OR is genuinely in the binary, and the reference decomp confirms the intent --
`*data = IO_READ(pihandle->baseAddress | devAddr);` in `src/ultra/io/epirawread.c`. What differs is the
OPERAND'S TYPE: in the reference, `baseAddress` is a `u32`, so `u32 | u32` is an integer address and
`IO_READ` casts it once at the load. m2c declares the member as a POINTER and dereferences the OR, and C
has no operator for `pointer | int`.

SO THE FIX IS NOT TO CHANGE THE OPERATOR. It is to place the cast where the reference places it:

    *arg2 = *(s32 *)((u32)arg0->unkC | arg1 | (u32)&D_A0000000);

Every operand becomes an integer so the OR is well-typed; the single conversion to a pointer happens at the
load, which is exactly what the instruction sequence does. No offset is invented, no operator is changed,
and nothing is claimed about the value: the object comparison still decides whether the result is right.

NOT A GUESS, AND NOT UNBOUNDED: the rewrite only fires when a dereference's operand actually contains a `|`,
which is a shape the assembly must corroborate (an `or` feeding a load's base register). States without it
are untouched.
"""
from __future__ import annotations

import re

# A DEREFERENCE, NOT A DECLARATOR. `s32 *f(void)` and `T *(name)` both contain `*(`, and the scan's first
# version started there -- on `void f(void) {` it matched the parameter list and DELETED the rest of the
# function from its output (verified: `'void f(void) {\n    *p = *(q);\n}\n'` came back as `';\n}\n'`).
# A unary `*` is in expression position, so it follows `=`, `(`, a comma, a semicolon or an operator -- never
# an identifier, which is what a declarator looks like.
#
# `[` AND `?` ARE OUT OF THE SET, and that is not tidiness: with `[` in it, the lookbehind matched the `[`
# of `arg0[0]` in one draft while the `*` it then consumed belonged to a DIFFERENT expression, so the scan
# removed a character (`*arg2 = *(arg0->unkC` became `**(s32 *)((u32)rg0->unkC`). A zero-width context
# character lets a match start anywhere, which is the same class of silent mis-target the regexes kept
# producing.
# ONE SHAPE, NOT AN ALTERNATION. Every regex here that offered two ways to match produced a wrong
# off-by-one match that was silent; the alternation with `^` in particular let the match start at the `*`
# of a `->` since `\s*` can be empty. So the pattern is a single form: an expression operator, then `*(`.
#
# `[` AND `?` ARE DELIBERATELY ABSENT from the operator set. With `[` in it, the context character could be
# taken from an unrelated `arg0[0]` while the `*(` belonged to a different expression -- verified: the scan
# turned `*arg2 = *(arg0->unkC` into `**(s32 *)((u32)rg0->unkC`, removing a character from inside an
# identifier.
# THE CAST'S OWN `*` MUST NOT LOOK LIKE A CONTEXT. `(s32 *)` ends in `*`, and on a second run the pattern
# matched it as if it were multiplication -- `*(s32 *)((u32)a->b | (u32)c)` became
# `*(s32 *)((u32)s32 *)((u32)a->b | (u32)c)`. The idempotence test caught it.
#
# The exclusion is applied IN CODE, not in the pattern: Python's lookbehind must be fixed-width, so
# `(?<!\bs32\s)`-style guards of varying length cannot be expressed. A match whose context is a scalar type
# name is a cast, not a dereference.
DEREFERENCE = re.compile(r"(?<=[=(,;+\-*/%&|!<>])[ \t]*\*\([^;\n]*\)")
# THE WRITE FORM SITS AT LINE START: `*(p | n) = v;`. The lookbehind above cannot express both "after an
# operator" and "at the beginning of a line" with one fixed-width assertion, and the alternation that tried
# to did so wrongly -- a `(?:^|(?<=...))` prefix let the match start at a `*` that belonged to `->`. So the
# two positions are two patterns, each unambiguous on its own.
DEREFERENCE_LINE_START = re.compile(r"(?m)^[ \t]*\*\([^;\n]*\)")
CAST_CONTEXT = re.compile(r"\b(?:s8|s16|s32|s64|u8|u16|u32|u64|f32|f64|void|char|int)\s+$")
CAST = "s32"

# An operand that already has an integer type needs no cast. A literal is an integer by definition; a cast
# to a scalar is one already. Everything else -- a pointer member, a pointer variable -- is what makes
# `pointer | int` ill-typed in the first place, and is cast to `u32`.
ALREADY_INTEGER = re.compile(r"^\s*(?:\(\s*(?:s32|u32|s16|u16|s8|u8|int|unsigned)\s*\)\s*&?\w+"
                             r"|0x[0-9A-Fa-f]+|\d+)\s*$")


def _operands(chain: str) -> list[str]:
    """Split an OR chain on its top-level `|`, keeping parenthesised groups intact.

    A `|` inside `( )` belongs to that operand -- `(s32) (a | b)` is one operand, not two -- so the split is
    depth-aware. The first version split naively and would have torn a grouped operand in half.
    """
    parts, depth, current = [], 0, []
    for character in chain:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == "|" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(character)
    parts.append("".join(current))
    return parts


def _matching_close(text: str, opening: int) -> int:
    """Index of the `)` matching the `(` at `opening`, or -1. Depth-aware, so nested calls are safe."""
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def rewrite(source: str) -> tuple[str, list[dict]]:
    """Cast the operand of every dereference that contains a top-level `|`.

    A BALANCED SCAN, NOT A REGEX. Three regex attempts failed on real drafts and each failure was silent:
    `[^()]*` cannot cross the `( )` in m2c's macro-guard comment, and `[^()\\n]*` cannot cross the `(s32)`
    cast that is part of the expression. A pattern that quietly matches nothing is indistinguishable from a
    pass with nothing to do, which is the failure this project keeps paying for, so the delimiter matching
    is explicit here.
    """
    changes: list[dict] = []
    out: list[str] = []
    cursor = 0
    matches = sorted(list(DEREFERENCE.finditer(source)) + list(DEREFERENCE_LINE_START.finditer(source)),
                     key=lambda m: m.start())
    for match in matches:
        star = source.rfind("*(", match.start(), match.end())
        if star < cursor:
            continue
        before = source[max(0, star - 12):star]
        if CAST_CONTEXT.search(before):
            continue          # the `*` belongs to a cast, not to a dereference
        close = _matching_close(source, star + 1)
        if close < 0:
            break
        chain = source[star + 2:close]
        if "\n" in chain or "|" not in chain:
            continue
        parts = _operands(chain)
        if len(parts) < 2:
            continue
        rebuilt = " | ".join(
            part.strip() if ALREADY_INTEGER.match(part.strip()) else f"(u32){part.strip()}"
            for part in parts)
        replacement = f"*({CAST} *)({rebuilt})"
        changes.append({"line": source.count("\n", 0, star) + 1,
                        "before": source[star:close][:80], "after": replacement[:80]})
        out.append(source[cursor:star])
        out.append(replacement)
        cursor = close + 1
    out.append(source[cursor:])
    return "".join(out), changes


def main(argv: list[str] | None = None) -> int:
    import argparse
    import hashlib
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    text = args.source.read_text(encoding="utf-8", errors="replace")
    rewritten, changes = rewrite(text)
    if args.out:
        args.out.write_text(rewritten, encoding="utf-8")
    print(json.dumps({"changes": changes, "unchanged": rewritten == text,
                      "sha256": hashlib.sha256(rewritten.encode()).hexdigest()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
