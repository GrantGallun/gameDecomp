"""The union placeholder rule, isolated so a checker can import it without running a measurement.

WHY THIS FILE EXISTS. The two-pass measurement lived at module level in `_placeholder_clean.py`, so
importing that file for its `union` rule re-ran the whole measurement -- the arm table printed twice in
the middle of an unrelated check. A helper that does work on import cannot be reused, which is the same
shape as the action-comparison defect: the thing that looks like a library is also a program.

The rule itself is small: apply `solver.m2c_placeholders.rewrite` (the module's own two rules), then a
`? name` substitution over whatever it left. It is a MEASUREMENT of what a fix would be worth, not a fix:
the `? name` case is now handled inside the module, so the second pass fires on nothing and the union
equals the module. It is kept because the check that proves that is this file.
"""
from __future__ import annotations

import re

from solver import m2c_placeholders

# `? name` anywhere a type specifier may stand, comments and strings masked first so a `?` in prose is
# never touched -- the same masking rule the module already uses, extended to the position it missed.
ANY_DECL = re.compile(r"(?P<lead>(?<![\w?])\?)(?P<stars>\s*\*+)?\s*(?P<name>[A-Za-z_]\w*)")


def widened(code: str, default: str = "s32") -> tuple[str, int]:
    """The `? name` substitution alone. Narrower than the module: it misses `(?` (nameless)."""
    masked = m2c_placeholders._masked(code)
    hits = [m for m in ANY_DECL.finditer(masked)]
    if not hits:
        return code, 0
    out, cursor = [], 0
    for match in hits:
        out.append(code[cursor:match.start("lead")])
        stars = (match.group("stars") or "").replace(" ", "")
        out.append(f"{default} {stars}{match.group('name')}" if stars
                   else f"{default} {match.group('name')}")
        cursor = match.end()
    out.append(code[cursor:])
    return "".join(out), len(hits)


def union(code: str) -> tuple[str, int]:
    """Module first, then the widened rule over what is left: neither undoes the other's work."""
    once, names = m2c_placeholders.rewrite(code)
    twice, hits = widened(once)
    return twice, len(names) + hits
