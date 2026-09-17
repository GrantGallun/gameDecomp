"""Per-MUTATION-FAMILY yield, to test the reference's measured non-lever list on our own corpus.

`solver/regalloc_mutations.py` defines the families the register search enumerates. The reference
project's DECOMPILATION_LEARNINGS.md (commits fd27a480, 5c8d6f2f, 9834bdf5) records that for a
symmetric value-kind colouring residual, declaration order, statement order, nested scopes,
expression grouping, separate carrier variables and source-line layout were all MEASURED INERT. If the
same is true here, the search is buying lottery tickets.

    python3 eval/family_yield.py --families
"""
from __future__ import annotations

import pathlib
import re
import sqlite3
import sys

DB = pathlib.Path.home() / "decomp" / "kb-sbk1.sqlite"
FAMILIES = ("local_type", "commutative", "decl_order", "inline_temp", "stmt_order", "stmt_move",
            "const_inline", "field_local", "self_update", "compound_assign", "typed_index",
            "symbol_scale", "negative_scale", "result_local", "single_use", "typed_reread",
            "truth_test", "load_modify_store", "rotated_loop", "readonly_field_local",
            "store_value_local", "struct_copy", "store_loop", "guard_before_load",
            "const_store_local", "do_restore")


def main() -> int:
    db = pathlib.Path(sys.argv[sys.argv.index("--db") + 1]) if "--db" in sys.argv else DB
    conn = sqlite3.connect(str(db))
    rows = conn.execute("select strategy, compiled, coalesce(exact,0) from attempts").fetchall()
    print(f"{'family':<24} {'attempts':>9} {'compiled':>9} {'exact':>6} {'exact/attempt':>13}")
    seen = []
    for name in FAMILIES:
        pattern = re.compile(rf"(?<![a-z_]){re.escape(name)}(?![a-z_])")
        hits = [r for r in rows if pattern.search(r[0] or "")]
        if not hits:
            continue
        comp = sum(1 for _s, c, _e in hits if c)
        ex = sum(1 for _s, _c, e in hits if e)
        seen.append((name, len(hits), comp, ex))
        print(f"{name:<24} {len(hits):>9} {comp:>9} {ex:>6} {ex / len(hits):>13.5f}")
    print(f"\nfamilies searched: {len(seen)}   total attempts {sum(s[1] for s in seen)}   "
          f"total exact {sum(s[3] for s in seen)}")
    inert = [s[0] for s in seen if s[3] == 0]
    print(f"families with ZERO exact closures in our corpus: {inert}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
