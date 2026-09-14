"""Golden test: check extracted evidence against a finished decomp's real types.

This is the answer to "how do we stop shipping analysis bugs". SBK1 is 100%
matched, so its struct headers are the truth. For every function whose param0
is a known struct pointer, every memory access through param0 must agree with
that struct's actual field layout. Disagreement means one of:

  - a bug in miner/evidence.py                      (fix it)
  - a compiler pattern we have not modelled          (model it, like bulk copies)
  - a genuine quirk                                  (whitelist it, with a reason)

Nothing is tolerated silently. The whitelist is code, it carries reasons, and
the test fails on anything not in it -- so a new disagreement is a regression,
not a shrug. That is the ratchet applied to our own analysis quality.

Run:
    python3 -m eval.validate_evidence --repo ~/decomp/sbk1 --db kb-sbk1.sqlite
Exit code is non-zero if unexplained disagreements appear.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

from eval.ground_truth import flatten, load
from kb.contradictions import mark_bulk_copies

# Disagreements we have investigated and accept, with the reason. Keyed by
# category so the test stays readable as it grows.
KNOWN_EXCEPTIONS = {
    "subfield_read": (
        "A load narrower than the declared field, landing on its start offset. "
        "Big-endian MIPS reads the high half of an s32 with `lh` at the same "
        "address, so this is legitimate C, not a layout error."
    ),
}


def evidence_for_params(conn):
    rows = conn.execute("""
        SELECT e.addr, e.func_addr, f.name AS func_name, e.base, e.offset,
               e.width, e.signed, e.class, e.access, e.is_load, e.op
        FROM evidence e
        JOIN functions f ON f.addr = e.func_addr
        WHERE e.kind = 'mem_access'
          AND e.base LIKE 'param%'
          AND e.width IS NOT NULL
    """)
    return [dict(r) for r in rows]


def validate(repo: Path, db: Path, verbose: bool = False) -> int:
    structs, sigs = load(repo)
    flattened = {name: flatten(name, structs) for name in structs}
    layouts = {n: v[0] for n, v in flattened.items()}
    pad_only = {n: v[1] for n, v in flattened.items()}

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = evidence_for_params(conn)
    copies = mark_bulk_copies(rows)

    stats = Counter()
    disagreements = []
    per_struct = defaultdict(Counter)

    for row in rows:
        param_index = int(row["base"].removeprefix("param"))
        types = sigs.get(row["func_name"])
        if not types or param_index >= len(types):
            stats["no_signature"] += 1
            continue

        struct_name = types[param_index]
        if not struct_name:
            stats["param_not_struct"] += 1
            continue

        layout = layouts.get(struct_name)
        if not layout:
            stats["struct_unknown"] += 1
            continue

        offset = row["offset"]
        if offset < 0:
            stats["negative_offset"] += 1
            continue

        if offset not in layout:
            # Past the end of the declared struct, or an unresolved field type.
            stats["offset_uncovered"] += 1
            continue

        if offset in pad_only.get(struct_name, ()):
            # Only padding describes this offset, so no access width there is
            # falsifiable. Counting it as agreement would inflate accuracy.
            stats["offset_in_padding"] += 1
            continue

        # A set: unions make an offset legitimately polymorphic.
        expected = layout[offset]
        actual = row["width"]

        if row["addr"] in copies:
            stats["bulk_copy_excluded"] += 1
            continue

        if actual in expected:
            stats["agree"] += 1
            per_struct[struct_name]["agree"] += 1
            continue

        # A narrower load at a field's start offset is a legitimate subfield
        # read on big-endian MIPS, not a layout disagreement.
        if row["is_load"] and actual < max(expected):
            stats["subfield_read"] += 1
            per_struct[struct_name]["subfield_read"] += 1
            continue

        stats["DISAGREE"] += 1
        per_struct[struct_name]["disagree"] += 1
        disagreements.append({
            "func": row["func_name"], "struct": struct_name,
            "offset": offset, "expected": sorted(expected), "actual": actual,
            "op": row["op"], "addr": row["addr"],
        })

    checked = stats["agree"] + stats["DISAGREE"] + stats["subfield_read"]
    accuracy = (100.0 * stats["agree"] / checked) if checked else 0.0

    print("=" * 62)
    print("EVIDENCE VALIDATION vs known-good struct layouts")
    print("=" * 62)
    print(f"param accesses in KB     : {len(rows)}")
    print(f"checkable against truth  : {checked}")
    print(f"  agree                  : {stats['agree']}")
    print(f"  subfield read (ok)     : {stats['subfield_read']}")
    print(f"  DISAGREE               : {stats['DISAGREE']}")
    print(f"accuracy                 : {accuracy:.2f}%")
    print()
    print("not checkable (honest coverage gaps):")
    for k in ("no_signature", "param_not_struct", "struct_unknown",
              "offset_uncovered", "offset_in_padding", "negative_offset",
              "bulk_copy_excluded"):
        print(f"  {k:24} {stats[k]}")

    if disagreements:
        print(f"\n{len(disagreements)} DISAGREEMENTS -- each is a bug, a pattern, or a whitelist entry:")
        shown = disagreements if verbose else disagreements[:15]
        for d in shown:
            print(f"  {d['struct']}@{d['offset']:#x} expected {d['expected']} "
                  f"got {d['actual']} ({d['op']}) in {d['func']} @ {d['addr']:#x}")
        if not verbose and len(disagreements) > 15:
            print(f"  ... {len(disagreements) - 15} more (use --verbose)")

    conn.close()
    return 1 if disagreements else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    sys.exit(validate(args.repo.expanduser(), args.db.expanduser(), args.verbose))


if __name__ == "__main__":
    main()
