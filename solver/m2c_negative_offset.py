"""m2c's negative field offsets, which are not C.

THE RESIDUAL THIS OWNS. On the size-bucketed frame (`eval/results/intake-20260921`) this is the largest
identified construct among states that still will not compile -- 8 of 31 by first error, and more once the
first blocker is passed, because one draft carries several:

    temp_v0->unk-4 = (s32) filter->unk14;
    var_v0_5->unk-604 = 9;
    var_s1_2->unk-1828 = 3;

`->unk-4` is not a member access. The identifier ends at `unk`, and `- 4` is a subtraction on it, so the
front end either refuses the line (cfe: `Syntax Error`) or reports what it actually did -- clang says
`member reference base type 'void' is not a structure or union`, which is the SAME defect seen from a
parser that got further. That diagnostic difference is why `solver/negative_field_repair.py` looks dead:
it is not. It gates on clang's message and is called from `solver/modelrepair.py:759`; it declines here
only because the intake route never runs the clang frontend, so the gate has nothing to match on. Both
routes are kept -- this one needs no frontend.

WHY A REWRITE AND NOT AN INFERENCE. The author of the draft already said what it means: a field four bytes
BELOW the base. That is a byte-view access, and the spelling is forced -- pointer arithmetic, because C
requires the pointee type to fix the scale, and an `unsigned char *` intermediate is what makes the offset
a byte offset rather than a scaled one. Nothing is enumerated because nothing is uncertain; the compiler
still decides whether the result is right.

WHAT IS DELIBERATELY NOT DONE. No type is invented. A base declared as a pointer is left alone (the
compiler will say something more useful than a byte view), and a base with no declaration in this source
is left alone naming that. Both declines are recorded per use rather than collapsed into "no change".
"""
from __future__ import annotations

import re

# The base and the literal. The base is an identifier because that is what m2c emits for a lost type;
# a cast expression or a call is left alone, and `\b` after the digits stops `unk-4x` from matching.
USE = re.compile(r"\b(?P<base>[A-Za-z_]\w*)\s*->\s*unk-(?P<offset>[\da-fA-F]+)\b")
# `T *base;` / `T base;` -- the declaration that says what the base is. The space before the `*` is
# optional and MUST be allowed: m2c writes `void *temp_v0;`, and the first version of this pattern
# required the star to touch the name, so every base looked undeclared and the rewrite declined on the
# exact residual it was written for. tests/test_m2c_negative_offset.py caught it.
DECL = re.compile(r"(?m)^(?P<indent>[ \t]*)(?P<type>[A-Za-z_]\w*)[ \t]*(?P<stars>\*+)?[ \t]*"
                  r"(?P<name>[A-Za-z_]\w*)\s*;")

# Types whose width is FIXED and known, so `T base;` is a byte-viewable scalar and `T *base;` is a
# pointer to one rather than to a struct whose fields the compiler could resolve.
SCALARS = frozenset({"s8", "u8", "s16", "u16", "s32", "u32", "s64", "u64", "f32", "f64",
                     "char", "short", "int", "long", "float", "double"})

# The access width `unk-N` does not state. `s32` is IDO's implicit int, and every observed use takes the
# value as a scalar; a caller that has evidence for a narrower access passes `access`.
ACCESS = "s32"


def rewrite(source: str, *, access: str = ACCESS) -> tuple[str, list[dict]]:
    """`base->unk-N` -> a byte-view access. Returns (source, changes).

    Offsets are collected from the ORIGINAL text and spliced right-to-left, so repeated spellings cannot
    mis-target and earlier edits cannot shift later ones.
    """
    changes: list[dict] = []
    declarations = {match.group("name"): match for match in DECL.finditer(source)}
    edits: list[tuple[int, int, str]] = []
    for use in USE.finditer(source):
        base = use.group("base")
        line = source.count("\n", 0, use.start()) + 1
        declaration = declarations.get(base)
        if declaration is None:
            changes.append({"line": line, "before": use.group(0),
                            "declined": "the base has no declaration in this source"})
            continue
        declared_type = declaration.group("type")
        if declaration.group("stars") and declared_type in SCALARS:
            changes.append({"line": line, "before": use.group(0), "base": base,
                            "declined": f"the base is already a pointer to a known scalar "
                                        f"({declared_type} {declaration.group('stars')}{base})"})
            continue
        if declared_type not in SCALARS and declared_type != "void":
            # A named struct/enum type: the compiler can resolve `base->field` itself, and a byte view
            # would throw that away. The draft's `unk` is a LABEL, not a member of this type.
            changes.append({"line": line, "before": use.group(0), "base": base,
                            "declined": f"the base is a declared type ({declared_type}), whose members "
                                        f"the compiler can resolve without a byte view"})
            continue
        offset = int(use.group("offset"), 16)
        if offset == 0:
            changes.append({"line": line, "before": use.group(0), "base": base,
                            "declined": "a zero offset has no byte view"})
            continue
        replacement = f"(*( {access} *)((unsigned char *){base} - 0x{offset:X}))"
        edits.append((use.start(), use.end(), replacement))
        changes.append({"line": line, "before": use.group(0), "base": base, "byte_offset": -offset,
                        "after": replacement})
    if not edits:
        return source, changes
    out = source
    for start, end, replacement in reversed(edits):
        out = out[:start] + replacement + out[end:]
    return out, changes


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
                      "rewritten_sha256": hashlib.sha256(rewritten.encode()).hexdigest()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
