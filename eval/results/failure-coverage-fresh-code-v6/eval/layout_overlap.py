"""Is there cross-function layout evidence to share, or does each diff stand alone?

Every layout repair derives a struct's offsets from ONE function's residual and
throws the result away when the run ends. The same struct is accessed by dozens
of functions, so in principle a field pinned by function A's diff is pinned for
function B before B is ever attempted. The KB was built for exactly this and its
inference tier holds zero rows.

Before building that machinery, measure whether the signal exists. Two ways it
could fail, and they need opposite responses:

  no struct is named by more than one function  -> nothing to share, stop
  several functions name one struct and DISAGREE
      about a field's offset                    -> sharing would inject errors

MEASURED OFFLINE. The attempts table already stores 10,941 residuals, so this
reads history instead of recompiling. That also makes it the first consumer of
that table -- 12,751 attempts have been logged and, until now, nothing has ever
read them back, despite the project's thesis being that the database remembers.

WHAT IS SHAREABLE, AND WHY IT IS EVIDENCE RATHER THAN INFERENCE
    A PRODUCED offset is candidate-specific: it describes whatever struct that
    attempt happened to declare, and means nothing in another function. An
    EXPECTED offset comes from the target binary, so `this struct has a 2-byte
    field at 0x1c` is a fact about the ROM and transfers. Only expected offsets
    and access widths are collected.

    python3 -m eval.layout_overlap
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

from solver import diffrepair


def harvest(conn, limit: int = 0) -> dict:
    """{struct name -> {expected offset -> {width -> [functions]}}}."""
    sql = ("select f.name, a.source_code, a.diff_summary from attempts a"
           " join functions f on f.addr = a.func_addr"
           " where a.diff_summary is not null and length(a.diff_summary) > 50"
           "   and a.source_code is not null and a.compiled = 1")
    if limit:
        sql += f" limit {limit}"

    store: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    seen_pairs = set()
    scanned = attributed = 0

    for name, src, diff in conn.execute(sql):
        scanned += 1
        try:
            regions = diffrepair._struct_regions(src)
            if not regions:
                continue
            sizes = diffrepair.type_sizes(src)
            named = []
            for r in regions:
                m = diffrepair.STRUCT_NAME.match(src, r[1])
                if m:
                    named.append((m.group("name"), r))
            if not named:
                continue
            sets = diffrepair.constraint_sets(diff)
            widths = diffrepair.width_constraints(diff)
        except Exception:                          # noqa: BLE001
            continue

        for _base, mapping in sets:
            for struct_name, region in named:
                try:
                    offs = {o for _m, o, _s in
                            diffrepair.region_fields(src, region, sizes)}
                except Exception:                  # noqa: BLE001
                    continue
                if not offs or not set(mapping).issubset(offs):
                    continue
                attributed += 1
                for produced, expected in mapping.items():
                    w = widths.get(produced, (None, None))[0]
                    key = (name, struct_name, expected)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    store[struct_name][expected][w].add(name)
                break
    return store, scanned, attributed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=120)
    store, scanned, attributed = harvest(conn, args.limit)
    print(f"scanned {scanned} logged residuals; "
          f"{attributed} constraint sets attributed to a named struct\n")

    multi = conflict = single = 0
    rows = []
    for struct_name, offsets in store.items():
        funcs = set()
        conflicts = []
        for off, widths in offsets.items():
            for w, fs in widths.items():
                funcs |= fs
            if len(widths) > 1:
                conflicts.append((off, sorted(w for w in widths)))
        if len(funcs) > 1:
            multi += 1
        else:
            single += 1
        if conflicts:
            conflict += 1
        rows.append((len(funcs), len(offsets), struct_name, conflicts))

    rows.sort(reverse=True)
    print(f"{'struct':<34}{'functions':>10}{'offsets':>9}  conflicts")
    print("-" * 70)
    for nf, no, name, conflicts in rows[:20]:
        c = f"{len(conflicts)} offset(s) with 2+ widths" if conflicts else "-"
        print(f"{name:<34}{nf:>10}{no:>9}  {c}")

    print("-" * 70)
    print(f"structs with evidence from MORE THAN ONE function : {multi}")
    print(f"structs seen in only one function                 : {single}")
    print(f"structs with a width conflict across functions    : {conflict}")
    if multi == 0:
        print("\nNothing to share: no struct is pinned by more than one "
              "function. Cross-function layout would add nothing.")
    elif conflict > multi / 2:
        print("\nMost shared structs DISAGREE across functions. Sharing would "
              "inject errors; find out why they disagree first.")
    else:
        print("\nShareable signal exists. A field pinned by one function's "
              "residual is pinned for the others that declare the same "
              "struct, before those functions are attempted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
