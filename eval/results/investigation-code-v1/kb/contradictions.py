"""Contradiction sweep over the evidence tier.

Free bug reports sitting in data we already have. Two accesses to the same
storage location that disagree about its width, signedness, or class mean one
of three things: a union, a mis-resolved base, or a bug in the extractor. All
three are worth knowing about.

IDENTITY SCOPE -- this is the subtle part:

    global:0xADDR   is a genuine cross-function identity. Every function that
                    touches it touches the same bytes, so disagreement between
                    two functions is a real signal.

    param0..param3  are function-LOCAL. Function A's param0 and function B's
                    param0 are unrelated until an inference says they share a
                    type. Grouping them globally would manufacture thousands of
                    meaningless contradictions.

    stack           is frame-local, and frames are reused for different
                    variables across their lifetime. Never grouped.

So globals sweep across the whole program; params sweep within a function only.
Cross-function param grouping becomes possible in Phase 3, once signatures
exist in the inference tier -- and at that point a contradiction is evidence
that the *signature* is wrong, which is exactly the poisoned-fact detector.

Run:
    python3 -m kb.contradictions --db kb.sqlite [--verbose]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

# A location is described by the set of accesses that touch it.
GLOBAL_SQL = """
SELECT base, offset, width, signed, class, access, is_load, op, addr, func_addr
FROM evidence
WHERE kind = 'mem_access' AND base LIKE 'global:%' AND width IS NOT NULL
"""

PARAM_SQL = """
SELECT func_addr, base, offset, width, signed, class, access, is_load, op, addr
FROM evidence
WHERE kind = 'mem_access' AND base LIKE 'param%' AND width IS NOT NULL
"""


def _group(rows, key):
    out = defaultdict(list)
    for row in rows:
        out[key(row)].append(row)
    return out


def mark_bulk_copies(rows, min_run=3):
    """Flag word accesses that are part of a struct copy, not a field access.

    IDO compiles a struct assignment into a run of word loads and stores at
    consecutive offsets. Those words say nothing about field widths -- a
    `sw` landing on offset 0x24 during a copy of a 0x20-byte nested struct
    does not make the field there four bytes wide.

    Verified against SBK1: RaceUiPodiumTrailActor.copyBlock is a Transform3D
    at 0x24 whose first member is an s16, copied word-wise at 0x34/0x38/0x3C
    while a genuine `lh` reads 0x24. Without this, that reads as a width
    contradiction on a codebase known to be correct.

    Detection is deterministic and pattern-based: three or more width-4
    accesses in the same direction at stride-4 offsets under one base.
    Returns the set of evidence addrs to exclude from width checks.
    """
    copies = set()
    for _, group in _group(rows, lambda r: (r["func_addr"], r["base"])).items():
        for is_load in (0, 1):
            words = sorted(
                (r for r in group
                 if r["width"] == 4 and r["is_load"] == is_load and r["access"] == "full"),
                key=lambda r: r["offset"],
            )
            run = []
            for acc in words:
                if run and acc["offset"] == run[-1]["offset"] + 4:
                    run.append(acc)
                    continue
                if len(run) >= min_run:
                    copies.update(a["addr"] for a in run)
                run = [acc]
            if len(run) >= min_run:
                copies.update(a["addr"] for a in run)
    return copies


def _check_location(subject, accesses, copies=frozenset()):
    """Return contradiction dicts for one storage location.

    Partial accesses (lwl/lwr/swl/swr) are excluded from width checks: they
    deliberately touch a sub-word of a larger object, so a width disagreement
    with a full access is expected, not a contradiction.

    Signedness disagreement is reported as a NOTE, not a contradiction. A
    signed field read through an unsigned cast is ordinary C -- `lh` and `lhu`
    on one location is a cast, not a type conflict. Treating it as an error
    fires constantly on correct code.
    """
    found = []
    full = [a for a in accesses
            if a["access"] == "full" and a["addr"] not in copies]
    if len(full) < 2:
        return found

    widths = {a["width"] for a in full}
    if len(widths) > 1:
        found.append({
            "subject": subject, "kind": "width",
            "detail": {
                "widths": sorted(widths),
                "ops": sorted({a["op"] for a in full}),
                "evidence_addrs": [hex(a["addr"]) for a in full[:8]],
            },
        })

    # Signedness is only claimed by loads; stores carry NULL and say nothing.
    # Downgraded to a note: this is a cast, not a conflict.
    signs = {a["signed"] for a in full if a["is_load"] and a["signed"] is not None}
    if len(signs) > 1:
        found.append({
            "subject": subject, "kind": "note:signedness",
            "detail": {
                "signed": sorted(signs),
                "ops": sorted({a["op"] for a in full if a["is_load"]}),
                "evidence_addrs": [hex(a["addr"]) for a in full[:8]],
            },
        })

    classes = {a["class"] for a in full}
    if len(classes) > 1:
        found.append({
            "subject": subject, "kind": "class",
            "detail": {
                "classes": sorted(classes),
                "ops": sorted({a["op"] for a in full}),
                "evidence_addrs": [hex(a["addr"]) for a in full[:8]],
            },
        })

    return found


def _check_overlap(prefix, by_offset):
    """Fields that run into each other within one object."""
    found = []
    offsets = sorted(by_offset)
    for i, off in enumerate(offsets):
        widths = {a["width"] for a in by_offset[off] if a["access"] == "full"}
        if not widths:
            continue
        span = max(widths)
        for nxt in offsets[i + 1:]:
            if nxt >= off + span:
                break
            found.append({
                "subject": f"{prefix}@{off:#x}",
                "kind": "overlap",
                "detail": {"field": off, "width": span, "overlaps": nxt},
            })
            break
    return found


def sweep(db_path: Path, verbose: bool = False) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    results = []

    # --- globals: cross-function identity ---------------------------------
    rows = [dict(r) for r in conn.execute(GLOBAL_SQL)]
    copies = mark_bulk_copies(rows)
    by_loc = _group(rows, lambda r: (r["base"], r["offset"]))
    for (base, off), accesses in by_loc.items():
        results.extend(_check_location(f"{base}@{off:#x}", accesses, copies))

    live = [r for r in rows if r["addr"] not in copies]
    by_obj = _group(live, lambda r: r["base"])
    for base, accesses in by_obj.items():
        results.extend(_check_overlap(base, _group(accesses, lambda r: r["offset"])))

    n_global_locs = len(by_loc)
    n_copy = len(copies)

    # --- params: within one function only ---------------------------------
    prows = [dict(r) for r in conn.execute(PARAM_SQL)]
    pcopies = mark_bulk_copies(prows)
    by_fn_loc = _group(prows, lambda r: (r["func_addr"], r["base"], r["offset"]))
    for (fn, base, off), accesses in by_fn_loc.items():
        results.extend(_check_location(f"func:{fn:#x}/{base}@{off:#x}", accesses, pcopies))

    n_param_locs = len(by_fn_loc)
    n_copy += len(pcopies)

    # --- persist ----------------------------------------------------------
    now = int(time.time())
    conn.execute("DELETE FROM contradictions")
    for c in results:
        conn.execute(
            "INSERT OR REPLACE INTO contradictions (subject, kind, detail, found_at)"
            " VALUES (?,?,?,?)",
            (c["subject"], c["kind"], json.dumps(c["detail"]), now),
        )
    conn.commit()

    summary = defaultdict(int)
    for c in results:
        scope = "global" if c["subject"].startswith("global:") else "param"
        summary[f"{scope}/{c['kind']}"] += 1

    hard = [c for c in results if not c["kind"].startswith("note:")]
    notes = [c for c in results if c["kind"].startswith("note:")]

    print(f"locations swept : {n_global_locs} global, {n_param_locs} param (in-function)")
    print(f"bulk-copy words : {n_copy} excluded from width checks")
    print(f"CONTRADICTIONS  : {len(hard)}")
    print(f"notes           : {len(notes)}")
    for k in sorted(summary):
        print(f"  {k:24} {summary[k]}")

    if verbose and hard:
        print("\ncontradictions:")
        for c in hard[:20]:
            print(f"  [{c['kind']:10}] {c['subject']}  {json.dumps(c['detail'])}")

    conn.close()
    return dict(summary)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    sweep(args.db.expanduser(), args.verbose)


if __name__ == "__main__":
    main()
