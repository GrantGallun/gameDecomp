"""Supply verified facts from the knowledge base to the solver's prompt.

This is the thesis in miniature. The model proposes, but it should not have to
guess at things the binary states outright: a `lhu` at param0+0x24 means the
field there is two bytes and read unsigned. That is evidence, mechanically
extracted and validated at 100% against SBK1's real struct layouts across 2,842
checks -- so it can be handed to the model as fact rather than hypothesis.

Why this should matter most where we have the least data: 91.5% of SBK1's game
functions are non-leaf, and the harder ones touch structs whose layouts m2c
cannot infer from one function alone. The KB sees every access to those types
across all 2,113 functions.

Nothing here reads decompiled source. Evidence comes from the binary, so this
carries no contamination risk on an already-matched target.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

ACCESS_SQL = """
SELECT base, offset, width, signed, class, is_load, op, access
FROM evidence
WHERE kind = 'mem_access'
  AND func_addr = (SELECT addr FROM functions WHERE name = ?)
  AND base != 'unknown'
  AND width IS NOT NULL
ORDER BY base, offset
"""

CALLEE_SQL = """
SELECT DISTINCT tf.name
FROM evidence e
JOIN functions f  ON f.addr = e.func_addr
JOIN functions tf ON tf.addr = e.target_addr
WHERE e.kind = 'call' AND f.name = ?
ORDER BY tf.name
"""


def _describe(rows) -> list[str]:
    """One line per storage location, merging every access that touched it."""
    by_loc = defaultdict(list)
    for r in rows:
        by_loc[(r["base"], r["offset"])].append(r)

    lines = []
    for (base, offset), accesses in sorted(
            by_loc.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        widths = sorted({a["width"] for a in accesses})
        classes = sorted({a["class"] for a in accesses})

        # Signedness is only claimed by loads; stores carry no such information.
        signs = {a["signed"] for a in accesses if a["is_load"] and a["signed"] is not None}
        if signs == {1}:
            sign = "signed"
        elif signs == {0}:
            sign = "unsigned"
        elif len(signs) > 1:
            sign = "read both signed and unsigned (a cast; one underlying type)"
        else:
            sign = "signedness unknown (only stored, never loaded)"

        reads = sum(1 for a in accesses if a["is_load"])
        writes = len(accesses) - reads
        width = "/".join(str(w) for w in widths)
        kind = "float" if "float" in classes else "int"
        extra = "  [MULTIPLE WIDTHS -- likely a union]" if len(widths) > 1 else ""

        loc = f"{base}+{offset:#x}" if offset >= 0 else f"{base}{offset:#x}"
        lines.append(f"  {loc:24} {width} byte {kind}, {sign}"
                     f"  ({reads} read, {writes} write){extra}")
    return lines


def for_function(conn: sqlite3.Connection, func: str, max_lines: int = 40) -> str:
    """A prompt block of verified facts about this function, or "" if none."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(ACCESS_SQL, (func,)).fetchall()
    if not rows:
        return ""

    lines = _describe(rows)
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]

    out = ["\nOBSERVED MEMORY ACCESSES (extracted from the target binary; these "
           "are facts, not guesses -- declare types to match them):"]
    out.extend(lines)
    if truncated:
        out.append(f"  ... and more; showing the first {max_lines}")

    callees = [r[0] for r in conn.execute(CALLEE_SQL, (func,)).fetchall()]
    if callees:
        shown = ", ".join(callees[:12])
        more = f" (+{len(callees)-12} more)" if len(callees) > 12 else ""
        out.append(f"\nCALLS: {shown}{more}")
        out.append("  Declare each callee with the argument and return types its "
                   "use here implies. Wrong arity or types change codegen.")

    return "\n".join(out) + "\n"


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--function", required=True)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db.expanduser())
    block = for_function(conn, args.function)
    print(block if block else "(no KB facts for this function)")
