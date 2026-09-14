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

The evidence path reads nothing but the binary, so it carries no contamination
risk on an already-matched target. The inference path below is different: it
emits whatever claims the KB holds, and on a ceiling KB those claims came from
the reference decomp's own headers. That is why the taint lives in the database
rather than here -- see `kb/provenance.py`. A clean KB has an empty inference
tier and this module behaves exactly as it did before that path existed.
"""

from __future__ import annotations

import bisect
import json
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

# --------------------------------------------------------------- inference

# Claims, not observations. On a clean KB both queries return nothing and this
# whole path is inert, so behaviour is unchanged until something populates the
# inference tier -- today only the ceiling import does.
SIGNATURE_SQL = """
SELECT value FROM inference
WHERE kind = 'signature' AND subject = ? AND status = 'active'
ORDER BY confidence DESC, id DESC LIMIT 1
"""

STRUCT_FIELD_SQL = """
SELECT subject, value, origin FROM inference
WHERE kind = 'field' AND subject LIKE ? AND status = 'active'
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


def _struct_layout(conn, sname: str, touched: set[int] | None = None,
                   max_fields: int = 24) -> list[str]:
    """Declared fields of one struct, from the inference tier.

    Big structs must be cut down -- RacePlayer has 193 fields -- but cutting
    them at the first 24 is the wrong 24. A function reading `param0+0x2fc`
    got a layout that stopped at 0x28, omitting the only field it needed. So
    when the evidence says which offsets this function touches, keep those and
    elide the rest; fall back to a prefix only when nothing is known.
    """
    rows = conn.execute(STRUCT_FIELD_SQL, (f"struct:{sname}@%",)).fetchall()
    fields = []
    for subject, value, origin in rows:
        try:
            offset = int(subject.rsplit("@", 1)[1], 16)
            v = json.loads(value)
        except (IndexError, ValueError):
            continue
        decl = f"{v['type']}{' *' if v.get('is_pointer') else ' '}{v['name']}"
        if v.get("elem_count", 1) > 1:
            decl += f"[{v['elem_count']}]"
        fields.append((offset, decl))
    if not fields:
        return []

    fields.sort()
    if touched:
        # A field covers an access if the access lands at or after it and
        # before the next field starts.
        starts = [off for off, _ in fields]
        keep = set()
        for off in touched:
            i = bisect.bisect_right(starts, off) - 1
            if i >= 0:
                keep.add(i)
        # One field either side for context; a lone field with no neighbours
        # reads as a guess rather than a layout.
        keep |= {i - 1 for i in keep if i > 0}
        keep |= {i + 1 for i in keep if i + 1 < len(fields)}
        chosen = sorted(keep)[:max_fields]
    else:
        chosen = list(range(min(len(fields), max_fields)))

    if not chosen:
        return []

    # Every gap gets a marker, the leading one included. Without it a window
    # starting at 0x2f8 reads as a struct whose first field is at 0x2f8, which
    # is a false layout rather than a partial one.
    out = []
    if chosen[0] > 0:
        out.append(f"  /* ... {chosen[0]} fields omitted ... */")
    prev = None
    for i in chosen:
        if prev is not None and i > prev + 1:
            out.append(f"  /* ... {i - prev - 1} fields omitted ... */")
        out.append(f"  /* {fields[i][0]:#06x} */ {fields[i][1]};")
        prev = i
    if prev < len(fields) - 1:
        out.append(f"  /* ... {len(fields) - 1 - prev} fields omitted ... */")
    return out


def types_for_function(conn: sqlite3.Connection, func: str,
                       touched: dict[str, set[int]] | None = None) -> str:
    """Declared signature and parameter struct layouts, from the inference tier.

    Empty on a clean KB. These are *claims* -- a struct layout is not something
    the binary states, unlike a load width -- so the block says where they came
    from and the model is told they may be wrong. An inference presented as
    evidence is exactly the confusion invariant 3 exists to prevent.
    """
    row = conn.execute(SIGNATURE_SQL, (f"func:{func}",)).fetchone()
    if not row:
        return ""
    try:
        params = json.loads(row[0]).get("params") or []
    except ValueError:
        return ""

    # Keep the parameter position: param0's offsets describe params[0]'s type.
    named = {p: f"param{i}" for i, p in enumerate(params) if p}
    if not named:
        return ""

    out = ["\nDECLARED TYPES (from the project's existing headers -- these are "
           "claims, not binary facts; prefer the observed accesses above where "
           "they disagree):"]
    for sname, slot in named.items():
        layout = _struct_layout(conn, sname, (touched or {}).get(slot))
        if layout:
            out.append(f"\ntypedef struct {sname} {{")
            out.extend(layout)
            out.append(f"}} {sname};")
    return "\n".join(out) + "\n" if len(out) > 1 else ""


def for_function(conn: sqlite3.Connection, func: str, max_lines: int = 40) -> str:
    """A prompt block of verified facts about this function, or "" if none."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(ACCESS_SQL, (func,)).fetchall()

    touched: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        if r["base"].startswith("param"):
            touched[r["base"]].add(r["offset"])
    types = types_for_function(conn, func, touched)
    if not rows:
        return types

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

    return "\n".join(out) + "\n" + types


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
