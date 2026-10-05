"""Widen an extern declaration WE hypothesised, when the checker states the type the use requires.

WHAT THIS OWNS. `incompatible integer to pointer conversion` on a name the intake route itself
declared. `globals_variant` and `undeclared_identifiers` type an undeclared datum from access WIDTH --
a four-byte access becomes `extern s32 X;` -- and a width is not a type. When the same datum is used as
an address, the candidate is left asserting `s32` where the context needs a pointer:

    extern s32 gRaceItemEffectSpriteIds;        var_s0 = gRaceItemEffectSpriteIds;
      -> assigning to 'u16 *' (aka 'unsigned short *') from 's32' (aka 'long')

This became the frame's largest sole blocker (65 states, 8 sole) directly BECAUSE the declaration
passes started firing: every state they converted hit this as its next wall. The histogram predicted
exactly that -- clear one wall and the next becomes visible.

WHY IT IS INFERENCE AND NOT INVENTION. The declaration being replaced is the project's OWN earlier
hypothesis, and the replacement's type is **the checker's own statement of what the context requires**.
Nothing is read from the target's source and no field, size or layout is chosen. This cites its
evidence (the diagnostic) and is retractable, which is what the inference tier is for.

THREE SHAPES, decided by how the draft uses the name:

    indexed   `p = X[i];`          the ELEMENT is `T *`        -> extern T *X[];
    assigned  `X++;` / `X = p;`    a pointer OBJECT            -> extern T *X;
    bare      `p = X;` only        the array decays to `T *`   -> extern T X[];

`assigned` was added after the bare rule shipped without it and produced 604 `array type 'Gfx[]'
is not assignable` errors: an array cannot be assigned, so a cursor like `gDisplayListHead++` is a
pointer object. Whether a name is assigned is read from the candidate's own text.

THE TIER IS A PROPERTY OF THE CANDIDATE, NOT OF THIS PASS. A primitive pointee (`u16 *`) is a
fact about width and is always adopted. A struct pointee (`MenuGlyphScript *`) is adopted only
when the candidate ALREADY includes a reconstructed `include/game/**` header -- clang could not
have named the type otherwise, and the layout arrived with that include, so nothing new is taken.
The first version refused every struct pointee; measured, two of the three states it refused
already carried 5-6 game headers, so the refusal protected nothing. A struct pointee in a
candidate with NO game header still abstains, because there it would be new assistance.
Every plan records `pointee_is_primitive` and `game_headers`, so the tier travels with the gain.
"""
from __future__ import annotations

import hashlib
import re

# Widths as the build's C sees them; `long` is 32-bit under N64 o32. Membership decides the TIER a
# plan is recorded under, and gates only when the candidate has no game header in scope.
PRIMITIVE_POINTEES: frozenset[str] = frozenset({
    "s8", "u8", "char", "signed char", "unsigned char",
    "s16", "u16", "short", "unsigned short",
    "s32", "u32", "int", "unsigned int", "long", "unsigned long",
    "f32", "float",
})

# `incompatible integer to pointer conversion assigning to 'u16 *' (aka 'unsigned short *') from 's32'`
# and the `passing`/`returning` wordings. The pointee is group `pointee`; the aka is ignored on purpose
# -- the SPELLING clang used first is the one the project writes.
# FOUR WORDINGS, AND THE POINTEE IS NOT IN THE SAME PLACE IN ANY TWO OF THEM. Measured against clang
# 20.1.2, the same conversion is reported as:
#
#   assigning to 'u16 *' (aka '...') from 's32'
#   initializing 'u16 *' (aka '...') with an expression of type 's32'
#   passing 's32' (aka 'long') to parameter of type 'u16 *' (aka '...')
#   returning 's32' (aka 'long') from a function with result type 'u16 *' (aka '...')
#
# The first version anchored on `(?:assigning to|passing|returning)[^']*'(pointee) \*'`, and `[^']*`
# cannot cross a quote -- so on `passing` and `returning`, where `'s32'` comes first, it never reached
# the pointee at all and the pass silently matched only one wording of four.
#
# So the pointee is taken as THE FIRST QUOTED TYPE ENDING IN `*`, which lands on `'u16 *'` in all four
# and never on an `(aka ...)` spelling, because the aka always follows the type it explains.
_CONVERSION = re.compile(
    r"^candidate\.c:(?P<line>\d+):(?P<col>\d+):\s*error:\s*incompatible integer to pointer conversion "
    r"(?P<body>.*)$",
    re.MULTILINE)
_POINTEE = re.compile(r"'(?P<pointee>[A-Za-z_][\w ]*?)\s*\*'")

# An extern this route emitted: a scalar, or an incomplete/ sized array of one.
_DECLARATION = re.compile(
    r"(?m)^(?P<indent>[ \t]*)extern\s+(?P<type>[A-Za-z_][\w ]*?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?P<array>\[[^\]]*\])?\s*;[ \t]*$")


def _declarations(source: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for match in _DECLARATION.finditer(source):
        found[match["name"]] = {
            "start": match.start(), "end": match.end(), "type": match["type"].strip(),
            "array": bool(match["array"]), "text": match.group(0).strip(),
            "indent": match["indent"]}
    return found


_GAME_INCLUDE = re.compile(r'(?m)^\s*#\s*include\s+"(game/[^"]+)"')


def _game_headers(source: str) -> list[str]:
    """The reconstructed `include/game/**` headers this candidate already includes.

    Non-empty means the candidate is header-assisted REGARDLESS of what this pass does, because the
    layouts those headers carry are already in scope. Public SDK headers (`PR/**`) are not counted.
    """
    return _GAME_INCLUDE.findall(source)


def _assigned(masked: str, name: str) -> bool:
    """True when `name` itself -- not an element, not a member -- is written anywhere in the candidate.

    `X = ...`, `X += ...` and every compound form, `X++`, `++X`, `X--`, `--X`. `X[i] = ...` and
    `X->f = ...` are writes THROUGH the name, not to it, and `X == ...` is a comparison.
    """
    n = re.escape(name)
    return bool(re.search(rf"\b{n}\s*(?:[+\-*/%&|^]|<<|>>)?=(?!=)", masked)
                or re.search(rf"(?:\+\+|--)\s*\b{n}\b|\b{n}\s*(?:\+\+|--)", masked))


def rewrite(source: str, diagnostics: str) -> tuple[str, list[dict]]:
    """Widen the declarations the diagnostics implicate. Returns the source and every decision."""
    changes: list[dict] = []
    if "incompatible integer to pointer conversion" not in (diagnostics or ""):
        return source, changes
    declared = _declarations(source)
    if not declared:
        return source, changes
    lines = source.splitlines()
    from solver import project_headers
    masked = project_headers._mask_noncode(source)
    edits: dict[str, tuple[int, int, str]] = {}

    for match in _CONVERSION.finditer(diagnostics):
        line_no, column = int(match["line"]), int(match["col"])
        pointee_match = _POINTEE.search(match["body"])
        record: dict = {"line": line_no, "column": column}
        if not pointee_match:
            record["declined"] = "the conversion names no pointer type"
            changes.append(record)
            continue
        pointee = pointee_match["pointee"].strip()
        record["pointee"] = pointee
        if not 1 <= line_no <= len(lines):
            record["declined"] = "the diagnostic points outside the candidate"
            changes.append(record)
            continue
        text = lines[line_no - 1]
        # THE COLUMN IS THE DISAMBIGUATOR, and it was dismissed on an assumption. This first took the one
        # declared name on the line and abstained when there were several -- which abstained on
        # `temp_a2 = mainMenuModeDescriptionTitles[gMainMenuModeSelection];`, where BOTH names are
        # declared and only the first is being converted. Measured against clang 20.1.2, the column
        # points at the START OF THE CONVERTED EXPRESSION in all four wordings: on
        # `    u16 *a = titles[gSel];` it is column 10, which is `titles`, not the index.
        name = None
        tail = text[column - 1:] if 0 < column <= len(text) else ""
        head = re.match(r"[A-Za-z_]\w*", tail)
        if head and head.group(0) in declared:
            name = head.group(0)
        else:
            # The column did not land on a name we declared. Fall back to the line, and still refuse
            # to choose between several.
            implicated = [n for n in declared if re.search(rf"\b{re.escape(n)}\b", text)]
            if len(implicated) != 1:
                record["declined"] = (
                    f"the column does not name a declaration we emitted and {len(implicated)} such "
                    "names are on that line, so the subject is ambiguous")
                record["candidates"] = sorted(implicated)
                changes.append(record)
                continue
            name = implicated[0]
        record["name"] = name
        record["pointee_is_primitive"] = pointee in PRIMITIVE_POINTEES
        record["game_headers"] = _game_headers(source)
        # THE TIER IS A PROPERTY OF THE CANDIDATE, NOT OF THIS PASS -- corrected 2026-09-21 after
        # measuring the states this abstained on.
        #
        # This used to refuse every non-primitive pointee, on the reasoning that naming
        # `MenuGlyphScript *` would take a struct layout from a reconstructed `include/game/**` header
        # and make the repair header-assisted. Measured on the three states it was abstaining on:
        #
        #   drawRaceSetupPlayerCountPrompt   5 of 6 includes are game/**
        #   waitCourseSelectRecordsClose     6 of 7 includes are game/**
        #   acquireSoundEffectHandleNode     0 of 1  -- genuinely binary-only
        #
        # So for two of three the assistance had ALREADY been taken by `header_variant`, and abstaining
        # protected nothing. And clang could not print `MenuGlyphScript *` unless that type were already
        # declared in this candidate, so adopting it invents no layout -- the layout arrived with the
        # include, whether or not this pass names it.
        #
        # `pointee_is_primitive` and `game_headers` are therefore RECORDED rather than gated on, so the
        # tier travels with the gain. That is also the gap `eval/status.py` documents about itself: it
        # keys header assistance on a strategy string and is blind to a candidate that reaches the same
        # headers by `#include`.
        if not record["pointee_is_primitive"] and not record["game_headers"]:
            record["declined"] = (
                f"{pointee!r} is not a primitive scalar and this candidate includes no game/** header, "
                "so adopting it would be NEW assistance rather than assistance already taken")
            changes.append(record)
            continue
        entry = declared[name]
        # Indexed use means the ELEMENT is the pointer; a bare use means the array decays to one.
        indexed = bool(re.search(rf"\b{re.escape(name)}\s*\[", text))
        # AN ASSIGNED NAME IS A POINTER OBJECT, NEVER AN ARRAY -- corrected 2026-09-21, after this pass
        # caused the frame's largest residual.
        #
        # The bare-use rule was "`p = X;` means X decays to `T *`, so declare `extern T X[];`". That is a
        # hypothesis about the program, encoded without testing, and it is false whenever the program
        # also ASSIGNS X -- a display-list cursor doing `gDisplayListHead++` is a pointer variable, and C
        # forbids assigning an array. Measured: when struct pointees were admitted (LOOP-5, `widen3`),
        # `unclassified` went from 123 errors in 39 states to 729 in 59, 604 of them one message --
        # `array type 'Gfx[]' is not assignable` -- and this pass had fired in 35 of those 59.
        #
        # It also fooled the quality ratchet: each state traded many `int -> pointer` errors (one per
        # use) for fewer `array not assignable` errors (one per assignment), so per-state totals FELL and
        # 46 states read as "better" while carrying a different wrong declaration. A lower error count is
        # not a more correct candidate.
        #
        # Whether a name is assigned is a fact about the candidate's own text, so this invents nothing.
        assigned = _assigned(masked, name)
        record["assigned"] = assigned
        if indexed and assigned:
            record["declined"] = ("the name is both indexed and assigned as a whole, so array-of-"
                                  "pointers and pointer-to-pointer both fit and choosing is a guess")
            changes.append(record)
            continue
        if indexed:
            replacement = f"{entry['indent']}extern {pointee} *{name}[];"
        elif assigned:
            replacement = f"{entry['indent']}extern {pointee} *{name};"
        else:
            replacement = f"{entry['indent']}extern {pointee} {name}[];"
        if replacement.strip() == entry["text"]:
            record["declined"] = "the declaration already says that"
            changes.append(record)
            continue
        previous = edits.get(name)
        if previous and previous[2] != replacement:
            record["declined"] = "two conversions on this name disagree about its pointee"
            changes.append(record)
            continue
        edits[name] = (entry["start"], entry["end"], replacement)
        record.update(before=entry["text"], after=replacement.strip(), indexed=indexed,
                      authority="the checker states the required pointee; the prior extern was a "
                                "width hypothesis from this same route")
        changes.append(record)

    for start, end, replacement in sorted(edits.values(), reverse=True):
        source = source[:start] + replacement + source[end:]
    if edits:
        changes.append({"applied": len(edits),
                        "source_sha256": hashlib.sha256(source.encode()).hexdigest()})
    return source, changes
