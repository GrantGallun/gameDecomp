"""Write out C89's implicit declaration, `extern int f();`, for a real ROM function called for effect.

WHAT THIS OWNS. `implicit declaration of function 'f'` where `f` is a real function in the binary, no
header declares it, and every call discards the result. Measured on the frozen frame
(`wide-intake-widen3.json`) this is three sole-blocked states:

    updateRaceUiResultsBannerWaitForInput   enqueueSoundEffect(0x18, 0x32);
    updateCharacterSelectMenu               enqueueSoundEffect(...);  x3
    drawMainMenuModeSelectIcons             drawMenuFillRectangle(...);  x2

`scalar_header_prototypes` cannot reach them because `project_headers.declarations` returns ZERO for
both names -- they are declared in no header at all (LOOP-3). They are nonetheless real: the KB's
`functions` table has `enqueueSoundEffect` at 0x80072138 and `drawMenuFillRectangle` at 0x80046748.

WHY THIS CHANGES NOTHING IN THE OBJECT. In C89 a call to an undeclared function IS an implicit
`extern int f();` -- unprototyped, default argument promotions. IDO compiles the call exactly that way,
which is why these states already produce an object; only the project's clang check rejects them, by
policy (`-Werror=implicit-function-declaration`). Writing the declaration out states what the compiler
already assumed. No parameter type is invented (the list stays `()`), and the `int` return is the
language's default rather than a claim about the callee.

THE GUARDS, each measured against a real case that must NOT be declared:

  * The callee must be a ROM FUNCTION. `M2C_BREAK` and `M2C_MEMCPY_ALIGNED` are absent from the
    `functions` table because they are m2c spellings for a `break` instruction and a bulk copy. A
    prototype would compile them into a `jal` to a symbol the ROM does not have (LOOP-3's trap).
  * No header may declare it. A real prototype outranks the implicit one, and `scalar_header_prototypes`
    owns that case.
  * EVERY call must discard its result. `temp_ret = __ll_mul(...)` uses a 64-bit return, and
    `extern int __ll_mul();` would truncate it -- that would change the object, which this pass must
    never do.
"""
from __future__ import annotations

import hashlib
import re

from solver import project_headers, repair_context

_IMPLICIT = re.compile(r"implicit declaration of function '([A-Za-z_]\w*)'")


def _calls(body: str, name: str):
    """Yield (before_char, after_char) around each call of `name` in a MASKED body."""
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", body):
        depth, end = 1, match.end()
        while end < len(body) and depth:
            depth += (body[end] == "(") - (body[end] == ")")
            end += 1
        if depth:
            yield None, None
            continue
        before = body[:match.start()].rstrip()
        after = body[end:].lstrip()
        prev_word = re.search(r"([A-Za-z_]\w*)\s*$", before)
        yield ((prev_word.group(1) if prev_word and prev_word.group(1) == "else"
                else (before[-1:] or "{")),
               after[:1])


def result_discarded(body: str, name: str) -> bool:
    """True when every call of `name` is a whole statement, so its result is never read.

    A statement call is preceded by a statement boundary (`;`, `{`, `}`), by `)` closing an
    `if`/`while`/`for` condition or a `(void)` cast, or by `else`, and is followed by `;`.
    Anything else -- `=`, `return`, an operator, an argument position -- reads the result.
    """
    seen = False
    for before, after in _calls(body, name):
        seen = True
        if before is None or after != ";" or before not in (";", "{", "}", ")", "else"):
            return False
    return seen


def propose(source: str, function: str, diagnostics: str, rom_functions: set[str],
            repo=None) -> tuple[str, dict]:
    """Prepend `extern int f();` for each implicitly declared name that passes every guard."""
    report: dict = {"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "declared": [], "declines": [],
                    "authority": "C89's own implicit declaration, written out; the object is unchanged"}
    names = sorted(set(_IMPLICIT.findall(diagnostics or "")))
    if not names:
        return source, report
    try:
        definition, end = repair_context.definition(source, function)
    except ValueError as exc:
        report["declines"].append({"reason": str(exc)})
        return source, report
    body = project_headers._mask_noncode(source)[definition.end():end]
    additions = []
    for name in names:
        if name not in rom_functions:
            report["declines"].append({"name": name, "reason": (
                "not a function in the ROM, so a prototype would compile into a jal to a symbol "
                "that does not exist -- an m2c spelling, not a callee")})
            continue
        if repo is not None and project_headers.declarations(repo, name):
            report["declines"].append({"name": name, "reason": (
                "a header declares it, and a real prototype outranks the implicit one")})
            continue
        if re.search(rf"\b{re.escape(name)}\s*\(", project_headers._mask_noncode(source)[:definition.start()]):
            report["declines"].append({"name": name, "reason": "already declared in this candidate"})
            continue
        if not result_discarded(body, name):
            report["declines"].append({"name": name, "reason": (
                "a call reads the result, so `int` would be a claim about the return type and could "
                "change the object")})
            continue
        additions.append(f"extern int {name}();")
        report["declared"].append(name)
    if not additions:
        return source, report
    return "\n".join(additions) + "\n" + source, report
